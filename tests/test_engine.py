"""Unit tests for the pure engine (no Home Assistant required).

engine.py is loaded by file path so the package __init__ (which imports
Home Assistant) is never executed.
"""

import importlib.util
import pathlib
import unittest
from typing import ClassVar

_ENGINE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components"
    / "asymmetric_group"
    / "engine.py"
)
_spec = importlib.util.spec_from_file_location("asymmetric_group_engine", _ENGINE_PATH)
engine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(engine)

BASE = ["light.b1", "light.b2", "light.b3", "light.b4"]
ALL_OFF = {"power": False, "attributes": False, "state": False}
ALL_ON = {"power": True, "attributes": True, "state": True}


def make_config(**overrides):
    config = {
        "base_members": list(BASE),
        "power_on_members": ["light.b3"],
        "power_off_members": list(BASE),
        "attribute_members": ["light.b3"],
        "state_members": ["light.b3"],
        "hide_members": False,
        "all_entities": False,
    }
    config.update(overrides)
    return config


def runtime_state():
    return {
        "toggles": {"power": False, "attributes": True, "state": False},
        "subgroups": {
            "power_on_members": ["light.b1"],
            "power_off_members": list(BASE),
            "attribute_members": list(BASE),
            "state_members": list(BASE),
        },
        "last_applied_preset": None,
    }


class ResolveSetsTest(unittest.TestCase):
    def test_toggles_off_everything_is_base(self):
        sets = engine.resolve_sets(make_config(), ALL_OFF)
        self.assertEqual(sets["power_on"], BASE)
        self.assertEqual(sets["power_off"], BASE)
        self.assertEqual(sets["attributes"], BASE)
        self.assertEqual(sets["state"], BASE)

    def test_toggles_on_use_subgroups(self):
        sets = engine.resolve_sets(make_config(), ALL_ON)
        self.assertEqual(sets["power_on"], ["light.b3"])
        self.assertEqual(sets["power_off"], BASE)
        self.assertEqual(sets["attributes"], ["light.b3"])
        self.assertEqual(sets["state"], ["light.b3"])

    def test_mixed_toggles_are_independent(self):
        toggles = {"power": True, "attributes": False, "state": True}
        sets = engine.resolve_sets(make_config(), toggles)
        self.assertEqual(sets["power_on"], ["light.b3"])
        self.assertEqual(sets["attributes"], BASE)
        self.assertEqual(sets["state"], ["light.b3"])

    def test_subgroups_filtered_to_base_and_deduped(self):
        config = make_config(
            power_on_members=["light.b3", "light.nope", "light.b3", "light.b2"]
        )
        sets = engine.resolve_sets(config, ALL_ON)
        self.assertEqual(sets["power_on"], ["light.b3", "light.b2"])

    def test_missing_keys_default_to_base(self):
        sets = engine.resolve_sets({"base_members": BASE}, ALL_ON)
        self.assertEqual(sets["power_on"], [])
        # subgroup absent in config -> empty subgroup when toggle on;
        # but a config produced by the flow always carries the keys.
        sets_off = engine.resolve_sets({"base_members": BASE}, ALL_OFF)
        self.assertEqual(sets_off["state"], BASE)


class PlanTurnOnTest(unittest.TestCase):
    """The single-lamp scenario: asymmetric power+attributes on, only
    light 03 turns on, 02 was switched on manually, group OFF still
    sweeps everything (power_off = base, checked via resolve_sets)."""

    def _sets(self, attribute_members):
        config = make_config(attribute_members=attribute_members)
        toggles = {"power": True, "attributes": True, "state": True}
        return engine.resolve_sets(config, toggles)

    def test_single_lamp_scenario_on_lamp_adjusted_not_reactivated(self):
        sets = self._sets(["light.b3"])
        activate, attribute_only = engine.plan_turn_on(
            sets, currently_on={"light.b2", "light.b3"}, has_attributes=True
        )
        # 03 is the only Power/Attribute member and is already on:
        # nothing needs switching on, and 03 receives the payload
        # via the attribute-only call. (Before the intersection
        # model this asserted activate == [b3]; the observable
        # behaviour — 03 gets the payload, 02 untouched — is the
        # same, only the internal call partition changed.)
        self.assertEqual(activate, [])
        # 02 is on but not in the attribute scope -> untouched.
        self.assertEqual(attribute_only, ["light.b3"])
        self.assertNotIn("light.b2", activate + attribute_only)

    def test_single_lamp_scenario_attribute_scope_widened(self):
        sets = self._sets(["light.b2", "light.b3"])
        activate, attribute_only = engine.plan_turn_on(
            sets, currently_on={"light.b2", "light.b3"}, has_attributes=True
        )
        # Both attribute members are already on, so nothing is
        # activated; both receive the payload via the attribute call.
        self.assertEqual(activate, [])
        self.assertEqual(attribute_only, ["light.b2", "light.b3"])

    def test_no_attributes_no_attribute_only(self):
        sets = self._sets(["light.b2", "light.b3"])
        activate, attribute_only = engine.plan_turn_on(
            sets, currently_on={"light.b2", "light.b3"}, has_attributes=False
        )
        self.assertEqual(activate, ["light.b3"])
        self.assertEqual(attribute_only, [])

    def test_attribute_member_off_stays_off(self):
        sets = self._sets(["light.b2", "light.b3"])
        activate, attribute_only = engine.plan_turn_on(
            sets, currently_on={"light.b3"}, has_attributes=True
        )
        # 02 is in the attribute scope but OFF and outside the Power
        # set -> never activated by an attribute change, and not in
        # either call. 03 is already on and receives the payload via
        # the attribute-only call.
        self.assertEqual(activate, [])
        self.assertEqual(attribute_only, ["light.b3"])
        self.assertNotIn("light.b2", activate + attribute_only)

    def test_turn_off_set_is_base(self):
        sets = self._sets(["light.b3"])
        self.assertEqual(sets["power_off"], BASE)


class AttributeIntersectionTest(unittest.TestCase):
    """Attribute-bearing turn_on: activation is Power \u2229 Attribute.

    Owner scenario: Power-ON = [b1, b2, b3], Attribute = [b2, b3].
    A brightness call touches only the Attribute set: members in both
    sets are switched on with the payload, Attribute-only members are
    adjusted only if already on, and Power-only members are not
    touched at all.
    """

    def _sets(self, attribute_members, toggles=None):
        config = make_config(
            power_on_members=["light.b1", "light.b2", "light.b3"],
            attribute_members=attribute_members,
        )
        return engine.resolve_sets(config, toggles or ALL_ON)

    def test_owner_scenario(self):
        sets = self._sets(["light.b2", "light.b3"])
        activate, attribute_only = engine.plan_turn_on(
            sets, currently_on={"light.b3"}, has_attributes=True
        )
        # b2 is in both sets and off: switched on with the payload.
        # b3 is already on: it only needs the payload, so it rides
        # the attribute-only call. b1 (Power only) is untouched.
        self.assertEqual(activate, ["light.b2"])
        self.assertEqual(attribute_only, ["light.b3"])
        self.assertNotIn("light.b1", activate + attribute_only)

    def test_owner_scenario_attribute_only_extra(self):
        # Attribute set widened to a light outside the Power set:
        # b4 already on receives the payload without being activated;
        # b1 (Power only) is untouched.
        sets = self._sets(["light.b2", "light.b3", "light.b4"])
        activate, attribute_only = engine.plan_turn_on(
            sets, currently_on={"light.b3", "light.b4"}, has_attributes=True
        )
        self.assertEqual(activate, ["light.b2"])
        self.assertEqual(attribute_only, ["light.b3", "light.b4"])
        self.assertNotIn("light.b1", activate + attribute_only)

    def test_power_only_member_on_stays_untouched(self):
        sets = self._sets(["light.b2", "light.b3"])
        activate, attribute_only = engine.plan_turn_on(
            sets, currently_on={"light.b1", "light.b3"}, has_attributes=True
        )
        self.assertEqual(activate, ["light.b2"])
        self.assertEqual(attribute_only, ["light.b3"])
        self.assertNotIn("light.b1", activate + attribute_only)

    def test_attribute_only_member_off_stays_off(self):
        sets = self._sets(["light.b2", "light.b3", "light.b4"])
        activate, attribute_only = engine.plan_turn_on(
            sets, currently_on={"light.b3"}, has_attributes=True
        )
        self.assertEqual(activate, ["light.b2"])
        self.assertEqual(attribute_only, ["light.b3"])
        self.assertNotIn("light.b4", activate + attribute_only)

    def test_attributes_toggle_off_collapses_to_native(self):
        toggles = {"power": True, "attributes": False, "state": True}
        sets = self._sets(["light.b2", "light.b3"], toggles=toggles)
        activate, attribute_only = engine.plan_turn_on(
            sets, currently_on={"light.b3"}, has_attributes=True
        )
        # Attribute set resolves to the full base set, so every
        # Power-ON member receives the payload and the off ones are
        # switched on: native behaviour. (b3 is already on, so it
        # rides the attribute-only call.)
        self.assertEqual(activate, ["light.b1", "light.b2"])
        self.assertEqual(attribute_only, ["light.b3"])
        self.assertEqual(
            sorted(activate + attribute_only),
            ["light.b1", "light.b2", "light.b3"],
        )

    def test_bare_turn_on_activates_full_power_set(self):
        sets = self._sets(["light.b2", "light.b3"])
        activate, attribute_only = engine.plan_turn_on(
            sets, currently_on={"light.b3"}, has_attributes=False
        )
        self.assertEqual(activate, ["light.b1", "light.b2", "light.b3"])
        self.assertEqual(attribute_only, [])


class ComputeGroupStateTest(unittest.TestCase):
    def test_any_mode(self):
        c = engine.compute_group_state
        self.assertEqual(c(["on", "off"], False), "on")
        self.assertEqual(c(["off", "off"], False), "off")
        self.assertEqual(c(["unavailable", "unavailable"], False), "unavailable")
        self.assertEqual(c(["unknown", "unavailable"], False), "unknown")
        self.assertEqual(c([None, None], False), "unknown")
        self.assertEqual(c(["unknown", "off"], False), "off")
        self.assertEqual(c(["unavailable", "on"], False), "on")
        self.assertEqual(c([None, "off"], False), "off")

    def test_all_mode(self):
        c = engine.compute_group_state
        self.assertEqual(c(["on", "on"], True), "on")
        self.assertEqual(c(["on", "off"], True), "off")
        self.assertEqual(c(["unavailable", "unavailable"], True), "unavailable")
        self.assertEqual(c(["unknown", "on"], True), "unknown")
        self.assertEqual(c(["unavailable", "on"], True), "unknown")
        self.assertEqual(c([None, "on"], True), "unknown")
        self.assertEqual(c(["off", "off"], True), "off")

    def test_empty_is_unknown(self):
        self.assertEqual(engine.compute_group_state([], False), "unknown")
        self.assertEqual(engine.compute_group_state([], True), "unknown")


class ApplySparsePresetTest(unittest.TestCase):
    def test_absent_keys_untouched(self):
        state = runtime_state()
        new = engine.apply_sparse_preset(state, {}, BASE)
        self.assertEqual(new, state)

    def test_explicit_false_applied(self):
        state = runtime_state()
        new = engine.apply_sparse_preset(state, {"asymmetric_attributes": False}, BASE)
        self.assertFalse(new["toggles"]["attributes"])
        # other toggles unchanged
        self.assertFalse(new["toggles"]["power"])
        self.assertFalse(new["toggles"]["state"])

    def test_explicit_true_applied(self):
        state = runtime_state()
        new = engine.apply_sparse_preset(state, {"asymmetric_power": True}, BASE)
        self.assertTrue(new["toggles"]["power"])

    def test_empty_member_list_applied(self):
        state = runtime_state()
        new = engine.apply_sparse_preset(state, {"power_on_members": []}, BASE)
        self.assertEqual(new["subgroups"]["power_on_members"], [])
        self.assertEqual(new["subgroups"]["state_members"], BASE)

    def test_members_filtered_to_base(self):
        state = runtime_state()
        new = engine.apply_sparse_preset(
            state, {"state_members": ["light.b2", "light.nope", "light.b2"]}, BASE
        )
        self.assertEqual(new["subgroups"]["state_members"], ["light.b2"])

    def test_input_not_mutated(self):
        state = runtime_state()
        engine.apply_sparse_preset(
            state, {"asymmetric_power": True, "power_on_members": []}, BASE
        )
        self.assertFalse(state["toggles"]["power"])
        self.assertEqual(state["subgroups"]["power_on_members"], ["light.b1"])

    def test_last_applied_preset_not_in_engine_scope(self):
        state = runtime_state()
        new = engine.apply_sparse_preset(state, {"asymmetric_power": True}, BASE)
        self.assertIsNone(new["last_applied_preset"])


class PlanEnforcementTest(unittest.TestCase):
    OLD: ClassVar[list[str]] = ["light.a", "light.b", "light.c", "light.d"]
    NEW: ClassVar[list[str]] = ["light.c"]

    def test_narrow_turns_off_removed_that_are_on(self):
        off, on = engine.plan_enforcement(self.OLD, self.NEW, {"light.a", "light.c"})
        self.assertEqual(off, ["light.a"])
        self.assertEqual(on, [])

    def test_narrow_dark_changes_nothing(self):
        off, on = engine.plan_enforcement(self.OLD, self.NEW, set())
        self.assertEqual((off, on), ([], []))

    def test_widen_turns_on_added_when_old_scope_lit(self):
        off, on = engine.plan_enforcement(self.NEW, self.OLD, {"light.c"})
        self.assertEqual(off, [])
        self.assertEqual(on, ["light.a", "light.b", "light.d"])

    def test_widen_dark_stages_only(self):
        off, on = engine.plan_enforcement(self.NEW, self.OLD, set())
        self.assertEqual((off, on), ([], []))

    def test_manual_excluded_light_does_not_count_as_lit(self):
        # light.x is on but in neither scope: widening must not strike
        # the added members, and the manual light is left alone.
        off, on = engine.plan_enforcement(self.NEW, self.OLD, {"light.x"})
        self.assertEqual((off, on), ([], []))

    def test_manual_excluded_light_removed_by_narrowing(self):
        # Widen first (scope = all, light.b manually on), then narrow:
        # b fell out of the set while on -> turned off.
        off, on = engine.plan_enforcement(self.OLD, self.NEW, {"light.b"})
        self.assertEqual(off, ["light.b"])
        self.assertEqual(on, [])

    def test_swap_off_then_on(self):
        off, on = engine.plan_enforcement(
            ["light.a"], ["light.b"], {"light.a"}
        )
        self.assertEqual(off, ["light.a"])
        self.assertEqual(on, ["light.b"])

    def test_added_already_on_not_retriggered(self):
        _off, on = engine.plan_enforcement(self.NEW, self.OLD, {"light.c", "light.a"})
        self.assertEqual(on, ["light.b", "light.d"])

    def test_enforce_toggle_in_sparse_preset(self):
        state = runtime_state()
        new = engine.apply_sparse_preset(state, {"enforce_power_scope": True}, BASE)
        self.assertTrue(new["toggles"]["enforce"])
        new = engine.apply_sparse_preset(new, {"enforce_power_scope": False}, BASE)
        self.assertFalse(new["toggles"]["enforce"])


class MeanHelpersTest(unittest.TestCase):
    def test_mean_int(self):
        self.assertEqual(engine.mean_int([100, 101]), 100)
        self.assertEqual(engine.mean_int([0, 255]), 128)
        self.assertIsNone(engine.mean_int([]))
        self.assertEqual(engine.mean_int([42]), 42)

    def test_mean_float(self):
        self.assertAlmostEqual(engine.mean_float([1.0, 2.0]), 1.5)
        self.assertIsNone(engine.mean_float([]))


if __name__ == "__main__":
    unittest.main()
