# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
