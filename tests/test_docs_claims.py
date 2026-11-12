"""HA-level tests backing specific documentation claims.

Each test names the claim it backs (README / CHANGELOG wording).
These claims were traced to code during the 2026-11-12 docs
validation pass; the tests exist so the claims stay backed by both
code and a running test, not code alone.
"""

from __future__ import annotations

import unittest

try:
    from homeassistant.core import HomeAssistant
    from homeassistant.exceptions import ServiceValidationError
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry
except ImportError:
    # See tests/test_config_flow.py: skip cleanly without the harness.
    raise unittest.SkipTest(
        "pytest-homeassistant-custom-component is not installed"
    ) from None

import pytest

from custom_components.asymmetric_group.const import (
    CONF_ASYMMETRIC_ATTRIBUTES,
    CONF_ASYMMETRIC_POWER,
    CONF_ASYMMETRIC_STATE,
    CONF_ATTRIBUTE_MEMBERS,
    CONF_BASE_MEMBERS,
    CONF_ENFORCE_POWER_SCOPE,
    CONF_HIDE_MEMBERS,
    CONF_POWER_OFF_MEMBERS,
    CONF_POWER_ON_MEMBERS,
    CONF_STATE_MEMBERS,
    DOMAIN,
)
from tests.test_config_flow import MEMBERS, _entry_data
from tests.test_nesting import _BRIGHTNESS_ATTRS, _member_calls, _track_light_calls


async def _setup(hass: HomeAssistant, data: dict) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=data["name"], data=data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_displayed_attributes_come_from_attribute_set_not_state_set(
    hass: HomeAssistant,
) -> None:
    """README: the State set decides on/off only; the reported
    brightness/colour come from the on Attribute members, falling
    back to lit base members when the Attribute scope is dark."""
    await _setup(
        hass,
        _entry_data(
            **{
                CONF_ASYMMETRIC_ATTRIBUTES: True,
                CONF_ATTRIBUTE_MEMBERS: ["light.bedroom_1"],
                CONF_ASYMMETRIC_STATE: True,
                CONF_STATE_MEMBERS: ["light.bedroom_2"],
            }
        ),
    )
    ct_attrs = {
        "supported_color_modes": ["color_temp"],
        "color_mode": "color_temp",
    }
    hass.states.async_set(
        "light.bedroom_1",
        "on",
        {**ct_attrs, "brightness": 100, "color_temp_kelvin": 3000},
    )
    hass.states.async_set(
        "light.bedroom_2",
        "on",
        {**ct_attrs, "brightness": 240, "color_temp_kelvin": 5000},
    )
    hass.states.async_set("light.bedroom_3", "off", dict(_BRIGHTNESS_ATTRS))
    await hass.async_block_till_done()

    group = hass.states.get("light.test_group")
    assert group is not None
    # On because the State member is on...
    assert group.state == "on"
    # ...but the reported brightness AND colour are the Attribute
    # member's, not the State member's and not an average of the two.
    assert group.attributes["brightness"] == 100
    assert group.attributes["color_temp_kelvin"] == 3000

    # Attribute scope dark, group still on via the State member:
    # reported brightness/colour fall back to the lit base member.
    hass.states.async_set("light.bedroom_1", "off", dict(ct_attrs))
    await hass.async_block_till_done()
    group = hass.states.get("light.test_group")
    assert group.state == "on"
    assert group.attributes["brightness"] == 240
    assert group.attributes["color_temp_kelvin"] == 5000

    # State member off: the group reads off even though an Attribute
    # member is lit — the State set alone decides on/off.
    hass.states.async_set(
        "light.bedroom_1",
        "on",
        {**ct_attrs, "brightness": 100, "color_temp_kelvin": 3000},
    )
    hass.states.async_set("light.bedroom_2", "off", dict(ct_attrs))
    await hass.async_block_till_done()
    group = hass.states.get("light.test_group")
    assert group.state == "off"


async def test_mixed_onoff_member_dropped_from_supported_modes(
    hass: HomeAssistant,
) -> None:
    """CHANGELOG 1.1.1: ONOFF is dropped from the group's supported
    colour modes when other modes exist."""
    await _setup(hass, _entry_data())
    hass.states.async_set(
        "light.bedroom_1",
        "on",
        {"supported_color_modes": ["onoff"], "color_mode": "onoff"},
    )
    hass.states.async_set(
        "light.bedroom_2", "on", dict(_BRIGHTNESS_ATTRS)
    )
    hass.states.async_set(
        "light.bedroom_3",
        "off",
        {"supported_color_modes": ["onoff"], "color_mode": "onoff"},
    )
    await hass.async_block_till_done()

    group = hass.states.get("light.test_group")
    assert group is not None
    modes = group.attributes["supported_color_modes"]
    assert "brightness" in modes
    assert "onoff" not in modes


async def test_enforcement_never_fires_on_startup(hass: HomeAssistant) -> None:
    """README / CHANGELOG 1.2.0: enforcement never fires on startup,
    even with a lit member outside the narrowed Power-ON set."""
    recorded = _track_light_calls(hass)
    # The lit member outside the narrowed Power-ON set must exist
    # BEFORE the entry sets up — that is the startup scenario.
    hass.states.async_set("light.bedroom_1", "off", dict(_BRIGHTNESS_ATTRS))
    hass.states.async_set("light.bedroom_2", "on", dict(_BRIGHTNESS_ATTRS))
    hass.states.async_set("light.bedroom_3", "off", dict(_BRIGHTNESS_ATTRS))
    await _setup(
        hass,
        _entry_data(
            **{
                CONF_ASYMMETRIC_POWER: True,
                CONF_POWER_ON_MEMBERS: ["light.bedroom_1"],
                CONF_ENFORCE_POWER_SCOPE: True,
            }
        ),
    )
    await hass.async_block_till_done()
    # Setup itself issued no light calls at all.
    assert _member_calls(recorded, set(MEMBERS)) == []


async def test_services_semantics_and_runtime_persistence(
    hass: HomeAssistant,
) -> None:
    """README services/presets claims: set_members changes only the
    fields passed and refuses an empty call; save_preset refuses a
    duplicate without overwrite; applying an unknown preset is
    refused; deleting a preset leaves groups untouched; runtime
    settings and last_applied_preset survive a reload (restart)."""
    entry = await _setup(hass, _entry_data())
    for member in MEMBERS:
        hass.states.async_set(member, "off", dict(_BRIGHTNESS_ATTRS))
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_members",
            {"entity_id": "light.test_group"},
            blocking=True,
        )

    await hass.services.async_call(
        DOMAIN,
        "set_members",
        {
            "entity_id": "light.test_group",
            CONF_ASYMMETRIC_STATE: True,
            CONF_STATE_MEMBERS: ["light.bedroom_2"],
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    group = hass.states.get("light.test_group")
    assert group.attributes["asymmetric_state"] is True
    assert group.attributes["effective_state_members"] == ["light.bedroom_2"]
    # Fields not passed are unchanged.
    assert group.attributes["effective_power_on_members"] == MEMBERS

    await hass.services.async_call(
        DOMAIN,
        "save_preset",
        {"entity_id": "light.test_group", "preset": "Snap"},
        blocking=True,
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "save_preset",
            {"entity_id": "light.test_group", "preset": "Snap"},
            blocking=True,
        )
    await hass.services.async_call(
        DOMAIN,
        "save_preset",
        {"entity_id": "light.test_group", "preset": "Snap", "overwrite": True},
        blocking=True,
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "apply_preset",
            {"entity_id": "light.test_group", "preset": "Nope"},
            blocking=True,
        )

    # Move the group away from the snapshot first, so applying the
    # preset has an observable effect (a no-op apply must fail here).
    await hass.services.async_call(
        DOMAIN,
        "set_members",
        {"entity_id": "light.test_group", CONF_ASYMMETRIC_STATE: False},
        blocking=True,
    )
    await hass.async_block_till_done()
    group = hass.states.get("light.test_group")
    assert group.attributes["asymmetric_state"] is False

    await hass.services.async_call(
        DOMAIN,
        "apply_preset",
        {"entity_id": "light.test_group", "preset": "Snap"},
        blocking=True,
    )
    await hass.async_block_till_done()
    group = hass.states.get("light.test_group")
    assert group.attributes["asymmetric_state"] is True
    assert group.attributes["effective_state_members"] == ["light.bedroom_2"]
    assert group.attributes["last_applied_preset"] == "Snap"

    # Deleting the preset changes nothing on the group (FAQ claim).
    await hass.services.async_call(
        DOMAIN, "delete_preset", {"preset": "Snap"}, blocking=True
    )
    await hass.async_block_till_done()
    group = hass.states.get("light.test_group")
    assert group.attributes["asymmetric_state"] is True
    assert group.attributes["effective_state_members"] == ["light.bedroom_2"]
    response = await hass.services.async_call(
        DOMAIN, "list_presets", {}, blocking=True, return_response=True
    )
    assert response == {"presets": {}}

    # Reload (restart equivalent): runtime settings survive.
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    group = hass.states.get("light.test_group")
    assert group is not None
    assert group.attributes["asymmetric_state"] is True
    assert group.attributes["effective_state_members"] == ["light.bedroom_2"]
    assert group.attributes["last_applied_preset"] == "Snap"


async def test_hide_members_and_unhide_on_remove(hass: HomeAssistant) -> None:
    """README setup/uninstall claims: Hide members hides the member
    lights in the registry; deleting the group shows them again."""
    registry = er.async_get(hass)
    first = registry.async_get_or_create(
        "light", "demo", "hide_a_uid", suggested_object_id="hide_a"
    )
    second = registry.async_get_or_create(
        "light", "demo", "hide_b_uid", suggested_object_id="hide_b"
    )
    members = [first.entity_id, second.entity_id]
    entry = await _setup(
        hass,
        _entry_data(
            **{
                CONF_BASE_MEMBERS: members,
                CONF_POWER_ON_MEMBERS: members,
                CONF_POWER_OFF_MEMBERS: members,
                CONF_ATTRIBUTE_MEMBERS: members,
                CONF_STATE_MEMBERS: members,
                CONF_HIDE_MEMBERS: True,
            }
        ),
    )
    await hass.async_block_till_done()
    for entity_id in members:
        reg_entry = registry.async_get(entity_id)
        assert reg_entry is not None
        assert reg_entry.hidden_by == er.RegistryEntryHider.INTEGRATION

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    for entity_id in members:
        reg_entry = registry.async_get(entity_id)
        assert reg_entry is not None
        assert reg_entry.hidden_by is None
