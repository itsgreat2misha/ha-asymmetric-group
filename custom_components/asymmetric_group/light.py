"""Light platform for Asymmetric Group.

One light entity per config entry. Externally it looks like a core
light group (same entity_id semantics, same service surface), but the
member set used for turn-on, turn-off, attribute changes and state
computation is resolved per dimension from the runtime.

A member may itself be a light group (core or asymmetric). It stays
atomic, exactly as in core groups: it is called as one entity and
contributes its own aggregated state as one member — nothing here
ever expands a member into its own members.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from . import engine
from .const import (
    ATTR_LAST_APPLIED_PRESET,
    CONF_ASYMMETRIC_ATTRIBUTES,
    CONF_ASYMMETRIC_POWER,
    CONF_ASYMMETRIC_STATE,
    CONF_ENFORCE_POWER_SCOPE,
    DOMAIN,
)
from .runtime import GroupRuntime

_EFFECT_BIT = LightEntityFeature.EFFECT

# Service-data keys that count as an attribute payload when they
# reach async_turn_on: brightness (absolute, percent, and step
# forms), colour (colour temperature in kelvin or legacy mireds,
# colour name, hs/rgb/rgbw/rgbww/xy, and the white channel), and
# effect. These are string literals rather than the ATTR_* constants
# because some legacy names (color_temp, kelvin) no longer exist as
# constants in current Home Assistant, while a payload carrying them
# should still be recognised.
#
# Deliberately NOT payload keys: "transition" and "flash" are
# turn_on modifiers, not attributes to apply — a call carrying only
# a transition is a fade-on (a power action) and behaves like a
# bare turn_on, with the transition riding along on the call.
_ATTRIBUTE_PAYLOAD_KEYS = frozenset(
    {
        "brightness",
        "brightness_pct",
        "brightness_step",
        "brightness_step_pct",
        "color_name",
        "color_temp",
        "color_temp_kelvin",
        "kelvin",
        "effect",
        "hs_color",
        "rgb_color",
        "rgbw_color",
        "rgbww_color",
        "white",
        "xy_color",
    }
)


def _has_attribute_payload(kwargs: dict[str, Any]) -> bool:
    """True when a turn_on call carries an attribute payload."""
    return any(key in _ATTRIBUTE_PAYLOAD_KEYS for key in kwargs)


# Fallbacks when no member reports a colour-temperature range; the
# same literals core's group light reduces with (the DEFAULT_MIN_KELVIN
# / DEFAULT_MAX_KELVIN names are not importable from
# homeassistant.components.light on the oldest HA this integration
# supports, so they cannot be referenced directly).
_FALLBACK_MIN_KELVIN = 2000
_FALLBACK_MAX_KELVIN = 6500


class _PresetExtraData(ExtraStoredData):
    """Restored extra data carrying last_applied_preset."""

    def __init__(self, last_applied_preset: str | None) -> None:
        self._last_applied_preset = last_applied_preset

    def as_dict(self) -> dict:
        return {ATTR_LAST_APPLIED_PRESET: self._last_applied_preset}

    @classmethod
    def from_dict(cls, data: dict) -> _PresetExtraData:
        return cls(data.get(ATTR_LAST_APPLIED_PRESET))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the group's light entity."""
    from . import get_runtime

    runtime = get_runtime(hass, entry.entry_id)
    async_add_entities([AsymmetricGroupLight(runtime)])


class AsymmetricGroupLight(RestoreEntity, LightEntity):
    """A light group with per-dimension member sets."""

    _attr_has_entity_name = False

    def __init__(self, runtime: GroupRuntime) -> None:
        self._runtime = runtime
        self._attr_name = runtime.name
        self._attr_unique_id = f"{runtime.entry_id}_light"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.entry_id)},
            name=runtime.name,
        )
        self._remove_runtime_listener = None
        self._remove_state_tracker = None

    # ------------------------------------------------------------------
    # Lifecycle
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Make this group findable by the preset services.
        self._runtime.register_entity_id(self.entity_id)
        self.async_on_remove(self._runtime.unregister_entity_id)

        # Restore last_applied_preset as a fallback if the runtime
        # store had nothing (the store normally carries it).
        if self._runtime.last_applied_preset is None:
            # async_get_last_extra_data returns a RestoredExtraData
            # whose as_dict() gives the stored payload.
            last_extra = await self.async_get_last_extra_data()
            if last_extra is not None:
                data = last_extra.as_dict()
                if data.get(ATTR_LAST_APPLIED_PRESET):
                    self._runtime.last_applied_preset = data[ATTR_LAST_APPLIED_PRESET]

        @callback
        def _member_state_changed(event) -> None:
            self.async_write_ha_state()

        self._remove_state_tracker = async_track_state_change_event(
            self.hass, self._runtime.base_members, _member_state_changed
        )
        self.async_on_remove(self._remove_state_tracker)

        self._remove_runtime_listener = self._runtime.add_listener(
            self._handle_runtime_update
        )
        self.async_on_remove(self._remove_runtime_listener)

    @callback
    def _handle_runtime_update(self) -> None:
        self.async_write_ha_state()

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        """Persist last_applied_preset with the restored state."""
        return _PresetExtraData(self._runtime.last_applied_preset)

    # ------------------------------------------------------------------
    # State helpers
    def _member_state(self, entity_id: str):
        return self.hass.states.get(entity_id)

    def _is_member_on(self, entity_id: str) -> bool:
        state = self._member_state(entity_id)
        return state is not None and state.state == "on"

    def _group_state(self) -> str:
        sets = self._runtime.effective_sets()
        member_states = []
        for member in sets["state"]:
            state = self._member_state(member)
            member_states.append(state.state if state is not None else None)
        return engine.compute_group_state(
            member_states, self._runtime.all_entities
        )

    @property
    def state(self) -> str | None:
        """Mirror core group state, including unknown/unavailable."""
        result = self._group_state()
        if result in ("unknown", "unavailable"):
            return result
        return "on" if result == "on" else "off"

    @property
    def is_on(self) -> bool:
        return self._group_state() == "on"

    @property
    def available(self) -> bool:
        return self._group_state() != "unavailable"

    # ------------------------------------------------------------------
    # Attribute computation (over currently-ON attribute-scope members)
    def _on_attribute_members(self) -> list[str]:
        sets = self._runtime.effective_sets()
        return [m for m in sets["attributes"] if self._is_member_on(m)]

    def _display_members(self) -> list[str]:
        """Members whose attributes the group displays.

        Normally the currently-on Attribute-scope members. When that
        scope is dark but the group is on via another scope, fall back
        to lit base members (core-group behaviour) so the reported
        brightness/colour stay consistent with the reported
        color_mode instead of going blank.
        """
        on_attr = self._on_attribute_members()
        if on_attr:
            return on_attr
        return [m for m in self._runtime.base_members if self._is_member_on(m)]

    def _chosen_color_mode(self, on_members: list[str]) -> str | None:
        """Unanimous color_mode of the on members, else the first one's.

        Simplification (vs core's mode negotiation): we just describe
        the lights attributes will actually change. Member modes that
        the group itself does not support (an ONOFF member in a mixed
        group, whose ONOFF was dropped from supported_color_modes)
        are skipped so the reported mode is always a supported one.
        """
        supported = self.supported_color_modes
        modes = []
        for member in on_members:
            state = self._member_state(member)
            if state is not None and state.attributes.get("color_mode"):
                mode = state.attributes["color_mode"]
                if mode in supported:
                    modes.append(mode)
        if not modes:
            return None
        if all(mode == modes[0] for mode in modes):
            return modes[0]
        return modes[0]

    @property
    def color_mode(self) -> str | None:
        on_members = self._on_attribute_members()
        if on_members:
            mode = self._chosen_color_mode(on_members)
            if mode is not None:
                return mode
        # The group can be ON via its State scope while nobody in the
        # Attribute scope is on (the scopes are independent). HA
        # raises "does not report a color mode" and drops the state
        # write if an ON light with supported color modes reports
        # color_mode=None — which silently froze the group state at
        # off. Fall back to the mode of any lit base member, then to
        # the Attribute scope's (off) members, so a mode is reported
        # whenever the members support one.
        lit = [m for m in self._runtime.base_members if self._is_member_on(m)]
        if lit:
            mode = self._chosen_color_mode(lit)
            if mode is not None:
                return mode
        sets = self._runtime.effective_sets()
        mode = self._chosen_color_mode(sets["attributes"])
        if mode is not None:
            return mode
        # Last resort (e.g. only an ONOFF member is lit in a mixed
        # group): report any supported mode so the mode is valid.
        supported = self.supported_color_modes
        return min(supported) if supported else None

    @property
    def supported_color_modes(self) -> set[str]:
        sets = self._runtime.effective_sets()
        modes: set[str] = set()
        for member in sets["attributes"]:
            state = self._member_state(member)
            if state is not None:
                modes.update(state.attributes.get("supported_color_modes") or [])
        if not modes:
            return {ColorMode.ONOFF}
        # HA forbids ONOFF alongside any other mode ("sets invalid
        # supported color modes" — the entity is refused entirely).
        # A mixed group (e.g. an on/off closet light next to dimmable
        # colour lights) must drop ONOFF, like core groups do.
        if len(modes) > 1:
            modes.discard(ColorMode.ONOFF)
        return modes

    @property
    def brightness(self) -> int | None:
        values = []
        for member in self._display_members():
            state = self._member_state(member)
            if state is not None and state.attributes.get("brightness") is not None:
                values.append(state.attributes["brightness"])
        return engine.mean_int(values)

    def _average_attr(self, attr: str):
        """Mean of a numeric attribute over on attribute-scope members."""
        values = []
        for member in self._display_members():
            state = self._member_state(member)
            if state is not None and state.attributes.get(attr) is not None:
                values.append(state.attributes[attr])
        return engine.mean_float(values)

    def _average_tuple_attr(self, attr: str):
        """Per-component mean of a tuple attribute (rgb/hs/xy)."""
        tuples = []
        for member in self._display_members():
            state = self._member_state(member)
            if state is not None and state.attributes.get(attr) is not None:
                tuples.append(state.attributes[attr])
        if not tuples:
            return None
        return tuple(
            engine.mean_float([t[i] for t in tuples]) for i in range(len(tuples[0]))
        )

    @property
    def color_temp_kelvin(self) -> int | None:
        if self.color_mode != ColorMode.COLOR_TEMP:
            return None
        value = self._average_attr("color_temp_kelvin")
        return round(value) if value is not None else None

    def _member_capability_values(self, attr: str) -> list:
        """A capability attribute over all attribute-scope members.

        Collected over the whole scope rather than only the lit
        members (like supported_color_modes and effect_list) so the
        reported capabilities do not flap as members toggle.
        """
        values = []
        sets = self._runtime.effective_sets()
        for member in sets["attributes"]:
            state = self._member_state(member)
            if state is not None and state.attributes.get(attr) is not None:
                values.append(state.attributes[attr])
        return values

    @property
    def min_color_temp_kelvin(self) -> int:
        """Warmest colour temperature any member supports.

        Without this override HA's base LightEntity falls back to the
        legacy mireds defaults and warns on state writes (core PR
        #79591). Mirror core's group light: the lowest member minimum,
        with a fixed fallback when no member reports a range.
        """
        values = self._member_capability_values("min_color_temp_kelvin")
        return int(min(values)) if values else _FALLBACK_MIN_KELVIN

    @property
    def max_color_temp_kelvin(self) -> int:
        """Coldest colour temperature any member supports.

        Same core-group reduction as min_color_temp_kelvin: the
        highest member maximum, with a fixed fallback.
        """
        values = self._member_capability_values("max_color_temp_kelvin")
        return int(max(values)) if values else _FALLBACK_MAX_KELVIN

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        if self.color_mode not in (ColorMode.RGB, ColorMode.RGBW, ColorMode.RGBWW):
            return None
        value = self._average_tuple_attr("rgb_color")
        if value is None:
            return None
        return tuple(round(component) for component in value)

    @property
    def hs_color(self) -> tuple[float, float] | None:
        if self.color_mode != ColorMode.HS:
            return None
        value = self._average_tuple_attr("hs_color")
        return value  # type: ignore[return-value]

    @property
    def xy_color(self) -> tuple[float, float] | None:
        if self.color_mode != ColorMode.XY:
            return None
        value = self._average_tuple_attr("xy_color")
        return value  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Effects
    @property
    def supported_features(self) -> LightEntityFeature:
        sets = self._runtime.effective_sets()
        for member in sets["attributes"]:
            state = self._member_state(member)
            if state is not None and (
                (state.attributes.get("supported_features") or 0) & int(_EFFECT_BIT)
            ):
                return LightEntityFeature.EFFECT
        return LightEntityFeature(0)

    @property
    def effect_list(self) -> list[str] | None:
        if not (self.supported_features & LightEntityFeature.EFFECT):
            return None
        # Union over *all* attribute-scope members (not just on ones)
        # so the list does not flap as members toggle.
        effects: list[str] = []
        sets = self._runtime.effective_sets()
        for member in sets["attributes"]:
            state = self._member_state(member)
            if state is not None:
                for effect in state.attributes.get("effect_list") or []:
                    if effect not in effects:
                        effects.append(effect)
        return effects or None

    @property
    def effect(self) -> str | None:
        for member in self._display_members():
            state = self._member_state(member)
            if state is not None and state.attributes.get(ATTR_EFFECT):
                return state.attributes[ATTR_EFFECT]
        return None

    # ------------------------------------------------------------------
    # Attributes
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sets = self._runtime.effective_sets()
        return {
            # entity_id always reports the full base set — the DIRECT
            # members only, like a core group: a member that is itself
            # a group is listed as itself, never flattened into its
            # own members. Note: core's expand() does not expand this
            # platform (it only recognises platform: group), so
            # templates must read this attribute rather than expand()
            # the group itself.
            "entity_id": self._runtime.base_members,
            "effective_power_on_members": sets["power_on"],
            "effective_power_off_members": sets["power_off"],
            "effective_attribute_members": sets["attributes"],
            "effective_state_members": sets["state"],
            CONF_ASYMMETRIC_POWER: self._runtime.toggles["power"],
            CONF_ASYMMETRIC_ATTRIBUTES: self._runtime.toggles["attributes"],
            CONF_ASYMMETRIC_STATE: self._runtime.toggles["state"],
            CONF_ENFORCE_POWER_SCOPE: self._runtime.toggles["enforce"],
            ATTR_LAST_APPLIED_PRESET: self._runtime.last_applied_preset,
        }

    # ------------------------------------------------------------------
    # Control
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on per the engine plan.

        A bare turn_on (or one carrying only a transition) activates
        the power-ON set. A turn_on with an attribute payload touches
        only the attribute set: off members that are also in the
        power-ON set are switched on with the payload, already-on
        attribute members are adjusted in place, and power-ON members
        outside the attribute set are left completely untouched.

        Both calls are issued back-to-back from the same plan, so an
        excluded light never flashes: it is either in the activation
        call once, in the attribute-only call once, or untouched.
        """
        sets = self._runtime.effective_sets()
        currently_on = {
            member
            for member in self._runtime.base_members
            if self._is_member_on(member)
        }
        activate, attribute_only = engine.plan_turn_on(
            sets, currently_on, has_attributes=_has_attribute_payload(kwargs)
        )
        if activate:
            await self.hass.services.async_call(
                "light",
                "turn_on",
                {**kwargs, "entity_id": activate},
                blocking=False,
                context=self._context,
            )
        if attribute_only:
            await self.hass.services.async_call(
                "light",
                "turn_on",
                {**kwargs, "entity_id": attribute_only},
                blocking=False,
                context=self._context,
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the power-OFF set (transition forwarded verbatim)."""
        sets = self._runtime.effective_sets()
        if not sets["power_off"]:
            return
        await self.hass.services.async_call(
            "light",
            "turn_off",
            {**kwargs, "entity_id": sets["power_off"]},
            blocking=False,
            context=self._context,
        )
