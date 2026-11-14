# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] - 2026-11-14

### Changed

- The Configure form now groups each behaviour in its own section
  — **Power**, **Attributes**, and **State** — with the behaviour's
  switch as the first field inside its section, directly above the
  member pickers it governs. On the flat form, the frontend's field
  spacing left each switch visually closer to the previous
  behaviour's pickers than to its own sets; inside a section the
  switch and its sets stay together. Base members, Enforce power
  scope, and Hide members are unchanged, and saved settings and
  behaviour are identical.

### Requirements

- Minimum Home Assistant version raised from 2024.5.0 to
  **2024.7.0**: the form sections used by the Configure form
  (`homeassistant.data_entry_flow.section`) were added in 2024.7.0.

## [1.2.0] - 2026-11-13

### Changed

- Configure is now a single page. The **Member sets and switches**
  form shows the base members, every behaviour switch, and every
  member set at once — each switch sits directly above the member
  pickers it governs — instead of sending each switched-on
  behaviour to its own follow-up page after Submit. Member sets
  are always shown and editable, including for behaviours that are
  switched off: such a set is saved but has no effect until the
  behaviour is switched on. Saved settings and behaviour are
  otherwise unchanged.

### Removed

- The long explanatory paragraphs at the top of the Configure form
  and the creation wizard's switch page. Behaviour is documented
  in the README instead.

## [1.1.0] - 2026-11-13

### Added

- Base members can now be edited after creation under the group's
  **Configure** button (core-group parity; previously they were
  fixed at creation, and changing them meant deleting and
  recreating the helper). The members form now opens with the base
  members alongside the switches. When the base set changes, a set
  that still lists the whole base set follows it, and narrowed sets
  are filtered to the new base; a member removed from the base that
  the group had hidden is shown again. Editing the base never
  switches any lights — like every Configure edit it reseeds the
  group and takes effect from the next command. The group's name
  remains fixed at creation, as in core groups.

## [1.0.4] - 2026-11-13

### Fixed

- Documentation corrections from a claim-by-claim validation of
  the README against the code: the Enforce section no longer lists
  editing the sets in Configure as an enforcement trigger, the
  setup/options descriptions scope the Enforce wording to runtime
  changes, presets are documented as applied via the
  `apply_preset` service (the Configure preset editor only creates
  and edits them), the "controllable from automations" claims now
  name the actual surface (switches, sets, presets), and the
  ambient-lights example states that both presets switch
  Asymmetric power on. No functional changes.

## [1.0.3] - 2026-11-13

### Changed

- Documentation: the README and the bug-report template now call
  the store the "Marketplace" — Home Assistant 2026.11 renamed
  HACS to the built-in Marketplace — with a note mapping the old
  name for users on older Home Assistant versions. No functional
  changes.

## [1.0.2] - 2026-11-12

### Changed

- Documentation: the README's behaviour rules are presented as a
  "Behaviour at a glance" matrix covering turn on, turn off,
  reported state, and brightness/colour changes; the worked
  example is replaced by a brief Examples section with one short
  scenario per feature; and the uninstall steps explain why the
  helpers are deleted while the integration is still installed.

## [1.0.1] - 2026-11-12

### Fixed

- The setup and options screens now explain Enforce power scope and
  Hide members where their switches are chosen, instead of showing
  bare labels.
- The attribute member step no longer implies that an already-on
  light outside the Power-ON set is left alone by attribute
  changes — it is adjusted, and the text now says so.
- The option that counts the group's state lights is labelled
  "All state lights on"; the member-count wording in the setup
  screens, the README, and the service descriptions is aligned.
- Saving a group's options when a member group has been edited to
  include it now explains that the loop must be broken in the
  member group, instead of asking for a member removal this
  screen doesn't offer.
- Service descriptions are clearer: preset names must match
  exactly, and saving a preset fails on a duplicate name when
  overwrite is off.

### Changed

- Documentation tidy-up: the README's attribute guarantees are
  split into short bullets, contributor templates ask for the
  group's effective sets in bug reports, and blank issues are
  disabled so reports use the form.

## [1.0.0] - 2026-11-12

First release.

### Added

- Asymmetric Group helper for Home Assistant light groups: the lights
  that turn on, the lights that turn off, the lights that take
  brightness and colour changes, and the lights that decide whether
  the group shows as "on" are four separate member sets, each with its
  own switch on the group's device page (Asymmetric power, Asymmetric
  attributes, Asymmetric state). While a switch is off, that behaviour
  uses all the group's lights, exactly like a regular group.
- Enforce power scope: when the Power-ON set changes, lights that
  dropped out of the set are switched off and lights that joined are
  switched on (only if the group was lit), as soon as the change
  happens instead of at the next turn-on. The change waits until the
  affected lights have registered their new states.
- Attribute changes are scoped: a brightness, colour or effect change
  sent to the group reaches only the Attribute set. A light is
  switched on by such a call only if it is in both the Attribute and
  Power-ON sets; an Attribute light outside the Power-ON set is
  adjusted only if it is already on, and a Power-ON light outside the
  Attribute set is left out of the change entirely.
- Group members: a member can itself be a light group — a regular
  group or another Asymmetric Group — counted as one member in every
  set and switched and dimmed as one unit. A group that would contain
  itself, directly or through another group, is rejected.
- Presets: named combinations of the switches and member sets, stored
  in one shared library and applied to any group; a preset stores only
  the settings it includes.
- Services `asymmetric_group.set_members`, `apply_preset`,
  `save_preset`, `delete_preset` and `list_presets` for automations.
- Hide members and All entities options, matching the core light
  group helper.

Requires Home Assistant 2024.5.0 or newer.
