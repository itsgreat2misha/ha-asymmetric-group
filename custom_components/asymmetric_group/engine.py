"""Pure resolution engine for Asymmetric Group.

This module intentionally has **no Home Assistant imports** so it can be
unit-tested standalone. All HA-facing code translates its data into the
plain dicts used here.

Vocabulary
----------
config dict keys:
    base_members, power_on_members, power_off_members, attribute_members,
    state_members, hide_members, all_entities

toggle dict keys:
    power, attributes, state, enforce  (bools)

runtime state dict (used by apply_sparse_preset / snapshots):
    {
        "toggles": {"power": bool, "attributes": bool, "state": bool},
        "subgroups": {
            "power_on_members": [...], "power_off_members": [...],
            "attribute_members": [...], "state_members": [...],
        },
        "last_applied_preset": str | None,
    }

preset dict (SPARSE):
    Keys ``asymmetric_power`` / ``asymmetric_attributes`` /
    ``asymmetric_state`` / ``enforce_power_scope`` for toggles and the
    four ``*_members`` keys for member lists. A key that is *present*
    is applied — including an explicit ``False`` or an empty list. A
    key that is *absent* leaves the current value untouched.
"""

from __future__ import annotations

import copy

MEMBER_KEYS = (
    "power_on_members",
    "power_off_members",
    "attribute_members",
    "state_members",
)

# preset toggle key -> runtime toggle key
_PRESET_TOGGLE_MAP = {
    "asymmetric_power": "power",
    "asymmetric_attributes": "attributes",
    "asymmetric_state": "state",
    "enforce_power_scope": "enforce",
}


def _dedup(items):
    """Order-preserving de-duplication."""
    seen = set()
    out = []
    for item in items or []:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _filtered(subgroup, base):
    """Subgroup intersected with base: subgroup order kept, deduped."""
    base_set = set(base)
    return _dedup([item for item in subgroup or [] if item in base_set])


def resolve_sets(config, toggles):
    """Resolve the effective member set for each dimension.

    Returns a dict with keys ``power_on``, ``power_off``, ``attributes``
    and ``state``. When a dimension's toggle is off, that dimension
    resolves to the full base set; when on, to its (base-filtered)
    subgroup.
    """
    base = _dedup(config.get("base_members"))
    toggles = toggles or {}
    return {
        "power_on": (
            _filtered(config.get("power_on_members"), base)
            if toggles.get("power")
            else list(base)
        ),
        "power_off": (
            _filtered(config.get("power_off_members"), base)
            if toggles.get("power")
            else list(base)
        ),
        "attributes": (
            _filtered(config.get("attribute_members"), base)
            if toggles.get("attributes")
            else list(base)
        ),
        "state": (
            _filtered(config.get("state_members"), base)
            if toggles.get("state")
            else list(base)
        ),
    }


def plan_turn_on(sets, currently_on, has_attributes):
    """Plan a turn_on call as (activate, attribute_only).

    A bare turn_on (``has_attributes`` false) activates the full
    power-ON set and ``attribute_only`` is empty.

    A turn_on carrying an attribute payload touches only the
    attribute set, partitioned by power membership:

    - ``activate`` is the members in *both* the power-ON and
      attribute sets that are currently off: they are switched on
      with the payload. Waking a light with an attribute change
      requires membership in both sets.
    - ``attribute_only`` is the attribute-set members that are
      already on (in either power arrangement): they receive the
      payload without a power change — attribute application never
      activates a light outside the intersection.
    - Power-ON members outside the attribute set are not touched
      by an attribute-bearing call at all, whether on or off.

    When the attributes toggle is off the attribute set resolves
    to the full base set, the intersection is the whole power-ON
    set, and an attribute call behaves exactly like a native
    group turn_on. The caller issues at most these two batched
    service calls.
    """
    power_on = list(sets.get("power_on") or [])
    if not has_attributes:
        return power_on, []
    currently_on = set(currently_on or ())
    attribute_members = list(sets.get("attributes") or [])
    attribute_set = set(attribute_members)
    activate = [
        member
        for member in power_on
        if member in attribute_set and member not in currently_on
    ]
    attribute_only = [member for member in attribute_members if member in currently_on]
    return activate, attribute_only


def compute_group_state(member_states, all_entities):
    """Compute group state from member states, mirroring core groups.

    ``member_states`` holds state strings ("on"/"off"/"unknown"/
    "unavailable") or None for a missing state (treated as unknown).

    any-mode (all_entities=False):
        unavailable if all members unavailable; unknown if all members
        unknown/unavailable; on if at least one on; else off.
    all-mode (all_entities=True):
        unavailable if all members unavailable; unknown if at least one
        member unknown/unavailable; on if all members on; else off.
    """
    states = [state if state is not None else "unknown" for state in member_states]
    if not states:
        return "unknown"

    if all(state == "unavailable" for state in states):
        return "unavailable"

    if all_entities:
        if any(state in ("unknown", "unavailable") for state in states):
            return "unknown"
        return "on" if all(state == "on" for state in states) else "off"

    if all(state in ("unknown", "unavailable") for state in states):
        return "unknown"
    return "on" if any(state == "on" for state in states) else "off"


def apply_sparse_preset(runtime_state, preset, base):
    """Apply a sparse preset dict to a runtime state dict.

    Returns a *new* runtime state dict; the input is not mutated.
    Present keys are applied (explicit False and empty member lists
    included); absent keys are left unchanged. Member lists are
    filtered to ``base``.
    """
    new_state = copy.deepcopy(runtime_state)
    new_state.setdefault("toggles", {})
    new_state.setdefault("subgroups", {})
    preset = preset or {}
    base = _dedup(base)

    for preset_key, toggle_key in _PRESET_TOGGLE_MAP.items():
        if preset_key in preset:
            new_state["toggles"][toggle_key] = bool(preset[preset_key])

    for member_key in MEMBER_KEYS:
        if member_key in preset:
            new_state["subgroups"][member_key] = _filtered(preset[member_key], base)

    return new_state


def plan_enforcement(old_power_on, new_power_on, currently_on):
    """Plan scope enforcement as (to_turn_off, to_turn_on).

    Called when the *effective* power-ON set changes (toggle flip,
    member edit, preset apply) and the group's Enforce power scope
    toggle is on. The reconciliation is anchored to the OLD scope:

    - Members that fell out of the set (old - new) and are currently
      on are turned off — unconditionally: if one is lit, the group
      was lit through its old scope by definition.
    - Members that entered the set (new - old) are turned on only
      when at least one OLD-scope member is currently on — i.e. the
      group was actually lit through its old scope. When nothing in
      the old scope is on the group is effectively dark, so the new
      scope is only staged, never struck. A light that is on but was
      in *neither* scope (turned on manually while excluded) does not
      count toward "was on" and is left alone.

    ``currently_on`` is the set of member entity_ids whose state is
    exactly "on"; unknown/unavailable members are treated as off.
    """
    old_set = set(old_power_on or [])
    new_set = set(new_power_on or [])
    on = set(currently_on or ())
    to_turn_off = [m for m in old_power_on or [] if m in old_set - new_set and m in on]
    was_on = any(m in on for m in old_set)
    to_turn_on = []
    if was_on:
        to_turn_on = [
            m for m in new_power_on or [] if m in new_set - old_set and m not in on
        ]
    return to_turn_off, to_turn_on


def mean_int(values):
    """Rounded mean of a list of ints, or None when empty."""
    values = list(values or [])
    if not values:
        return None
    return round(sum(values) / len(values))


def mean_float(values):
    """Mean of a list of numbers, or None when empty."""
    values = list(values or [])
    if not values:
        return None
    return sum(values) / len(values)
