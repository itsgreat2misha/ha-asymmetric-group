"""Per-group runtime state for Asymmetric Group.

A GroupRuntime holds everything that can change *without* editing the
config entry: the dimension toggles plus the Enforce power scope
toggle, the four subgroup member lists (as possibly modified by
presets) and ``last_applied_preset``.

Runtime state is seeded from the config entry at setup and persisted in
a per-entry Store, so preset-applied member sets survive a restart.
Editing the entry's options clears that Store, re-seeding from config.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections.abc import Callable

from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from . import engine
from .const import (
    CONF_ALL_ENTITIES,
    CONF_ASYMMETRIC_ATTRIBUTES,
    CONF_ASYMMETRIC_POWER,
    CONF_ASYMMETRIC_STATE,
    CONF_BASE_MEMBERS,
    CONF_ENFORCE_POWER_SCOPE,
    CONF_HIDE_MEMBERS,
    CONF_NAME,
    ENTRY_STORE_VERSION,
    MEMBER_KEYS,
    TOGGLE_ATTRIBUTES,
    TOGGLE_ENFORCE,
    TOGGLE_POWER,
    TOGGLE_STATE,
    entry_store_key,
)

# config default-toggle key -> runtime toggle key
_CONFIG_TOGGLE_MAP = {
    CONF_ASYMMETRIC_POWER: TOGGLE_POWER,
    CONF_ASYMMETRIC_ATTRIBUTES: TOGGLE_ATTRIBUTES,
    CONF_ASYMMETRIC_STATE: TOGGLE_STATE,
    CONF_ENFORCE_POWER_SCOPE: TOGGLE_ENFORCE,
}

_LOGGER = logging.getLogger(__name__)


class GroupRuntime:
    """Mutable runtime state for one Asymmetric Group config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str, config: dict) -> None:
        self.hass = hass
        self.entry_id = entry_id
        # Merged config entry data+options; treated as read-only here.
        self.config = config
        self._store: Store = Store(
            hass, ENTRY_STORE_VERSION, entry_store_key(entry_id)
        )
        self._listeners: list[Callable[[], None]] = []
        # entity_id of the group's light entity, set once it is added.
        self.entity_id: str | None = None
        # True once the light entity is live. Enforcement only acts on
        # changes made after setup — never during load/restore — so a
        # restart can never strike or kill lights on its own.
        self._live = False

        # --- seed from config -------------------------------------------
        self.toggles: dict[str, bool] = {
            toggle_key: bool(config.get(config_key, False))
            for config_key, toggle_key in _CONFIG_TOGGLE_MAP.items()
        }
        self.subgroups: dict[str, list[str]] = {
            key: list(config.get(key) or []) for key in MEMBER_KEYS
        }
        self.last_applied_preset: str | None = None

    # ------------------------------------------------------------------
    # Plain config accessors
    @property
    def name(self) -> str:
        return self.config.get(CONF_NAME) or "Asymmetric Group"

    @property
    def base_members(self) -> list[str]:
        return list(self.config.get(CONF_BASE_MEMBERS) or [])

    @property
    def hide_members(self) -> bool:
        return bool(self.config.get(CONF_HIDE_MEMBERS, False))

    @property
    def all_entities(self) -> bool:
        return bool(self.config.get(CONF_ALL_ENTITIES, False))

    # ------------------------------------------------------------------
    # Persistence
    def _state_dict(self) -> dict:
        """Runtime state in engine/snapshot shape."""
        return {
            "toggles": dict(self.toggles),
            "subgroups": copy.deepcopy(self.subgroups),
            "last_applied_preset": self.last_applied_preset,
        }

    async def async_load(self) -> None:
        """Overlay persisted runtime state (if any) onto the config seed."""
        data = await self._store.async_load()
        if not isinstance(data, dict):
            return
        toggles = data.get("toggles")
        if isinstance(toggles, dict):
            for key in self.toggles:
                if key in toggles:
                    self.toggles[key] = bool(toggles[key])
        subgroups = data.get("subgroups")
        if isinstance(subgroups, dict):
            base = set(self.base_members)
            for key in MEMBER_KEYS:
                if key in subgroups:
                    # Keep only members that are still in base.
                    self.subgroups[key] = [
                        m for m in subgroups[key] or [] if m in base
                    ]
        if "last_applied_preset" in data:
            self.last_applied_preset = data.get("last_applied_preset")

    async def async_save(self) -> None:
        await self._store.async_save(self._state_dict())

    async def async_clear_store(self) -> None:
        """Drop persisted runtime state so the next setup re-seeds."""
        # Store.async_remove is safe when no file exists yet.
        await self._store.async_remove()

    # ------------------------------------------------------------------
    # Listeners (entities subscribe to runtime changes)
    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(callback)

        def _remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return _remove

    def _notify(self) -> None:
        for callback in list(self._listeners):
            callback()

    # ------------------------------------------------------------------
    # Mutations (each persists + notifies, then enforces scope)
    async def async_set_toggle(self, toggle_key: str, value: bool) -> None:
        if self.toggles.get(toggle_key) == value:
            self._notify()
            return
        old_power_on = self.effective_sets()["power_on"]
        self.toggles[toggle_key] = bool(value)
        await self.async_save()
        self._notify()
        await self._async_enforce_power_scope(old_power_on)

    def _apply_sparse(self, data: dict) -> None:
        new_state = engine.apply_sparse_preset(
            self._state_dict(), data, self.base_members
        )
        self.toggles = {k: bool(v) for k, v in new_state["toggles"].items()}
        self.subgroups = new_state["subgroups"]

    async def async_apply_preset(self, preset: dict, preset_name: str) -> None:
        """Apply a sparse preset and record it as last applied.

        Only the ``last_applied_preset`` label is recorded — there is
        deliberately no drift/Custom tracking: later manual changes do
        not clear the label.
        """
        old_power_on = self.effective_sets()["power_on"]
        self._apply_sparse(preset)
        self.last_applied_preset = preset_name
        await self.async_save()
        self._notify()
        await self._async_enforce_power_scope(old_power_on)

    async def async_apply_sparse(self, data: dict) -> None:
        """Apply a sparse settings dict (toggles and/or member sets).

        Used by the set_members service: exactly the keys present in
        ``data`` change; the last_applied_preset label is untouched
        because this was a direct edit, not a preset application.
        """
        old_power_on = self.effective_sets()["power_on"]
        self._apply_sparse(data)
        await self.async_save()
        self._notify()
        await self._async_enforce_power_scope(old_power_on)

    # ------------------------------------------------------------------
    # Scope enforcement (Enforce power scope toggle)
    #
    # Settle behaviour: a scope change does not return until the
    # lights enforcement touched have *registered* their new states,
    # so a caller sequencing other work after the change (an
    # automation arming the scope before flipping other integrations'
    # switches, say) gets true ordering instead of racing in-flight
    # state updates. The wait is event-driven and per target:
    #   - already in the desired state, or unavailable/unknown when
    #     the wait starts: skipped instantly, silently;
    #   - reaches the desired state: resolved;
    #   - drops to unavailable/unknown mid-wait, or is still reporting
    #     the old state a grace period after its command (alive but
    #     not complying): dropped from the wait and named in a single
    #     aggregated warning for the whole scope change;
    #   - never reports at all: ended by the backstop timeout and
    #     named in the same warning. The backstop is a safety net
    #     only — the event-driven drops end real-world waits.
    _SETTLE_BACKSTOP_SECONDS = 10.0
    _SETTLE_NONCOMPLIANCE_GRACE_SECONDS = 2.0

    async def _async_enforce_power_scope(self, old_power_on: list[str]) -> None:
        """Reconcile lit members after the effective power-ON set changed.

        No-ops unless the group is live, the enforce toggle is on and
        the effective set actually changed. The plan comes from the
        pure engine (see plan_enforcement): members that fell out of
        the set and are on are turned off; members that entered the
        set are turned on only if the group was lit through its old
        scope. Enforcement issues plain light calls — it never changes
        the scope itself, so it cannot re-trigger. Before returning,
        the change waits until those calls have settled (see the
        settle rules above), so the mutation callers — the toggle
        switches, apply_preset, set_members — all hand their callers a
        fully-applied scope change.
        """
        if not self._live or not self.toggles.get(TOGGLE_ENFORCE):
            return
        new_power_on = self.effective_sets()["power_on"]
        if set(new_power_on) == set(old_power_on):
            return
        currently_on = set()
        for member in self.base_members:
            state = self.hass.states.get(member)
            if state is not None and state.state == "on":
                currently_on.add(member)
        to_turn_off, to_turn_on = engine.plan_enforcement(
            old_power_on, new_power_on, currently_on
        )
        targets: dict[str, str] = {}
        if to_turn_off:
            await self.hass.services.async_call(
                "light",
                "turn_off",
                {"entity_id": to_turn_off},
                blocking=False,
            )
            targets.update({member: "off" for member in to_turn_off})
        if to_turn_on:
            await self.hass.services.async_call(
                "light",
                "turn_on",
                {"entity_id": to_turn_on},
                blocking=False,
            )
            targets.update({member: "on" for member in to_turn_on})
        if targets:
            await self._async_wait_until_settled(targets)

    async def _async_wait_until_settled(self, targets: dict[str, str]) -> None:
        """Wait until enforcement's calls register, per the rules above."""
        pending: dict[str, str] = {}
        for member, desired in targets.items():
            state = self.hass.states.get(member)
            if state is None or state.state in ("unavailable", "unknown"):
                continue  # dead or unknown light: skip silently
            if state.state != desired:
                pending[member] = desired
        if not pending:
            return

        started = time.monotonic()
        unconfirmed: list[str] = []
        done = asyncio.Event()

        def _evaluate(member: str) -> None:
            """Resolve or drop one pending member from its live state."""
            state = self.hass.states.get(member)
            if state is None:
                return
            if state.state == pending[member]:
                del pending[member]
            elif state.state in ("unavailable", "unknown"):
                unconfirmed.append(member)
                del pending[member]
            elif (
                time.monotonic() - started
                >= self._SETTLE_NONCOMPLIANCE_GRACE_SECONDS
            ):
                # Alive and still reporting the old state well after
                # its command: it is not complying; stop waiting on it.
                unconfirmed.append(member)
                del pending[member]
            if not pending:
                done.set()

        def _on_state_change(event: Event) -> None:
            member = event.data.get("entity_id")
            if member in pending:
                _evaluate(member)

        unsub = async_track_state_change_event(
            self.hass, list(pending), _on_state_change
        )
        try:
            # Close the check/register race: re-evaluate once now that
            # the listener is attached.
            for member in list(pending):
                _evaluate(member)
            if pending:
                await asyncio.wait_for(
                    done.wait(), timeout=self._SETTLE_BACKSTOP_SECONDS
                )
        except TimeoutError:
            unconfirmed.extend(sorted(pending))
            pending.clear()
        finally:
            unsub()
        if unconfirmed:
            _LOGGER.warning(
                "Asymmetric group %s: could not confirm new state for %s "
                "after a scope change; continuing without them",
                self.entity_id or self.name,
                ", ".join(sorted(unconfirmed)),
            )

    def snapshot_preset(self) -> dict:
        """Full (non-sparse) preset snapshot of the current runtime."""
        snapshot: dict = {
            CONF_ASYMMETRIC_POWER: self.toggles[TOGGLE_POWER],
            CONF_ASYMMETRIC_ATTRIBUTES: self.toggles[TOGGLE_ATTRIBUTES],
            CONF_ASYMMETRIC_STATE: self.toggles[TOGGLE_STATE],
            CONF_ENFORCE_POWER_SCOPE: self.toggles[TOGGLE_ENFORCE],
        }
        for key in MEMBER_KEYS:
            snapshot[key] = list(self.subgroups[key])
        return snapshot

    # ------------------------------------------------------------------
    # Resolution for the light entity
    def effective_sets(self) -> dict[str, list[str]]:
        """Resolve effective member sets via the pure engine."""
        engine_config = {CONF_BASE_MEMBERS: self.base_members, **self.subgroups}
        return engine.resolve_sets(engine_config, self.toggles)

    def register_entity_id(self, entity_id: str) -> None:
        """Record the light entity_id so services can find this runtime."""
        self.entity_id = entity_id
        self._live = True
        domain_data = self.hass.data.setdefault(
            "asymmetric_group", {}
        )
        by_entity = domain_data.setdefault("runtimes_by_entity", {})
        by_entity[entity_id] = self

    def unregister_entity_id(self) -> None:
        self._live = False
        if self.entity_id is None:
            return
        by_entity = (
            self.hass.data.get("asymmetric_group", {}).get("runtimes_by_entity", {})
        )
        if by_entity.get(self.entity_id) is self:
            del by_entity[self.entity_id]
        self.entity_id = None
