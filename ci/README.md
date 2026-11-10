# Testing and CI

## Running the tests

You need Linux, macOS or WSL (see the note on Windows below) and a Python version
supported by the current Home Assistant release (CI uses 3.14).

```
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/ruff check --select F,E,W,B,I,UP --ignore E501 custom_components tests
.venv/bin/python -m pytest tests --timeout 30
```

Validate with Home Assistant's own **hassfest** too (needs Docker):

```
docker run --rm -v "$PWD":/github/workspace ghcr.io/home-assistant/hassfest:latest
```

hassfest must report no errors, and the suite must pass with **zero skipped tests**: a skip is a false green.

## CI

`Jenkinsfile` is the maintainer's Jenkins pipeline. It does exactly the steps above
in clean containers: hassfest, then lint and the full suite with a JUnit report, then a gate that fails the
build if any test was skipped, for the branch given in its `BRANCH` parameter. It
needs Docker on the agent (images `ghcr.io/home-assistant/hassfest` and `python:3.14-slim`); you don't need
Jenkins to work on the integration.

## Notes for contributors

- **Windows.** `pytest-homeassistant-custom-component` imports
  `homeassistant.runner`, which needs `fcntl`, so the Home Assistant tests don't run
  on Windows; use WSL. The pure model tests run anywhere with the plugin turned off:
  `python -m pytest tests/test_models.py -p no:homeassistant`.
- **Every test uses the recorder.** The integration depends on `recorder`, so
  `tests/conftest.py` requests `recorder_mock` before anything creates `hass`.
  The other order fails with `assert not hass_fixture_setup`.
- **Statistics tests use the real test recorder**, not mocks: they write rows and
  read them back with `statistics_during_period`, which also proves that clearing a
  statistic and rewriting it happen in the right order.
- **The clock is frozen** in most tests (`freezer`, 2026-11-09 11:00 Stockholm).
  `asyncio.sleep(x)` with x > 0 then never returns; use `asyncio.sleep(0)` loops
  (`spin()` in `tests/conftest.py`). `--timeout 30` makes a hang show up at once.
- **LLM API tests call the tools directly**, with the schema-validated arguments,
  not through `APIInstance.async_call_tool`: that imports the conversation
  integration, which needs `hassil`, not installed in the test venv. They need
  Home Assistant 2026.10 or newer (`probatio` schemas).
- **No real food log in the repository.** Tests and examples use made-up entries.
