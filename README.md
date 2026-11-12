# Asymmetric Group

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)

A Home Assistant helper for light groups where different lights can do different jobs: one set turns on, another turns off, another takes brightness and colour changes, and another decides whether the group shows as "on". Each of those can be switched on or off at any time.

In a regular light group, the same lights do everything. That makes a common wish impossible:

> In the evening, "turn on the bedroom lights" should light just one lamp — but "turn off the bedroom lights" must switch **everything** off, including a lamp someone turned on by hand.

With an Asymmetric Group that's just how the group is set up, so it works the same everywhere: voice assistants, dashboards, wall switches, and automations all talk to the group itself.

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=itsgreat2misha&repository=ha-asymmetric-group&category=integration)

1. In **HACS**, open **⋮ → Custom repositories** and add `https://github.com/itsgreat2misha/ha-asymmetric-group` as an **Integration** (or use the My Home Assistant button above).
2. Install **Asymmetric Group** from HACS and restart Home Assistant.
3. Go to **Settings → Devices & services → Helpers → Create helper → Asymmetric Group**.

Requires Home Assistant 2024.5.0 or newer.

## Upgrading

Update through HACS as usual: **HACS → Asymmetric Group → Update**, then restart Home Assistant when prompted. Group configurations, presets, and runtime switch positions are stored by Home Assistant and survive upgrades; no re-setup is needed.

## Uninstalling

1. Delete each Asymmetric Group helper (**Settings → Devices & services → Helpers**, open the group, **⋮ → Delete**). Deleting a group removes its group light and its four switches; the member lights themselves are untouched (any members hidden by the group are shown again).
2. In **HACS**, open **Asymmetric Group → ⋮ → Remove**, then restart Home Assistant.
3. Optionally remove the custom repository from **HACS → ⋮ → Custom repositories** afterwards.

The shared preset library is stored under the integration's storage; removing the integration leaves that file behind harmlessly in `.storage` (it is only read while the integration is installed).

## Setting up a group

1. **Name** the group and choose its **base members**: every light that should ever be controlled or watched through this group. Each of the smaller sets below is picked from these lights.
2. **Choose which behaviours should be asymmetric.** For each one you turn on, you'll be asked which lights it applies to:
   - **Power** — separate light sets for turning on and turning off.
   - **Attributes** — the lights that receive brightness and colour changes.
   - **State** — the lights that decide whether the group counts as "on".
3. Optionally enable **Hide members** (hide the individual lights, like a regular group), **All entities** (the group is only "on" when *all* of its state lights are on), and **Enforce power scope** (when the Power-ON set changes, reconcile lights that are already on — see *Enforce power scope* below).

You can change all of this later under the group's **Configure** button. Settings for behaviours you didn't enable simply use all the base lights.

## How it works

Each behaviour has its own switch on the group's device page — **Asymmetric power**, **Asymmetric attributes**, and **Asymmetric state** — plus a fourth, **Enforce power scope**, described below. While a switch is off, that behaviour uses all the base lights, exactly like a regular group. Turn it on, and that behaviour uses its own set instead.

| Behaviour | Switch on | Switch off |
|---|---|---|
| Power | Turn-on uses the Power-ON lights; turn-off uses the Power-OFF lights | Both use all base lights |
| Attributes | Brightness/colour changes go to the Attribute lights | Changes go to all base lights |
| State | The group's on/off state comes from the State lights | It comes from all base lights |

The brightness and colour the group *reports* are averaged from the Attribute lights that are on — if no Attribute light is on but the group is on another way, they come from whichever base lights are lit (see *Good to know*).

Two guarantees hold at all times:

- **Changing brightness or colour touches only the Attribute lights.** When a brightness or colour change reaches the group, the lights it can affect are the Attribute set: an Attribute light that is also in the Power-ON set and is off is switched on with the new values, an Attribute light that is already on is adjusted in place, and a Power-ON light that is *not* in the Attribute set is left completely alone — if it's off it stays off, if it's on it keeps its level. Switching a light on with a new brightness or colour takes membership in both sets. A turn-on with no brightness or colour still switches on the full Power-ON set — and a turn-on whose only setting is a transition counts as a plain turn-on.
- **The group's light list always shows all base lights** (in its `entity_id` attribute). One template exception: `expand()` does not expand Asymmetric Groups — see *Good to know* below. Extra attributes (`effective_power_on_members`, `effective_power_off_members`, `effective_attribute_members`, `effective_state_members`) show which sets are currently in effect.

A member can itself be a light group — a regular group or another Asymmetric Group. Like core groups, a group member is **atomic**: it sits in each set as a single member, is switched on/off and given attributes as one entity, and contributes its own combined state to the group's state and its own brightness to the reported brightness, each through the relevant set. Membership of the subgroup is edited in one place — its own helper — and every group containing it follows. The one arrangement that's rejected is a cycle: a group containing itself, directly or through another group.

### Enforce power scope

**Enforce power scope** (off by default) applies a change to the Power-ON set right away, instead of only filtering the next turn-on:

- Lights that dropped out of the set and are on are turned off.
- Lights that joined the set are turned on — but only if at least one light from the old set is on. If no old-set light is on, nothing turns on; the new set just takes effect next time.
- A light that is in neither the old nor the new set (turned on by hand) is left alone, and doesn't count as the group being lit.

This happens when you flip the Asymmetric power switch, edit the sets, apply a preset, or call `set_members` — never on startup. The change waits until the affected lights have actually switched (up to 10 seconds), so automations that use it at the start of a scene run in order. A light that is already offline is skipped silently; a light whose new state can't be confirmed is dropped from the wait and named once in the log.

Example: a "Movie night" scene narrows the group to one lamp. With enforcement on, the room's other lit lights switch off as the scene starts, instead of staying on until the next turn-off.

## Controlling groups from automations

Everything about a group can be driven from automations and scripts:

- **Flip a behaviour on or off** with the group's switches, e.g. an "evening mode" automation turns on *Asymmetric power* and from then on the group turns on partially but always turns off fully.
- **Change sets and switches on the fly** with the `asymmetric_group.set_members` service. Only the fields you include are changed:

```yaml
action: asymmetric_group.set_members
data:
  entity_id: light.bedroom_lights
  asymmetric_state: true
  state_members:
    - light.bedroom_3
```

- **Presets** (below) can be applied, saved, and deleted with services, and the whole preset library can be read into a script variable — see the services list.

### Services

| Service | What it does |
|---|---|
| `asymmetric_group.set_members` | Change a group's switches and light sets. Only the fields you pass are changed. |
| `asymmetric_group.list_presets` | Returns the preset library (names and contents) as a response — use `response_variable` in scripts. |
| `asymmetric_group.apply_preset` | Apply a saved preset to a group (`entity_id`, `preset`). |
| `asymmetric_group.save_preset` | Save a group's current settings as a preset (`entity_id`, `preset`, optional `overwrite`). |
| `asymmetric_group.delete_preset` | Delete a preset from the library (`preset`). Groups that used it keep their settings. |

## Presets

A preset is a named bundle of settings — switch positions and light sets — kept in one library shared by all your Asymmetric Groups. Apply it to any group from an automation, a script, or the group's **Configure → Preset library** editor.

A preset only needs to contain the settings it should change. Settings it doesn't mention are left alone, which makes two styles possible:

- **Behaviour patterns** — e.g. a preset that only stores "Asymmetric power: on". Apply it to many groups to give them all the same behaviour, each keeping its own lights.
- **Full snapshots** — save a group's complete settings with `asymmetric_group.save_preset` and re-apply them later, or tweak the snapshot in the editor first.

Light sets in a preset are matched against each group's own base lights when applied, so a preset can never point a group at lights it doesn't have. After a preset is applied, the group's `last_applied_preset` attribute shows its name (just a label — editing the group afterwards doesn't remove it).

## Example: one bedroom, two personalities

A bedroom has four recessed lights, `light.bedroom_1` … `light.bedroom_4`. Light 3 is the one whose position gives a soft, low glow — enough to move around at night without waking anyone. During the day the room should behave like any normal room; at night, "the bedroom lights" should mean light 3 only.

Create an Asymmetric Group **Bedroom lights** with all four lights as base members:

- **Power-ON set:** `light.bedroom_3` · **Power-OFF set:** all four
- **Asymmetric attributes / state:** off (they use all base lights)
- **Enforce power scope:** on

A lighting automation flips the group's **Asymmetric power** switch: off during the day and evening, on when the household's "Night" scene runs.

What happens:

- **The night theme engages — Asymmetric power flips on.** The Power-ON set shrinks to light 3. Because **Enforce power scope** is on, the change applies right away: if all four lights were on, 1, 2 and 4 turn off and light 3 stays. If the room was dark, nothing turns on; the narrower set just takes effect next time.
- **"Turn on the bedroom lights" at night.** Only light 3 lights. The group is filtered at the group itself, so it works identically from voice, a dashboard, an automation, or a wall switch that targets the group.
- **Someone turns light 2 on by hand.** It comes on (direct control of a light is never blocked). But **"turn off the bedroom lights" still switches all four off**, including the hand-lit light 2 — the Power-OFF set is the full base set, so a group off always sweeps the whole room.
- **Morning — Asymmetric power flips back off.** The set widens to all four again. With enforcement on, if light 3 is still lit the other three come on with it; if the room is dark, nothing happens and the group is a plain group again.

(If you run Adaptive Lighting, point it at the individual lights, not the group.)

### Where the other two switches earn their keep

- **Asymmetric attributes** — a bathroom group of ceiling lights plus a vanity light. The vanity is set bright for shaving and shouldn't be dragged along when a scene dims the group for a bath. Asymmetric power is off in this example, so every light is in the Power-ON set. Put the ceiling lights in the Attribute set and leave the vanity out: brightness and colour changes to the group affect only the ceiling lights — the ones that are on are adjusted, and one that is off comes on at the new level — while the vanity is never dragged along: it keeps its level if it's on, and stays off if it's off. A plain turn-on still switches the vanity on with the group — only attribute changes skip it.
- **Asymmetric state** — a hallway group that also includes the hall closet light, so "hallway off" sweeps the closet too. The closet shouldn't make the hallway read as *on* on a dashboard just because someone left it lit. Put only the ceiling lights in the State set: the group's on/off state comes from them alone, and the closet light is invisible to the group's state while remaining a full member for power.

## Good to know

- Only calls made **to the group itself** are affected. Anything that addresses the lights directly — an area or label target, a scene, or another integration — works exactly as before.
- If a behaviour's lights are all off while the group is on for another reason (say, the group is on because a State light is on, but no Attribute light is), the group reports its colour and brightness from whichever lights are actually on, so dashboards always show sensible values.
- Switching a behaviour on or off can change the group's reported on/off state without any light changing — for example, turning *Asymmetric state* off when a hand-lit lamp is on suddenly counts that lamp. That's inherent to choosing which lights define the state.
- **Templates: `expand()` does not expand an Asymmetric Group.** Home Assistant's `expand()` only recognises groups created with the core Group integration, so `expand(light.my_group)` returns the group itself instead of its lights. Read the members from the attribute instead — `expand(state_attr('light.my_group', 'entity_id'))` — and if the group sits inside a regular group, apply the same step once more to anything in the result that still has an `entity_id` attribute. Targeting the group in service calls is unaffected.
- A group's current settings survive restarts. Editing the group under **Configure** resets it to the configured sets, discarding changes made by presets or `set_members` since — the Configure page is the starting point after every edit.

## Troubleshooting

- **The helper doesn't appear under Create helper.** The integration isn't loaded: confirm the HACS install finished, restart Home Assistant, and check **Settings → System → Logs** for `asymmetric_group` errors. HACS also hides integrations from Home Assistant versions older than the minimum in `hacs.json` (2024.5.0).
- **A group light is missing or unavailable after editing members.** Editing a group under **Configure** reloads it. If a member light was renamed or removed, re-open **Configure** and re-pick the member sets — member lists are filtered to the group's base members, so a removed light drops out of the sets. Until then the group may show as unknown.
- **Turning the group on lit fewer lights than expected.** That's the feature, most likely: check the group's **Asymmetric power** switch — while it is on, turn-on uses only the Power-ON set. The group's `effective_power_on_members` attribute shows the set currently in effect.
- **The group shows "on" while its lights look off (or the reverse).** Check the **Asymmetric state** switch and the `effective_state_members` attribute: while the switch is on, only the State set decides the group's state.
- **A scope change logged a warning about an unconfirmed light.** With **Enforce power scope** on, a member that drops offline (or keeps reporting its old state) during reconciliation is named once in the log and skipped — everything else still settles. Check that light's own connectivity.
- **Still stuck?** Enable debug logging for the integration and reproduce the problem before filing an issue:

```yaml
logger:
  logs:
    custom_components.asymmetric_group: debug
```

## FAQ

**Is this a replacement for the built-in Group helper?**
No — it sits next to it. Use regular groups unless you need different lights for different behaviours.

**What happens if I delete a preset?**
Nothing changes on any group. Applying a preset copies its settings into the group, so groups keep working without it.

**Can I create or delete groups from automations?**
Groups themselves are created in the UI like other helpers. Everything about an existing group — its switches, its light sets, and presets — is controllable from automations and scripts.

## License

MIT — see [LICENSE](LICENSE).
