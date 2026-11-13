"""Config and options flow for Asymmetric Group.

Creation is a helper-style flow:
  1. user       — name + base members (ALL lights involved in any way).
                  Members may themselves be light groups (core or
                  asymmetric); a group member stays atomic — it sits in
                  each set as one unit. Only a membership cycle (the
                  group containing itself, directly or transitively) is
                  rejected.
  2. sets       — the dimension toggles plus Enforce power scope
                  (their starting/default state) plus hide_members /
                  all_entities.
  3. power      — ONLY shown when Asymmetric power is on:
                  Power-ON and Power-OFF member selectors.
  4. attributes — ONLY shown when Asymmetric attributes is on.
  5. state      — ONLY shown when Asymmetric state is on.

Subgroup selectors for a dimension are never shown while that
dimension is off. A dimension that is off behaves exactly like a core
group (its set resolves to base), and if it is switched on later from
its companion switch before being configured here, its subgroup
defaults to the full base set.

The options flow mirrors this: "members" opens with the base
members plus the toggles (base members ARE editable here — a set
that still tracks the base set follows it when it changes, a
narrowed set is filtered to it), then runs the same conditional
chain (step ids members / members_power / members_attributes /
members_state); "presets" opens the global preset library editor.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify

from .const import (
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
    ENTRY_STORE_VERSION,
    MEMBER_KEYS,
    entry_store_key,
)
from .presets import async_get_preset_store

_NEW_PRESET = "__new__"

_TOGGLE_PRESET_KEYS = (
    CONF_ASYMMETRIC_POWER,
    CONF_ASYMMETRIC_ATTRIBUTES,
    CONF_ASYMMETRIC_STATE,
    CONF_ENFORCE_POWER_SCOPE,
)

# Keys the options flow is allowed to write (only the name stays
# in data — core groups don't offer renaming in Configure either).
_OPTION_KEYS = (
    CONF_ASYMMETRIC_POWER,
    CONF_ASYMMETRIC_ATTRIBUTES,
    CONF_ASYMMETRIC_STATE,
    CONF_ENFORCE_POWER_SCOPE,
    CONF_POWER_ON_MEMBERS,
    CONF_POWER_OFF_MEMBERS,
    CONF_ATTRIBUTE_MEMBERS,
    CONF_STATE_MEMBERS,
    CONF_HIDE_MEMBERS,
    CONF_ALL_ENTITIES,
    CONF_BASE_MEMBERS,
)


def _light_selector() -> EntitySelector:
    return EntitySelector(EntitySelectorConfig(domain="light", multiple=True))


def _creates_cycle(
    hass: HomeAssistant, own_entity_id: str, members: list[str]
) -> bool:
    """True if ``members`` would put the group inside itself.

    Members may be groups (core or asymmetric); like core groups, a
    group member is just a member. The one configuration that cannot
    work is a cycle — the group containing itself, directly or
    transitively — because power/state fan-out would chase its own
    tail. Walk each member's ``entity_id`` attribute (the attribute
    every group reports) looking for ``own_entity_id``. A member with
    no state, or a state without the attribute, is a leaf; the visited
    set keeps a pre-existing loop among *other* groups from hanging
    the walk.
    """
    seen: set[str] = set()
    stack = list(members)
    while stack:
        entity_id = stack.pop()
        if entity_id == own_entity_id:
            return True
        if entity_id in seen:
            continue
        seen.add(entity_id)
        state = hass.states.get(entity_id)
        if state is not None:
            stack.extend(state.attributes.get("entity_id") or [])
    return False


def _group_entity_id(hass: HomeAssistant, entry_id: str) -> str | None:
    """The light entity_id of an existing entry's group, if registered."""
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry_id):
        if reg_entry.domain == "light" and reg_entry.platform == DOMAIN:
            return reg_entry.entity_id
    return None


def _toggles_schema(defaults: dict) -> vol.Schema:
    """Step 2 form: the toggles + core-parity options only."""
    return vol.Schema(
        {
            vol.Required(
                CONF_ASYMMETRIC_POWER,
                default=bool(defaults.get(CONF_ASYMMETRIC_POWER, False)),
            ): BooleanSelector(),
            vol.Required(
                CONF_ASYMMETRIC_ATTRIBUTES,
                default=bool(defaults.get(CONF_ASYMMETRIC_ATTRIBUTES, False)),
            ): BooleanSelector(),
            vol.Required(
                CONF_ASYMMETRIC_STATE,
                default=bool(defaults.get(CONF_ASYMMETRIC_STATE, False)),
            ): BooleanSelector(),
            vol.Required(
                CONF_ENFORCE_POWER_SCOPE,
                default=bool(defaults.get(CONF_ENFORCE_POWER_SCOPE, False)),
            ): BooleanSelector(),
            vol.Required(
                CONF_HIDE_MEMBERS,
                default=bool(defaults.get(CONF_HIDE_MEMBERS, False)),
            ): BooleanSelector(),
            vol.Required(
                CONF_ALL_ENTITIES,
                default=bool(defaults.get(CONF_ALL_ENTITIES, False)),
            ): BooleanSelector(),
        }
    )


def _options_members_schema(defaults: dict) -> vol.Schema:
    """Options "members" form: base members + the toggles in one
    form, like core's single options form for groups."""
    fields: dict = {
        vol.Required(
            CONF_BASE_MEMBERS,
            default=list(defaults.get(CONF_BASE_MEMBERS) or []),
        ): _light_selector(),
    }
    fields.update(_toggles_schema(defaults).schema)
    return vol.Schema(fields)


def _power_schema(defaults: dict) -> vol.Schema:
    base = list(defaults.get(CONF_BASE_MEMBERS) or [])
    return vol.Schema(
        {
            vol.Required(
                CONF_POWER_ON_MEMBERS,
                default=list(defaults.get(CONF_POWER_ON_MEMBERS) or base),
            ): _light_selector(),
            vol.Required(
                CONF_POWER_OFF_MEMBERS,
                default=list(defaults.get(CONF_POWER_OFF_MEMBERS) or base),
            ): _light_selector(),
        }
    )


def _single_member_schema(key: str, defaults: dict) -> vol.Schema:
    base = list(defaults.get(CONF_BASE_MEMBERS) or [])
    return vol.Schema(
        {
            vol.Required(
                key, default=list(defaults.get(key) or base)
            ): _light_selector(),
        }
    )


class _SetsChain:
    """Shared conditional-subgroup routing for both flows.

    Subclasses set ``self._working`` (the config being assembled) and
    implement ``_finish_sets`` plus the four step methods' form
    rendering via the helpers below. The chain order is always
    power -> attributes -> state; a dimension whose toggle is off is
    skipped entirely, leaving its subgroup at its current/default
    value.
    """

    _working: dict

    def _next_after(self, dimension: str) -> str | None:
        """Next dimension step needed after ``dimension`` (or None)."""
        order = ["power", "attributes", "state"]
        toggles = {
            "power": CONF_ASYMMETRIC_POWER,
            "attributes": CONF_ASYMMETRIC_ATTRIBUTES,
            "state": CONF_ASYMMETRIC_STATE,
        }
        start = order.index(dimension) + 1 if dimension in order else 0
        for nxt in order[start:]:
            if self._working.get(toggles[nxt]):
                return nxt
        return None


class AsymmetricGroupConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Asymmetric Group."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return AsymmetricGroupOptionsFlow(config_entry)

    def __init__(self) -> None:
        self._working: dict = {}

    # ------------------------------------------------------------------
    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            base_members = list(user_input.get(CONF_BASE_MEMBERS) or [])
            # Group members are fine (they stay atomic, like core
            # groups); only a cycle is rejected. The group's own
            # entity does not exist yet, so check against the
            # entity_id its name will generate.
            own_entity_id = f"light.{slugify(user_input.get(CONF_NAME) or '')}"
            if _creates_cycle(self.hass, own_entity_id, base_members):
                errors[CONF_BASE_MEMBERS] = "group_member_cycle"
            else:
                self._working = {
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_BASE_MEMBERS: base_members,
                    # Subgroups start at base; the conditional steps
                    # below narrow them only for enabled dimensions.
                    CONF_POWER_ON_MEMBERS: list(base_members),
                    CONF_POWER_OFF_MEMBERS: list(base_members),
                    CONF_ATTRIBUTE_MEMBERS: list(base_members),
                    CONF_STATE_MEMBERS: list(base_members),
                }
                return await self.async_step_sets()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): TextSelector(),
                    vol.Required(CONF_BASE_MEMBERS): _light_selector(),
                }
            ),
            errors=errors,
        )

    async def async_step_sets(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._working.update(user_input)
            return await self._route_next("start")
        return self.async_show_form(
            step_id="sets",
            data_schema=_toggles_schema(self._working),
        )

    async def async_step_power(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._working.update(user_input)
            return await self._route_next("power")
        return self.async_show_form(
            step_id="power",
            data_schema=_power_schema(self._working),
        )

    async def async_step_attributes(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._working.update(user_input)
            return await self._route_next("attributes")
        return self.async_show_form(
            step_id="attributes",
            data_schema=_single_member_schema(CONF_ATTRIBUTE_MEMBERS, self._working),
        )

    async def async_step_state(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._working.update(user_input)
            return self.async_create_entry(
                title=self._working[CONF_NAME], data=self._working
            )
        return self.async_show_form(
            step_id="state",
            data_schema=_single_member_schema(CONF_STATE_MEMBERS, self._working),
        )

    async def _route_next(self, after: str) -> ConfigFlowResult:
        chain = _SetsChain()
        chain._working = self._working
        nxt = chain._next_after(after if after != "start" else "")
        # _next_after("") starts the scan at "power".
        if nxt == "power":
            return await self.async_step_power()
        if nxt == "attributes":
            return await self.async_step_attributes()
        if nxt == "state":
            return await self.async_step_state()
        return self.async_create_entry(
            title=self._working[CONF_NAME], data=self._working
        )


class AsymmetricGroupOptionsFlow(OptionsFlow):
    """Options: member sets (conditional chain) + preset editor."""

    def __init__(self, config_entry) -> None:
        # The entry is also stored explicitly: current HA manages
        # OptionsFlow.config_entry itself, and _config_entry below
        # prefers the managed attribute when it is present.
        self._entry = config_entry
        self._working: dict = {}
        self._preset_name: str | None = None
        self._preset_is_new = False

    @property
    def _config_entry(self):
        return getattr(self, "config_entry", None) or self._entry

    # ------------------------------------------------------------------
    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["members", "presets"],
        )

    # ------------------------------------------------------------------
    # Members: same conditional chain as the config flow.
    async def async_step_members(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        entry = self._config_entry
        if user_input is None:
            merged = {**entry.data, **entry.options}
            self._working = {key: merged.get(key) for key in _OPTION_KEYS}
            self._working[CONF_BASE_MEMBERS] = list(
                merged.get(CONF_BASE_MEMBERS) or []
            )
            return self.async_show_form(
                step_id="members",
                data_schema=_options_members_schema(self._working),
            )
        self._working.update(user_input)
        new_base = list(self._working.get(CONF_BASE_MEMBERS) or [])
        if not new_base:
            return self.async_show_form(
                step_id="members",
                data_schema=_options_members_schema(self._working),
                errors={CONF_BASE_MEMBERS: "base_members_required"},
            )
        # The cycle check runs against the NEW base: adding members
        # is the dangerous direction, and a cycle can also appear
        # after creation (a member group edited elsewhere to include
        # this group). Refuse to save a cyclic configuration: the
        # membership would make power/state fan-out chase itself.
        own_entity_id = _group_entity_id(self.hass, entry.entry_id)
        if own_entity_id is not None and _creates_cycle(
            self.hass, own_entity_id, new_base
        ):
            return self.async_show_form(
                step_id="members",
                data_schema=_options_members_schema(self._working),
                errors={CONF_BASE_MEMBERS: "group_member_cycle"},
            )
        return await self._route_next("")

    async def async_step_members_power(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._working.update(user_input)
            return await self._route_next("power")
        return self.async_show_form(
            step_id="members_power",
            data_schema=_power_schema(self._working),
        )

    async def async_step_members_attributes(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._working.update(user_input)
            return await self._route_next("attributes")
        return self.async_show_form(
            step_id="members_attributes",
            data_schema=_single_member_schema(CONF_ATTRIBUTE_MEMBERS, self._working),
        )

    async def async_step_members_state(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._working.update(user_input)
            return await self._finish_members()
        return self.async_show_form(
            step_id="members_state",
            data_schema=_single_member_schema(CONF_STATE_MEMBERS, self._working),
        )

    async def _route_next(self, after: str) -> ConfigFlowResult:
        chain = _SetsChain()
        chain._working = self._working
        nxt = chain._next_after(after)
        if nxt == "power":
            return await self.async_step_members_power()
        if nxt == "attributes":
            return await self.async_step_members_attributes()
        if nxt == "state":
            return await self.async_step_members_state()
        return await self._finish_members()

    async def _finish_members(self) -> ConfigFlowResult:
        entry = self._config_entry
        merged = {**entry.data, **entry.options}
        old_base = list(merged.get(CONF_BASE_MEMBERS) or [])
        new_base = list(self._working.get(CONF_BASE_MEMBERS) or [])
        # Keep the subgroups consistent with the new base. A subgroup
        # that still lists the whole old base was tracking the base
        # set, so it follows to the new base; a narrowed subgroup
        # keeps its own membership, filtered to the new base. This
        # save does NOT reconcile any lights — like every Configure
        # edit it reseeds and reloads; enforcement only runs on live
        # runtime changes (switches, presets, set_members).
        for key in MEMBER_KEYS:
            current = self._working.get(key)
            if not isinstance(current, list):
                continue
            if current == old_base:
                self._working[key] = list(new_base)
            else:
                self._working[key] = [m for m in current if m in new_base]
        # A member removed from the base that this group hid must be
        # shown again — the same cleanup async_remove_entry runs on
        # delete, or the light would stay hidden forever.
        removed = [m for m in old_base if m not in new_base]
        if removed:
            from . import _unhide_members

            _unhide_members(self.hass, removed)
        # Options edits re-seed runtime state: clear the per-entry
        # runtime store so stored toggles/subgroups don't shadow
        # the new config after the reload triggered by saving.
        store = Store(
            self.hass, ENTRY_STORE_VERSION, entry_store_key(entry.entry_id)
        )
        # Store.async_remove is safe when no file exists yet.
        await store.async_remove()
        payload = {key: self._working.get(key) for key in _OPTION_KEYS}
        return self.async_create_entry(title="", data=payload)

    # ------------------------------------------------------------------
    # Presets
    async def async_step_presets(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_presets_pick(user_input)

    async def async_step_presets_pick(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        store = await async_get_preset_store(self.hass)
        names = store.list_presets()
        if user_input is not None:
            chosen = user_input["preset"]
            self._preset_is_new = chosen == _NEW_PRESET
            self._preset_name = None if self._preset_is_new else chosen
            return await self.async_step_presets_edit()
        return self.async_show_form(
            step_id="presets_pick",
            data_schema=vol.Schema(
                {
                    vol.Required("preset", default=_NEW_PRESET): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                *(
                                    {"value": name, "label": name}
                                    for name in names
                                ),
                                {
                                    "value": _NEW_PRESET,
                                    "label": "+ Create new preset",
                                },
                            ]
                        )
                    ),
                }
            ),
        )

    def _preset_edit_schema(self, existing: dict) -> vol.Schema:
        fields: dict = {}
        if self._preset_is_new:
            fields[vol.Required("preset_name")] = TextSelector()
        for key in _TOGGLE_PRESET_KEYS:
            if key in existing:
                default = "on" if existing[key] else "off"
            else:
                default = "unchanged"
            fields[vol.Required(key, default=default)] = SelectSelector(
                SelectSelectorConfig(options=["unchanged", "on", "off"])
            )
        for key in MEMBER_KEYS:
            fields[
                vol.Required(f"{key}_include", default=key in existing)
            ] = BooleanSelector()
            fields[
                vol.Optional(key, default=list(existing.get(key) or []))
            ] = _light_selector()
        if not self._preset_is_new:
            fields[vol.Optional("delete_preset", default=False)] = BooleanSelector()
        return vol.Schema(fields)

    async def async_step_presets_edit(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        store = await async_get_preset_store(self.hass)
        existing = (
            {} if self._preset_is_new else (store.get(self._preset_name or "") or {})
        )
        errors: dict[str, str] = {}

        if user_input is not None:
            if not self._preset_is_new and user_input.get("delete_preset"):
                await store.async_delete_preset(self._preset_name or "")
                return await self.async_step_init()

            name = self._preset_name
            if self._preset_is_new:
                name = (user_input.get("preset_name") or "").strip()
                if not name:
                    errors["preset_name"] = "preset_name_required"
            if not errors:
                # Build the SPARSE preset: a field only lands in the
                # dict when the user chose to include it.
                preset: dict = {}
                for key in _TOGGLE_PRESET_KEYS:
                    choice = user_input.get(key, "unchanged")
                    if choice != "unchanged":
                        preset[key] = choice == "on"
                for key in MEMBER_KEYS:
                    if user_input.get(f"{key}_include"):
                        preset[key] = list(user_input.get(key) or [])
                await store.async_save_preset(name or "", preset)
                return await self.async_step_init()

        return self.async_show_form(
            step_id="presets_edit",
            data_schema=self._preset_edit_schema(existing),
            errors=errors,
        )
