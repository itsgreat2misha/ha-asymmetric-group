# Contributing

Thanks for helping improve Asymmetric Group!

## Ground rules

- **Bugs and ideas first:** open an issue before a large pull request so the
  approach can be agreed before you invest the work.
- **Keep the domain model intact:** base members are the full set; power,
  attributes, and state subgroups are always filtered to the base set, and
  an attribute-bearing call must never switch on a light outside the
  Power-ON set or apply attributes to a light outside the Attribute set.
  Changes that break those invariants need a design discussion in an
  issue first.
- **Storage and service compatibility matter:** the domain
  (`asymmetric_group`), service names, storage keys, and entity unique-id
  scheme are load-bearing for existing installs — do not rename them.

## Development

The member-set logic lives in `custom_components/asymmetric_group/engine.py`
and is deliberately free of Home Assistant imports, so it can be unit-tested
with the standard library alone:

```bash
python -m unittest discover -s tests
```

Home Assistant level tests (config flow, options flow, entry setup,
nested groups, attribute calls, documentation claims) live in
`tests/test_config_flow.py`, `tests/test_nesting.py`,
`tests/test_attribute_calls.py`, and `tests/test_docs_claims.py`
and run on top of
[pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component):

```bash
pip install -r requirements_test.txt
python -m pytest tests/
```

The pin in `requirements_test.txt` selects the Home Assistant version
under test. The minimum supported Home Assistant version is 2024.5.0
(see `hacs.json`), and the full test suite is verified against it.
On 2024.4.4 the config-flow tests pass, but nested groups do not
follow a subgroup's state there (member state-change propagation
behaves differently), which is why the floor is 2024.5.0.

CI (`.github/workflows/validate.yaml`) runs the unit tests, the
HA-level tests, lint (ruff), the HACS action, and hassfest on every
push — please make sure your change keeps all of them green.

## Pull requests

- One topic per PR, with tests for behaviour changes.
- Update the README when behaviour, services, or setup steps change.
- Describe user-visible changes in the PR body; releases are cut from tags
  whose version matches `manifest.json`.

## Repository workflow

Changes land on `main` only through pull requests. The default branch is
protected: force-pushes and branch deletion are blocked, and the CI checks
(Tests, HA-level tests, HACS validation, Hassfest validation) must pass
before a pull request can merge.

## AI involvement

This project's development uses AI-agent assistance. Every change is reviewed by the repository owner, who remains the responsible human for the project.
