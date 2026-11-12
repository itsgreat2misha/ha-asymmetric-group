"""The Asymmetric Group integration.

A group helper whose turn-on, turn-off, attribute and state behaviour
can each follow a different member set, switchable at runtime.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import (
    ATTR_OVERWRITE,
    ATTR_PRESET,
    CONF_ASYMMETRIC_ATTRIBUTES,
    CONF_ASYMMETRIC_POWER,
    CONF_ASYMMETRIC_STATE,
    CONF_ATTRIBUTE_MEMBERS,
    CONF_ENFORCE_POWER_SCOPE,
    CONF_POWER_OFF_MEMBERS,
    CONF_POWER_ON_MEMBERS,
    CONF_STATE_MEMBERS,
    DOMAIN,
    PLATFORMS,
    SERVICE_APPLY_PRESET,
    SERVICE_DELETE_PRESET,
    SERVICE_LIST_PRESETS,
    SERVICE_SAVE_PRESET,
    SERVICE_SET_MEMBERS,
)
from .presets import async_get_preset_store
from .runtime import GroupRuntime

_LOGGER = logging.getLogger(__name__)

_DATA_SERVICES_REGISTERED = "services_registered"
_DATA_ENTRIES = "entries"
_DATA_BY_ENTITY = "runtimes_by_entity"


def _domain_data(hass: HomeAssistant) -> dict:
    return hass.data.setdefault(DOMAIN, {})


def merged_config(entry: ConfigEntry) -> dict:
    """Entry data with options taking precedence."""
    return {**entry.data, **entry.options}


def _runtime_for_entity(hass: HomeAssistant, entity_id: str) -> GroupRuntime:
    runtime = _domain_data(hass).get(_DATA_BY_ENTITY, {}).get(entity_id)
    if runtime is None:
        raise ServiceValidationError(f"No Asymmetric Group found for {entity_id}")
    return runtime


# ----------------------------------------------------------------------
# Services (registered once, integration-wide)
async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration: register the preset services once."""
    data = _domain_data(hass)
    if data.get(_DATA_SERVICES_REGISTERED):
        return True

    async def _handle_apply_preset(call: ServiceCall) -> None:
        runtime = _runtime_for_entity(hass, call.data["entity_id"])
        store = await async_get_preset_store(hass)
        preset = store.get(call.data[ATTR_PRESET])
        if preset is None:
            raise ServiceValidationError(
                f"Unknown Asymmetric Group preset: {call.data[ATTR_PRESET]}"
            )
        await runtime.async_apply_preset(preset, call.data[ATTR_PRESET])

    async def _handle_save_preset(call: ServiceCall) -> None:
        runtime = _runtime_for_entity(hass, call.data["entity_id"])
        store = await async_get_preset_store(hass)
        name = call.data[ATTR_PRESET]
        if store.exists(name) and not call.data.get(ATTR_OVERWRITE, False):
            raise ServiceValidationError(
                f"Preset '{name}' already exists; pass overwrite: true to replace it"
            )
        await store.async_save_preset(name, runtime.snapshot_preset())

    async def _handle_delete_preset(call: ServiceCall) -> None:
        store = await async_get_preset_store(hass)
        await store.async_delete_preset(call.data[ATTR_PRESET])

    async def _handle_list_presets(call: ServiceCall) -> ServiceResponse:
        store = await async_get_preset_store(hass)
        return {"presets": await store.async_load()}

    async def _handle_set_members(call: ServiceCall) -> None:
        runtime = _runtime_for_entity(hass, call.data["entity_id"])
        changes = {k: v for k, v in call.data.items() if k != "entity_id"}
        if not changes:
            raise ServiceValidationError(
                "Nothing to change: pass at least one toggle or member set"
            )
        # Member sets cannot create a cycle (they are filtered to the
        # base set), but a cycle can appear after creation when a
        # member group is edited elsewhere to include this group.
        # Refuse to reconfigure a group whose membership already
        # loops back on itself.
        from .config_flow import _creates_cycle

        if _creates_cycle(hass, call.data["entity_id"], runtime.base_members):
            raise ServiceValidationError(
                "This group's members form a cycle: the group contains "
                "itself, directly or through a member group. Fix the "
                "member group before changing this group."
            )
        await runtime.async_apply_sparse(changes)

    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_PRESET,
        _handle_apply_preset,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): str,
                vol.Required(ATTR_PRESET): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SAVE_PRESET,
        _handle_save_preset,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): str,
                vol.Required(ATTR_PRESET): str,
                vol.Optional(ATTR_OVERWRITE, default=False): bool,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_PRESET,
        _handle_delete_preset,
        schema=vol.Schema({vol.Required(ATTR_PRESET): str}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_PRESETS,
        _handle_list_presets,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_MEMBERS,
        _handle_set_members,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): str,
                vol.Optional(CONF_ASYMMETRIC_POWER): cv.boolean,
                vol.Optional(CONF_ASYMMETRIC_ATTRIBUTES): cv.boolean,
                vol.Optional(CONF_ASYMMETRIC_STATE): cv.boolean,
                vol.Optional(CONF_ENFORCE_POWER_SCOPE): cv.boolean,
                vol.Optional(CONF_POWER_ON_MEMBERS): cv.entity_ids,
                vol.Optional(CONF_POWER_OFF_MEMBERS): cv.entity_ids,
                vol.Optional(CONF_ATTRIBUTE_MEMBERS): cv.entity_ids,
                vol.Optional(CONF_STATE_MEMBERS): cv.entity_ids,
            }
        ),
    )
    data[_DATA_SERVICES_REGISTERED] = True
    return True


# ----------------------------------------------------------------------
# Config entry lifecycle
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Asymmetric Group from a config entry."""
    data = _domain_data(hass)
    runtime = GroupRuntime(hass, entry.entry_id, merged_config(entry))
    await runtime.async_load()
    data.setdefault(_DATA_ENTRIES, {})[entry.entry_id] = runtime
    data.setdefault(_DATA_BY_ENTITY, {})

    _apply_hide_members(hass, runtime)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = _domain_data(hass)
        runtime = data.get(_DATA_ENTRIES, {}).pop(entry.entry_id, None)
        if runtime is not None:
            runtime.unregister_entity_id()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up when a group is deleted: unhide members we hid.

    Without this, deleting a group with hide_members on would leave
    its lights hidden forever (hidden_by = INTEGRATION with nothing
    left to undo it).
    """
    registry = er.async_get(hass)
    config = merged_config(entry)
    for member_id in config.get("base_members") or []:
        reg_entry = registry.async_get(member_id)
        if reg_entry is not None and (
            reg_entry.hidden_by == er.RegistryEntryHider.INTEGRATION
        ):
            registry.async_update_entity(member_id, hidden_by=None)


def get_runtime(hass: HomeAssistant, entry_id: str) -> GroupRuntime:
    """Return the runtime for a config entry (used by the platforms)."""
    return _domain_data(hass)[_DATA_ENTRIES][entry_id]


# ----------------------------------------------------------------------
# Hide members (core-group parity)
def _apply_hide_members(hass: HomeAssistant, runtime: GroupRuntime) -> None:
    """Apply the hide_members option to base members via the registry.

    When hiding, members get hidden_by = INTEGRATION. When unhiding,
    only members *we* hid (hidden_by == INTEGRATION) are shown again;
    a member the user hid themselves is left alone.
    """
    registry = er.async_get(hass)
    for member_id in runtime.base_members:
        reg_entry = registry.async_get(member_id)
        if reg_entry is None:
            continue
        if runtime.hide_members:
            if reg_entry.hidden_by is None:
                # hidden_by takes an er.RegistryEntryHider enum value.
                registry.async_update_entity(
                    member_id, hidden_by=er.RegistryEntryHider.INTEGRATION
                )
        elif reg_entry.hidden_by == er.RegistryEntryHider.INTEGRATION:
            registry.async_update_entity(member_id, hidden_by=None)
