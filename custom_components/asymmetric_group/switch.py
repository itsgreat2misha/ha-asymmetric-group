"""Companion switches for Asymmetric Group.

Each group gets four switches — one per dimension plus Enforce power
scope — that flip the runtime toggles. They live on the same device as
the group light, so the mode control is stored *on the group itself*
(config default + these switches / preset services at runtime), not
in a separate user-managed helper.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    TOGGLE_ATTRIBUTES,
    TOGGLE_ENFORCE,
    TOGGLE_POWER,
    TOGGLE_STATE,
)
from .runtime import GroupRuntime

# runtime toggle key -> translation key
_SWITCHES = (
    (TOGGLE_POWER, "asymmetric_power"),
    (TOGGLE_ATTRIBUTES, "asymmetric_attributes"),
    (TOGGLE_STATE, "asymmetric_state"),
    (TOGGLE_ENFORCE, "enforce_power_scope"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the companion switches for a group."""
    from . import get_runtime

    runtime = get_runtime(hass, entry.entry_id)
    async_add_entities(
        [
            AsymmetricToggleSwitch(runtime, toggle_key, translation_key)
            for toggle_key, translation_key in _SWITCHES
        ]
    )


class AsymmetricToggleSwitch(RestoreEntity, SwitchEntity):
    """A switch reflecting and controlling one runtime toggle."""

    _attr_has_entity_name = True

    def __init__(
        self, runtime: GroupRuntime, toggle_key: str, translation_key: str
    ) -> None:
        self._runtime = runtime
        self._toggle_key = toggle_key
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{runtime.entry_id}_{toggle_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.entry_id)},
            name=runtime.name,
        )
        self._remove_runtime_listener = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The runtime Store is the source of truth for the toggle
        # (RestoreEntity inheritance keeps us consistent with HA's
        # restore machinery; no separate restore is applied so a
        # preset-set value in the store is never overwritten by a
        # stale restored switch state).
        self._remove_runtime_listener = self._runtime.add_listener(
            self._handle_runtime_update
        )
        self.async_on_remove(self._remove_runtime_listener)

    def _handle_runtime_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return bool(self._runtime.toggles[self._toggle_key])

    async def async_turn_on(self, **kwargs) -> None:
        await self._runtime.async_set_toggle(self._toggle_key, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._runtime.async_set_toggle(self._toggle_key, False)
