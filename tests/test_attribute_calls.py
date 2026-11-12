"""HA-level tests for attribute-bearing turn_on calls.

Semantics under test (the intersection model): a turn_on carrying a
brightness/colour/effect payload touches only the Attribute set.
Members in both the Power-ON and Attribute sets are switched on with
the payload; Attribute members outside the Power set are adjusted
only if already on; Power-ON members outside the Attribute set are
not targeted at all. A turn_on whose only extra is a transition is
not a payload call: it behaves like a bare turn_on.
"""

from __future__ import annotations

import unittest

try:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry
except ImportError:
    # See tests/test_config_flow.py: skip cleanly without the harness.
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
from tests.test_nesting import _BRIGHTNESS_ATTRS, _member_calls, _track_light_calls

MEMBERS = ["light.ceiling", "light.vanity"]


def _data(**overrides):
    """Bathroom group: ceiling in the Attribute set, vanity not.

    Asymmetric power is off, so the Power-ON set is the full base
    set — the vanity is a Power member excluded from Attributes,
    which is exactly the arrangement the intersection model governs.
    """
    data = {
        CONF_NAME: "Bath Group",
        CONF_BASE_MEMBERS: list(MEMBERS),
        CONF_POWER_ON_MEMBERS: list(MEMBERS),
        CONF_POWER_OFF_MEMBERS: list(MEMBERS),
        CONF_ATTRIBUTE_MEMBERS: ["light.ceiling"],
        CONF_STATE_MEMBERS: list(MEMBERS),
        CONF_ASYMMETRIC_POWER: False,
        CONF_ASYMMETRIC_ATTRIBUTES: True,
        CONF_ASYMMETRIC_STATE: False,
        CONF_ENFORCE_POWER_SCOPE: False,
        CONF_HIDE_MEMBERS: False,
        CONF_ALL_ENTITIES: False,
    }
    data.update(overrides)
    return data


async def _setup(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Bath Group", data=_data())
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _targets(calls) -> list[str]:
    return [t for c in calls for t in c["data"].get("entity_id", [])]


async def test_vanity_not_targeted_by_group_brightness_when_on(
    hass: HomeAssistant,
) -> None:
    """Both lights on; a group brightness call touches only the ceiling."""
    await _setup(hass)
    for member in MEMBERS:
        hass.states.async_set(member, "on", {**_BRIGHTNESS_ATTRS, "brightness": 100})
    await hass.async_block_till_done()
    recorded = _track_light_calls(hass)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.bath_group", "brightness": 200},
        blocking=True,
    )
    await hass.async_block_till_done()
    calls = _member_calls(recorded, set(MEMBERS))
    assert _targets(calls) == ["light.ceiling"]
    assert calls[0]["data"]["brightness"] == 200


async def test_vanity_not_woken_by_group_brightness_when_off(
    hass: HomeAssistant,
) -> None:
    """Both lights off; a group brightness call wakes only the ceiling."""
    await _setup(hass)
    for member in MEMBERS:
        hass.states.async_set(member, "off", dict(_BRIGHTNESS_ATTRS))
    await hass.async_block_till_done()
    recorded = _track_light_calls(hass)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.bath_group", "brightness": 200},
        blocking=True,
    )
    await hass.async_block_till_done()
    calls = _member_calls(recorded, set(MEMBERS))
    assert _targets(calls) == ["light.ceiling"]
    assert calls[0]["data"]["brightness"] == 200


async def test_transition_only_call_behaves_like_bare_turn_on(
    hass: HomeAssistant,
) -> None:
    """Transition alone is not a payload: the full Power-ON set is targeted."""
    await _setup(hass)
    for member in MEMBERS:
        hass.states.async_set(member, "off", dict(_BRIGHTNESS_ATTRS))
    await hass.async_block_till_done()
    recorded = _track_light_calls(hass)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.bath_group", "transition": 2},
        blocking=True,
    )
    await hass.async_block_till_done()
    calls = _member_calls(recorded, set(MEMBERS))
    assert sorted(_targets(calls)) == sorted(MEMBERS)
