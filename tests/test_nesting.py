"""HA-level tests for nested groups (a group as a member).

A group member stays atomic, exactly as in core groups: it occupies
the Power/Attribute/State sets as one unit, service calls target the
subgroup entity itself, and the outer group reads the subgroup's own
aggregated state as a single member. The only rejected configuration
is a membership cycle — the group containing itself, directly or
transitively.

Covered here:
  (a) a (simulated) core group as a member: turn-on/off and attribute
      calls target the subgroup entity; the outer state/brightness
      treat the subgroup's aggregated values as one member;
  (b) an asymmetric group inside an asymmetric group, with the inner
      group alone in the outer Power-ON set;
  (c) Enforce Power Scope settling on a subgroup member;
  (d) cycle rejection in the config-flow validation helper and in
      the flow itself (direct and indirect);
  (e) regression: member sets and presets containing a group id
      round-trip unchanged.
"""

from __future__ import annotations

import time
import unittest

try:
    from homeassistant import config_entries
    from homeassistant.const import EVENT_CALL_SERVICE
    from homeassistant.core import HomeAssistant, callback
    from homeassistant.data_entry_flow import FlowResultType
    from pytest_homeassistant_custom_component.common import MockConfigEntry
except ImportError:
    # See tests/test_config_flow.py: skip cleanly without the harness.
    raise unittest.SkipTest(
        "pytest-homeassistant-custom-component is not installed"
    ) from None

from custom_components.asymmetric_group import engine
from custom_components.asymmetric_group.config_flow import _creates_cycle
from custom_components.asymmetric_group.const import (
    CONF_ALL_ENTITIES,
    CONF_ASYMMETRIC_ATTRIBUTES,
    CONF_ASYMMETRIC_POWER,
    CONF_ASYMMETRIC_STATE,
    CONF_ATTRIBUTE_MEMBERS,
    CONF_BASE_MEMBERS,
    CONF_ENFORCE_POWER_SCOPE,
    CONF_HIDE_MEMBERS,
    CONF_NAME,
    CONF_POWER_OFF_MEMBERS,
    CONF_POWER_ON_MEMBERS,
    CONF_STATE_MEMBERS,
    DOMAIN,
)

TOGGLES_OFF = {
    CONF_ASYMMETRIC_POWER: False,
    CONF_ASYMMETRIC_ATTRIBUTES: False,
    CONF_ASYMMETRIC_STATE: False,
    CONF_ENFORCE_POWER_SCOPE: False,
    CONF_HIDE_MEMBERS: False,
    CONF_ALL_ENTITIES: False,
}

_BRIGHTNESS_ATTRS = {
    "supported_color_modes": ["brightness"],
    "color_mode": "brightness",
}


def _entry_data(name, members, **overrides):
    data = {
        CONF_NAME: name,
        CONF_BASE_MEMBERS: list(members),
        CONF_POWER_ON_MEMBERS: list(members),
        CONF_POWER_OFF_MEMBERS: list(members),
        CONF_ATTRIBUTE_MEMBERS: list(members),
        CONF_STATE_MEMBERS: list(members),
        **TOGGLES_OFF,
    }
    data.update(overrides)
    return data


async def _setup_entry(hass: HomeAssistant, data: dict) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=data[CONF_NAME], data=data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _track_light_calls(hass) -> list[dict]:
    """Record every light turn_on/turn_off call via call_service events.

    Returns a list of {"service": ..., "data": service_data} entries,
    including calls made to the group entity itself; filter by target
    to see only a group's fan-out to its members.
    """
    recorded: list[dict] = []

    @callback
    def _record(event) -> None:
        if event.data.get("domain") != "light":
            return
        if event.data.get("service") not in ("turn_on", "turn_off"):
            return
        recorded.append(
            {
                "service": event.data["service"],
                "data": dict(event.data.get("service_data") or {}),
            }
        )

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)
    return recorded


def _member_calls(recorded: list[dict], member_ids) -> list[dict]:
    """Only the recorded calls whose targets are all members."""
    members = set(member_ids)
    out = []
    for call in recorded:
        raw = call["data"].get("entity_id") or []
        targets = [raw] if isinstance(raw, str) else list(raw)
        if targets and set(targets) <= members:
            out.append(call)
    return out


# ----------------------------------------------------------------------
# (a) Core group as a member (simulated via its state, the only shape
# the integration ever sees of a core group).
async def test_core_group_member_is_atomic(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "light.sub_group",
        "off",
        {**_BRIGHTNESS_ATTRS, "brightness": 200,
         "entity_id": ["light.leaf_1", "light.leaf_2"]},
    )
    hass.states.async_set(
        "light.leaf_3", "off", {**_BRIGHTNESS_ATTRS, "brightness": 100}
    )
    await _setup_entry(
        hass, _entry_data("Outer Group", ["light.sub_group", "light.leaf_3"])
    )

    group = hass.states.get("light.outer_group")
    assert group is not None
    assert group.state == "off"
    # Direct base members only (native behaviour): the subgroup is
    # listed, its leaves are not.
    assert group.attributes["entity_id"] == ["light.sub_group", "light.leaf_3"]

    # Subgroup on: the outer group follows its aggregated state.
    hass.states.async_set(
        "light.sub_group",
        "on",
        {**_BRIGHTNESS_ATTRS, "brightness": 200,
         "entity_id": ["light.leaf_1", "light.leaf_2"]},
    )
    await hass.async_block_till_done()
    group = hass.states.get("light.outer_group")
    assert group.state == "on"
    assert group.attributes["brightness"] == 200

    # A second member on: brightness averages per *member* (the
    # subgroup counts once with its own aggregated 200), not per leaf.
    hass.states.async_set(
        "light.leaf_3", "on", {**_BRIGHTNESS_ATTRS, "brightness": 100}
    )
    await hass.async_block_till_done()
    group = hass.states.get("light.outer_group")
    assert group.attributes["brightness"] == 150

    # Turn everything off, then turn the outer group on with
    # attributes: the call must target the subgroup entity itself,
    # never its leaves.
    hass.states.async_set(
        "light.sub_group",
        "off",
        {**_BRIGHTNESS_ATTRS, "brightness": 200,
         "entity_id": ["light.leaf_1", "light.leaf_2"]},
    )
    hass.states.async_set(
        "light.leaf_3", "off", {**_BRIGHTNESS_ATTRS, "brightness": 100}
    )
    await hass.async_block_till_done()

    recorded = _track_light_calls(hass)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.outer_group", "brightness": 128},
        blocking=True,
    )
    await hass.services.async_call(
        "light",
        "turn_off",
        {"entity_id": "light.outer_group"},
        blocking=True,
    )
    await hass.async_block_till_done()
    calls = _member_calls(recorded, {"light.sub_group", "light.leaf_3"})
    assert calls[0]["data"]["entity_id"] == ["light.sub_group", "light.leaf_3"]
    assert calls[0]["data"]["brightness"] == 128
    assert calls[1]["data"]["entity_id"] == ["light.sub_group", "light.leaf_3"]
    for call in calls:
        assert "light.leaf_1" not in call["data"]["entity_id"]
        assert "light.leaf_2" not in call["data"]["entity_id"]


# ----------------------------------------------------------------------
# (b) Asymmetric group inside an asymmetric group.
async def test_asymmetric_group_inside_asymmetric_group(
    hass: HomeAssistant,
) -> None:
    for leaf in ("light.i1", "light.i2", "light.o1"):
        hass.states.async_set(leaf, "off", dict(_BRIGHTNESS_ATTRS))

    await _setup_entry(hass, _entry_data("Inner Group", ["light.i1", "light.i2"]))
    await _setup_entry(
        hass,
        _entry_data(
            "Outer Group",
            ["light.inner_group", "light.o1"],
            **{
                CONF_ASYMMETRIC_POWER: True,
                CONF_POWER_ON_MEMBERS: ["light.inner_group"],
            },
        ),
    )

    group = hass.states.get("light.outer_group")
    assert group is not None
    # The entity_id attribute lists direct members; the Power-ON set
    # resolved to the subgroup alone.
    assert group.attributes["entity_id"] == ["light.inner_group", "light.o1"]
    assert group.attributes["effective_power_on_members"] == ["light.inner_group"]

    # Lighting a leaf of the inner group lights the inner group, and
    # with it the outer group (the subgroup's state counts as one
    # member of the outer State set).
    hass.states.async_set("light.i1", "on", dict(_BRIGHTNESS_ATTRS))
    await hass.async_block_till_done()
    assert hass.states.get("light.inner_group").state == "on"
    assert hass.states.get("light.outer_group").state == "on"

    recorded = _track_light_calls(hass)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.outer_group"},
        blocking=True,
    )
    await hass.services.async_call(
        "light",
        "turn_off",
        {"entity_id": "light.outer_group"},
        blocking=True,
    )
    await hass.async_block_till_done()
    calls = _member_calls(recorded, {"light.inner_group", "light.o1"})
    # Power on targets the subgroup alone (the asymmetric Power-ON
    # set); power off sweeps the whole base, subgroup included.
    assert calls[0]["data"]["entity_id"] == ["light.inner_group"]
    assert calls[1]["data"]["entity_id"] == ["light.inner_group", "light.o1"]


# ----------------------------------------------------------------------
# (c) Enforce Power Scope settle with a subgroup member.
async def test_enforce_settle_completes_with_subgroup_member(
    hass: HomeAssistant,
) -> None:
    sub_attrs = {"entity_id": ["light.leaf_a", "light.leaf_b"]}
    hass.states.async_set("light.sub_group", "off", dict(sub_attrs))
    hass.states.async_set("light.leaf_1", "on", {})
    await _setup_entry(
        hass,
        _entry_data(
            "Enforce Group",
            ["light.sub_group", "light.leaf_1"],
            **{
                CONF_ASYMMETRIC_POWER: True,
                CONF_ENFORCE_POWER_SCOPE: True,
                CONF_POWER_ON_MEMBERS: ["light.leaf_1"],
            },
        ),
    )

    recorded = _track_light_calls(hass)

    @callback
    def _respond(event) -> None:
        # The subgroup "device" answers its command as it is issued,
        # so by the time enforcement checks, its new state is already
        # registered. (No timers: the test harness freezes loop time,
        # so a delayed answer would never land.)
        if event.data.get("domain") != "light":
            return
        if event.data.get("service") != "turn_on":
            return
        data = event.data.get("service_data") or {}
        raw = data.get("entity_id") or []
        targets = [raw] if isinstance(raw, str) else list(raw)
        if "light.sub_group" in targets:
            hass.states.async_set("light.sub_group", "on", dict(sub_attrs))

    hass.bus.async_listen(EVENT_CALL_SERVICE, _respond)

    started = time.monotonic()
    await hass.services.async_call(
        DOMAIN,
        "set_members",
        {
            "entity_id": "light.enforce_group",
            CONF_POWER_ON_MEMBERS: ["light.sub_group", "light.leaf_1"],
        },
        blocking=True,
    )
    elapsed = time.monotonic() - started

    # The subgroup entered the Power-ON set while the old scope was
    # lit, so enforcement struck it — as one entity.
    calls = _member_calls(recorded, {"light.sub_group", "light.leaf_1"})
    assert any(
        "light.sub_group" in (call["data"].get("entity_id") or [])
        for call in calls
    )
    # ...and the scope change only returned once the subgroup had
    # registered its new state, well inside the settle grace period.
    assert hass.states.get("light.sub_group").state == "on"
    assert elapsed < 2.0


# ----------------------------------------------------------------------
# (d) Cycle rejection.
async def test_creates_cycle_helper(hass: HomeAssistant) -> None:
    # Direct self-member (the group's own state need not exist).
    assert _creates_cycle(hass, "light.a", ["light.a"]) is True

    # Indirect A -> B -> A.
    hass.states.async_set("light.b", "on", {"entity_id": ["light.a"]})
    assert _creates_cycle(hass, "light.a", ["light.b"]) is True

    # Longer chain A -> B -> C -> A.
    hass.states.async_set("light.b", "on", {"entity_id": ["light.c"]})
    hass.states.async_set("light.c", "on", {"entity_id": ["light.a"]})
    assert _creates_cycle(hass, "light.a", ["light.b"]) is True

    # Plain nesting is not a cycle.
    hass.states.async_set("light.c", "on", {"entity_id": ["light.x", "light.y"]})
    assert _creates_cycle(hass, "light.a", ["light.b"]) is False

    # A member with no state is a leaf.
    assert _creates_cycle(hass, "light.a", ["light.missing"]) is False

    # A pre-existing loop among other groups neither hangs nor
    # counts as this group's cycle.
    hass.states.async_set("light.b", "on", {"entity_id": ["light.c"]})
    hass.states.async_set("light.c", "on", {"entity_id": ["light.b"]})
    assert _creates_cycle(hass, "light.a", ["light.b"]) is False


async def test_flow_rejects_direct_cycle(hass: HomeAssistant) -> None:
    """Naming the group after a member group = direct self-membership."""
    hass.states.async_set(
        "light.some_group", "on", {"entity_id": ["light.bedroom_1"]}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Some Group", CONF_BASE_MEMBERS: ["light.some_group"]},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_BASE_MEMBERS: "group_member_cycle"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_flow_rejects_indirect_cycle(hass: HomeAssistant) -> None:
    """Group A may not take a member whose membership loops back to A."""
    hass.states.async_set("light.b", "on", {"entity_id": ["light.a"]})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "A", CONF_BASE_MEMBERS: ["light.b"]},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_BASE_MEMBERS: "group_member_cycle"}
    assert hass.config_entries.async_entries(DOMAIN) == []


# ----------------------------------------------------------------------
# (e) Regression: sets/presets containing a group id round-trip.
def test_engine_round_trips_group_member_ids() -> None:
    base = ["light.sub_group", "light.leaf"]
    config = {
        "base_members": base,
        "power_on_members": ["light.sub_group"],
        "power_off_members": list(base),
        "attribute_members": ["light.sub_group"],
        "state_members": list(base),
        "hide_members": False,
        "all_entities": False,
    }
    sets = engine.resolve_sets(
        config, {"power": True, "attributes": True, "state": True}
    )
    assert sets["power_on"] == ["light.sub_group"]
    assert sets["attributes"] == ["light.sub_group"]
    assert sets["state"] == base

    state = {
        "toggles": {"power": False, "attributes": False, "state": False},
        "subgroups": {
            "power_on_members": list(base),
            "power_off_members": list(base),
            "attribute_members": list(base),
            "state_members": list(base),
        },
        "last_applied_preset": None,
    }
    new = engine.apply_sparse_preset(
        state, {"power_on_members": ["light.sub_group"]}, base
    )
    assert new["subgroups"]["power_on_members"] == ["light.sub_group"]
    assert new["subgroups"]["state_members"] == base


async def test_preset_services_round_trip_group_member(
    hass: HomeAssistant,
) -> None:
    await _setup_entry(
        hass,
        _entry_data(
            "Preset Group",
            ["light.sub_group", "light.leaf_1"],
            **{
                CONF_ASYMMETRIC_POWER: True,
                CONF_POWER_ON_MEMBERS: ["light.sub_group"],
            },
        ),
    )
    hass.states.async_set("light.sub_group", "off", {"entity_id": ["light.x"]})
    await hass.services.async_call(
        DOMAIN,
        "save_preset",
        {"entity_id": "light.preset_group", "preset": "Nested"},
        blocking=True,
    )
    response = await hass.services.async_call(
        DOMAIN,
        "list_presets",
        {},
        blocking=True,
        return_response=True,
    )
    saved = response["presets"]["Nested"]
    assert saved["power_on_members"] == ["light.sub_group"]
    assert saved["power_off_members"] == ["light.sub_group", "light.leaf_1"]
