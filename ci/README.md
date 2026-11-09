# CI

## Jobs (Jenkins on LXC 200)

| Job | File | What it does |
|---|---|---|
| `ha-nutrition` | `ci/Jenkinsfile` | Lint (ruff), the full pytest suite in a Python 3.14 venv on the Jenkins agent (`BRANCH` parameter, `--timeout 30` per test), then a zero-skip gate. |

## Notes

- **Tests need Linux.** `pytest-homeassistant-custom-component` imports
  `homeassistant.runner`, which needs `fcntl`, so the tests do not run on Windows.
  The pure model tests run anywhere with the plugin turned off:
  `python -m pytest tests/test_models.py -p no:homeassistant`.
- **Every test uses the recorder.** The integration depends on `recorder`, so
  `tests/conftest.py` requests `recorder_mock` before anything creates `hass`.
  The other order fails with `assert not hass_fixture_setup`.
- **Statistics tests use the real test recorder**, not mocks: they write rows and
  read them back with `statistics_during_period`, which also proves that clearing a
  statistic and rewriting it happen in the right order.
- **Zero-skip gate.** A skipped test is a false green, so the build fails if
  `test-results.xml` reports any skips.
- **The clock is frozen** in most tests (`freezer`, 2026-11-09 11:00 Stockholm).
  `asyncio.sleep(x)` with x > 0 then never returns; use `asyncio.sleep(0)` loops
  (`spin()` in `tests/conftest.py`). Run with `--timeout 30` so a hang shows at once.
- **LLM API tests call the tools directly**, with the schema-validated arguments,
  not through `APIInstance.async_call_tool`: that imports the conversation
  integration, which needs `hassil`, not installed in the test venv. They need
  Home Assistant 2026.10 or newer (`probatio` schemas).
- **No real food log in the repository.** Tests and examples use made-up entries.
