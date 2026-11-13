"""HA-level tests for the Asymmetric Group config and options flows.

These run against a real (test) Home Assistant instance via
pytest-homeassistant-custom-component. They cover the helper creation
flow end to end, group entities as members, the options flow, and
that a created/configured entry sets up and produces its entities.
"""

from __future__ import annotations

import unittest

try:
    from homeassistant import config_entries
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.const import EVENT_CALL_SERVICE
    from homeassistant.core import HomeAssistant, callback
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry
except ImportError:
    # The HA test harness (pytest-homeassistant-custom-component and a
    # matching Home Assistant) is only installed in the HA-tests
    # environment; see requirements_test.txt. Skip cleanly elsewhere
    # (e.g. the engine-only unittest run under a bare Python).
    raise unittest.SkipTest(
        "pytest-homeassistant-custom-component is not installed"
    ) from None

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
from custom_components.asymmetric_group.presets import async_get_preset_store
from tests.test_nesting import _BRIGHTNESS_ATTRS, _member_calls, _track_light_calls

MEMBERS = ["light.bedroom_1", "light.bedroom_2", "light.bedroom_3"]

TOGGLES_OFF = {
    CONF_ASYMMETRIC_POWER: False,
    CONF_ASYMMETRIC_ATTRIBUTES: False,
    CONF_ASYMMETRIC_STATE: False,
    CONF_ENFORCE_POWER_SCOPE: False,
    CONF_HIDE_MEMBERS: False,
    CONF_ALL_ENTITIES: False,
}


def _entry_data(**overrides):
    data = {
        CONF_NAME: "Test Group",
        CONF_BASE_MEMBERS: list(MEMBERS),
        CONF_POWER_ON_MEMBERS: list(MEMBERS),
        CONF_POWER_OFF_MEMBERS: list(MEMBERS),
        CONF_ATTRIBUTE_MEMBERS: list(MEMBERS),
        CONF_STATE_MEMBERS: list(MEMBERS),
        **TOGGLES_OFF,
    }
    data.update(overrides)
    return data


async def test_flow_creates_entry_with_all_dimensions_off(
    hass: HomeAssistant,
) -> None:
    """User -> sets (all off) creates the entry directly."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Test Group", CONF_BASE_MEMBERS: MEMBERS},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "sets"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], TOGGLES_OFF
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Group"
    assert result["data"][CONF_BASE_MEMBERS] == MEMBERS
    # Subgroups default to the full base set for disabled dimensions.
    assert result["data"][CONF_POWER_ON_MEMBERS] == MEMBERS
    assert result["data"][CONF_POWER_OFF_MEMBERS] == MEMBERS

    await hass.async_block_till_done()
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].state == ConfigEntryState.LOADED


async def test_flow_power_step_shown_only_when_asymmetric_power_on(
    hass: HomeAssistant,
) -> None:
    """Enabling Asymmetric power routes through the power step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Power Group", CONF_BASE_MEMBERS: MEMBERS},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**TOGGLES_OFF, CONF_ASYMMETRIC_POWER: True}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "power"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_POWER_ON_MEMBERS: ["light.bedroom_1"],
            CONF_POWER_OFF_MEMBERS: MEMBERS,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ASYMMETRIC_POWER] is True
    assert result["data"][CONF_POWER_ON_MEMBERS] == ["light.bedroom_1"]
    assert result["data"][CONF_POWER_OFF_MEMBERS] == MEMBERS


async def test_flow_accepts_group_entities_as_members(
    hass: HomeAssistant,
) -> None:
    """A member that is itself a group is accepted (nested groups).

    Group members stay atomic, exactly as in core groups; only a
    membership cycle is rejected (see tests/test_nesting.py).
    """
    hass.states.async_set(
        "light.some_group", "on", {"entity_id": ["light.bedroom_1"]}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Nested Group", CONF_BASE_MEMBERS: ["light.some_group"]},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "sets"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], TOGGLES_OFF
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BASE_MEMBERS] == ["light.some_group"]
    # Subgroups default to the full base set, group member included.
    assert result["data"][CONF_POWER_ON_MEMBERS] == ["light.some_group"]


async def test_options_flow_members_round_trip(hass: HomeAssistant) -> None:
    """Options flow opens as a menu and saves member settings."""
    entry = MockConfigEntry(domain=DOMAIN, title="Test Group", data=_entry_data())
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "members"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "members"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TOGGLES_OFF, CONF_ASYMMETRIC_STATE: True}
    )
    # Asymmetric state on -> the state member step is shown.
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "members_state"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_STATE_MEMBERS: ["light.bedroom_2"]}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ASYMMETRIC_STATE] is True
    assert result["data"][CONF_STATE_MEMBERS] == ["light.bedroom_2"]
    assert result["data"][CONF_POWER_ON_MEMBERS] == MEMBERS

    await hass.async_block_till_done()
    assert entry.options[CONF_STATE_MEMBERS] == ["light.bedroom_2"]
    assert entry.state == ConfigEntryState.LOADED


async def test_options_preset_picker_shows_human_label_for_new_preset(
    hass: HomeAssistant,
) -> None:
    """The preset picker labels the new-preset option, never the raw sentinel."""
    entry = MockConfigEntry(domain=DOMAIN, title="Test Group", data=_entry_data())
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Seed one existing preset: it must appear under its own name.
    store = await async_get_preset_store(hass)
    await store.async_save_preset("Evening", {})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "presets"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "presets_pick"

    selector = next(iter(result["data_schema"].schema.values()))
    options = selector.config["options"]
    labels = [
        opt["label"] if isinstance(opt, dict) else opt for opt in options
    ]
    values = [
        opt["value"] if isinstance(opt, dict) else opt for opt in options
    ]
    # The internal sentinel stays the submitted value, but is never shown.
    assert "__new__" in values
    assert "__new__" not in labels
    assert "+ Create new preset" in labels
    # An existing preset is offered under its own name, as value and label.
    assert "Evening" in values
    assert "Evening" in labels

    # Choosing the sentinel still opens the new-preset editor.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"preset": "__new__"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "presets_edit"


async def test_setup_entry_produces_group_light_and_switches(
    hass: HomeAssistant,
) -> None:
    """A configured entry sets up and produces the group entities."""
    hass.states.async_set(
        "light.bedroom_1",
        "on",
        {"supported_color_modes": ["onoff"], "color_mode": "onoff"},
    )
    hass.states.async_set(
        "light.bedroom_2",
        "off",
        {"supported_color_modes": ["onoff"], "color_mode": "onoff"},
    )
    hass.states.async_set(
        "light.bedroom_3",
        "off",
        {"supported_color_modes": ["onoff"], "color_mode": "onoff"},
    )

    entry = MockConfigEntry(domain=DOMAIN, title="Test Group", data=_entry_data())
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.LOADED

    group = hass.states.get("light.test_group")
    assert group is not None
    assert group.state == "on"
    # The entity_id attribute always reports the full base set.
    assert group.attributes["entity_id"] == MEMBERS

    for suffix in (
        "asymmetric_power",
        "asymmetric_attributes",
        "asymmetric_state",
        "enforce_power_scope",
    ):
        switch = hass.states.get(f"switch.test_group_{suffix}")
        assert switch is not None, suffix
        assert switch.state == "off"


async def test_group_reports_member_color_temp_range_in_kelvin(
    hass: HomeAssistant,
) -> None:
    """The group aggregates member min/max colour temperature in kelvin.

    Regression test: the light overrode color_temp_kelvin but not
    min/max_color_temp_kelvin, so HA's base LightEntity fell back to
    the legacy mireds defaults and warned on state writes (core PR
    #79591). The range follows core's group light: lowest member
    minimum / highest member maximum over the whole attribute scope,
    including members that are off.
    """
    hass.states.async_set(
        "light.bedroom_1",
        "on",
        {
            "supported_color_modes": ["color_temp"],
            "color_mode": "color_temp",
            "color_temp_kelvin": 3000,
            "min_color_temp_kelvin": 2200,
            "max_color_temp_kelvin": 6500,
        },
    )
    hass.states.async_set(
        "light.bedroom_2",
        "off",
        {
            "supported_color_modes": ["color_temp"],
            "color_mode": "color_temp",
            "min_color_temp_kelvin": 2700,
            "max_color_temp_kelvin": 6000,
        },
    )
    hass.states.async_set(
        "light.bedroom_3",
        "off",
        {
            "supported_color_modes": ["color_temp"],
            "color_mode": "color_temp",
            "min_color_temp_kelvin": 2000,
            "max_color_temp_kelvin": 7000,
        },
    )

    entry = MockConfigEntry(domain=DOMAIN, title="Test Group", data=_entry_data())
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.LOADED

    group = hass.states.get("light.test_group")
    assert group is not None
    assert group.attributes["min_color_temp_kelvin"] == 2000
    assert group.attributes["max_color_temp_kelvin"] == 7000


# ----------------------------------------------------------------------
# Base-member editing in the options flow (core-group parity).
async def _setup_entry(hass: HomeAssistant, data: dict) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=data[CONF_NAME], data=data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _open_members_step(hass: HomeAssistant, entry: MockConfigEntry):
    """Open Configure -> members and return the members form result."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "members"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "members"
    return result


async def test_options_add_base_member(hass: HomeAssistant) -> None:
    """Adding a base member in Configure saves it and reloads."""
    entry = await _setup_entry(hass, _entry_data())
    result = await _open_members_step(hass, entry)
    new_base = [*MEMBERS, "light.bedroom_4"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TOGGLES_OFF, CONF_BASE_MEMBERS: new_base}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BASE_MEMBERS] == new_base

    await hass.async_block_till_done()
    assert entry.options[CONF_BASE_MEMBERS] == new_base
    assert entry.state == ConfigEntryState.LOADED
    group = hass.states.get("light.test_group")
    assert group is not None
    # Same group entity, now reporting the widened base set.
    assert group.attributes["entity_id"] == new_base


async def test_options_remove_base_member_filters_subgroups(
    hass: HomeAssistant,
) -> None:
    """Removing a base member drops it from every subgroup on save."""
    entry = await _setup_entry(
        hass,
        _entry_data(
            **{
                CONF_ASYMMETRIC_POWER: True,
                CONF_POWER_ON_MEMBERS: ["light.bedroom_1"],
            }
        ),
    )
    kept = ["light.bedroom_1", "light.bedroom_2"]
    result = await _open_members_step(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **TOGGLES_OFF,
            CONF_ASYMMETRIC_POWER: True,
            CONF_BASE_MEMBERS: kept,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "members_power"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_POWER_ON_MEMBERS: ["light.bedroom_1"],
            CONF_POWER_OFF_MEMBERS: MEMBERS,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_POWER_ON_MEMBERS] == ["light.bedroom_1"]
    assert result["data"][CONF_POWER_OFF_MEMBERS] == kept
    assert result["data"][CONF_ATTRIBUTE_MEMBERS] == kept
    assert result["data"][CONF_STATE_MEMBERS] == kept

    await hass.async_block_till_done()
    group = hass.states.get("light.test_group")
    assert group is not None
    assert group.attributes["entity_id"] == kept
    assert group.attributes["effective_power_on_members"] == ["light.bedroom_1"]


async def test_options_removed_member_hidden_by_group_is_unhidden(
    hass: HomeAssistant,
) -> None:
    """A base edit un-hides a removed member the group had hidden.

    A member hidden by the user is never touched, and a member
    still in the base stays hidden by the group.
    """
    registry = er.async_get(hass)
    kept = registry.async_get_or_create(
        "light", "demo", "base_kept_uid", suggested_object_id="base_kept"
    )
    removed = registry.async_get_or_create(
        "light", "demo", "base_removed_uid", suggested_object_id="base_removed"
    )
    user_hidden = registry.async_get_or_create(
        "light", "demo", "base_user_uid", suggested_object_id="base_user"
    )
    members = [kept.entity_id, removed.entity_id, user_hidden.entity_id]
    registry.async_update_entity(
        user_hidden.entity_id, hidden_by=er.RegistryEntryHider.USER
    )
    entry = await _setup_entry(
        hass,
        _entry_data(
            **{
                CONF_NAME: "Hide Group",
                CONF_BASE_MEMBERS: members,
                CONF_POWER_ON_MEMBERS: members,
                CONF_POWER_OFF_MEMBERS: members,
                CONF_ATTRIBUTE_MEMBERS: members,
                CONF_STATE_MEMBERS: members,
                CONF_HIDE_MEMBERS: True,
            }
        ),
    )
    # Setup hid the two members that were not already hidden.
    assert (
        registry.async_get(kept.entity_id).hidden_by
        == er.RegistryEntryHider.INTEGRATION
    )
    assert (
        registry.async_get(removed.entity_id).hidden_by
        == er.RegistryEntryHider.INTEGRATION
    )
    assert (
        registry.async_get(user_hidden.entity_id).hidden_by
        == er.RegistryEntryHider.USER
    )

    result = await _open_members_step(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **TOGGLES_OFF,
            CONF_HIDE_MEMBERS: True,
            CONF_BASE_MEMBERS: [kept.entity_id],
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    # The removed member we hid is shown again...
    assert registry.async_get(removed.entity_id).hidden_by is None
    # ...the user-hidden member is exactly as the user left it...
    assert (
        registry.async_get(user_hidden.entity_id).hidden_by
        == er.RegistryEntryHider.USER
    )
    # ...and the kept member is still hidden by the group.
    assert (
        registry.async_get(kept.entity_id).hidden_by
        == er.RegistryEntryHider.INTEGRATION
    )


async def test_options_base_edit_creating_cycle_is_rejected(
    hass: HomeAssistant,
) -> None:
    """Adding the group itself to its own base is refused, unsaved."""
    entry = await _setup_entry(hass, _entry_data())
    result = await _open_members_step(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**TOGGLES_OFF, CONF_BASE_MEMBERS: [*MEMBERS, "light.test_group"]},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "members"
    assert result["errors"] == {CONF_BASE_MEMBERS: "group_member_cycle"}
    assert entry.options == {}


async def test_options_base_change_rebases_tracking_sets_only(
    hass: HomeAssistant,
) -> None:
    """Sets still listing the whole old base follow it to the new
    base; narrowed sets keep exactly their own membership."""
    entry = await _setup_entry(
        hass,
        _entry_data(
            **{
                CONF_POWER_ON_MEMBERS: ["light.bedroom_1", "light.bedroom_2"],
                CONF_STATE_MEMBERS: ["light.bedroom_1"],
            }
        ),
    )
    new_base = ["light.bedroom_1", "light.bedroom_2", "light.bedroom_4"]
    result = await _open_members_step(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TOGGLES_OFF, CONF_BASE_MEMBERS: new_base}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    # Tracking sets (they listed the whole old base) follow...
    assert data[CONF_POWER_OFF_MEMBERS] == new_base
    assert data[CONF_ATTRIBUTE_MEMBERS] == new_base
    # ...narrowed sets are only filtered (bedroom_4 not added).
    assert data[CONF_POWER_ON_MEMBERS] == ["light.bedroom_1", "light.bedroom_2"]
    assert data[CONF_STATE_MEMBERS] == ["light.bedroom_1"]


async def test_options_added_member_joins_tracking_sets_only(
    hass: HomeAssistant,
) -> None:
    """Runtime view: an added member is in the tracking Power-OFF
    set after the edit but stays out of the curated Power-ON set."""
    for member in [*MEMBERS, "light.bedroom_4"]:
        hass.states.async_set(member, "off", dict(_BRIGHTNESS_ATTRS))
    entry = await _setup_entry(
        hass,
        _entry_data(
            **{
                CONF_ASYMMETRIC_POWER: True,
                CONF_POWER_ON_MEMBERS: ["light.bedroom_1"],
            }
        ),
    )
    new_base = [*MEMBERS, "light.bedroom_4"]
    result = await _open_members_step(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **TOGGLES_OFF,
            CONF_ASYMMETRIC_POWER: True,
            CONF_BASE_MEMBERS: new_base,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "members_power"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_POWER_ON_MEMBERS: ["light.bedroom_1"],
            CONF_POWER_OFF_MEMBERS: new_base,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    group = hass.states.get("light.test_group")
    assert group is not None
    assert group.attributes["entity_id"] == new_base
    assert group.attributes["effective_power_on_members"] == ["light.bedroom_1"]
    assert group.attributes["effective_power_off_members"] == new_base


async def test_options_base_edit_does_not_enforce_but_set_members_does(
    hass: HomeAssistant,
) -> None:
    """A Configure base edit never switches lights (it reseeds and
    reloads, R54-style); the same narrowing via set_members with
    Enforce on turns the dropped lit member off at once."""
    for member in MEMBERS:
        state = "off" if member == "light.bedroom_3" else "on"
        hass.states.async_set(member, state, dict(_BRIGHTNESS_ATTRS))
    entry = await _setup_entry(
        hass,
        _entry_data(
            **{
                CONF_ASYMMETRIC_POWER: True,
                CONF_ENFORCE_POWER_SCOPE: True,
                CONF_POWER_ON_MEMBERS: ["light.bedroom_1", "light.bedroom_2"],
            }
        ),
    )
    recorded = _track_light_calls(hass)

    # Remove the lit bedroom_2 from the base in Configure.
    result = await _open_members_step(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **TOGGLES_OFF,
            CONF_ASYMMETRIC_POWER: True,
            CONF_ENFORCE_POWER_SCOPE: True,
            CONF_BASE_MEMBERS: ["light.bedroom_1", "light.bedroom_3"],
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "members_power"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_POWER_ON_MEMBERS: ["light.bedroom_1", "light.bedroom_2"],
            CONF_POWER_OFF_MEMBERS: ["light.bedroom_1", "light.bedroom_3"],
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    # The saved Power-ON set was filtered to the new base, but no
    # light call was issued for the removed member; it is still on.
    assert entry.options[CONF_POWER_ON_MEMBERS] == ["light.bedroom_1"]
    assert _member_calls(recorded, {"light.bedroom_2"}) == []
    assert hass.states.get("light.bedroom_2").state == "on"

    # Control: a second group where set_members narrows the Power-ON
    # set away from a lit member — enforcement turns it off at once.
    ctrl = ["light.ctrl_1", "light.ctrl_2"]
    for member in ctrl:
        hass.states.async_set(member, "on", dict(_BRIGHTNESS_ATTRS))
    await _setup_entry(
        hass,
        _entry_data(
            **{
                CONF_NAME: "Control Group",
                CONF_BASE_MEMBERS: ctrl,
                CONF_POWER_ON_MEMBERS: ctrl,
                CONF_POWER_OFF_MEMBERS: ctrl,
                CONF_ATTRIBUTE_MEMBERS: ctrl,
                CONF_STATE_MEMBERS: ctrl,
                CONF_ASYMMETRIC_POWER: True,
                CONF_ENFORCE_POWER_SCOPE: True,
            }
        ),
    )

    @callback
    def _respond(event) -> None:
        # The control light answers its turn_off as it is issued, so
        # enforcement's settle check sees the new state immediately.
        if event.data.get("domain") != "light":
            return
        if event.data.get("service") != "turn_off":
            return
        data = event.data.get("service_data") or {}
        raw = data.get("entity_id") or []
        targets = [raw] if isinstance(raw, str) else list(raw)
        if "light.ctrl_2" in targets:
            hass.states.async_set(
                "light.ctrl_2", "off", dict(_BRIGHTNESS_ATTRS)
            )

    hass.bus.async_listen(EVENT_CALL_SERVICE, _respond)
    await hass.services.async_call(
        DOMAIN,
        "set_members",
        {
            "entity_id": "light.control_group",
            CONF_POWER_ON_MEMBERS: ["light.ctrl_1"],
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    calls = _member_calls(recorded, {"light.ctrl_2"})
    assert any(call["service"] == "turn_off" for call in calls)


async def test_options_empty_base_is_rejected(hass: HomeAssistant) -> None:
    """The base set cannot be emptied in Configure."""
    entry = await _setup_entry(hass, _entry_data())
    result = await _open_members_step(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**TOGGLES_OFF, CONF_BASE_MEMBERS: []}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "members"
    assert result["errors"] == {CONF_BASE_MEMBERS: "base_members_required"}
    assert entry.options == {}
