"""Global preset library for Asymmetric Group.

Presets live in a single integration-level Store, shared by all groups.
A preset is a *sparse* dict (see engine.py): keys that are present are
applied, absent keys leave a group untouched.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, PRESET_STORE_KEY, PRESET_STORE_VERSION

_DATA_KEY = "preset_store"


class PresetStore:
    """Thin async wrapper around the preset Store."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store = Store(hass, PRESET_STORE_VERSION, PRESET_STORE_KEY)
        self._presets: dict[str, dict] | None = None

    async def async_load(self) -> dict[str, dict]:
        """Load (once) and return all presets as {name: preset_dict}."""
        if self._presets is None:
            data = await self._store.async_load()
            self._presets = dict(data) if isinstance(data, dict) else {}
        return self._presets

    async def _async_save(self) -> None:
        await self._store.async_save(self._presets or {})

    def list_presets(self) -> list[str]:
        """Return preset names (requires async_load first)."""
        return sorted((self._presets or {}).keys())

    def get(self, name: str) -> dict | None:
        """Return a copy of one preset, or None."""
        preset = (self._presets or {}).get(name)
        return dict(preset) if preset is not None else None

    def exists(self, name: str) -> bool:
        return name in (self._presets or {})

    async def async_save_preset(self, name: str, data: dict) -> None:
        await self.async_load()
        assert self._presets is not None
        self._presets[name] = dict(data)
        await self._async_save()

    async def async_delete_preset(self, name: str) -> bool:
        await self.async_load()
        assert self._presets is not None
        if name not in self._presets:
            return False
        del self._presets[name]
        await self._async_save()
        return True


async def async_get_preset_store(hass: HomeAssistant) -> PresetStore:
    """Return the shared PresetStore for this HA instance (lazy-init)."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    store = domain_data.get(_DATA_KEY)
    if store is None:
        store = PresetStore(hass)
        await store.async_load()
        domain_data[_DATA_KEY] = store
    return store
