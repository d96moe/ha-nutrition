import asyncio
from datetime import datetime

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_nutrition.const import DOMAIN

#: Monday 2026-11-09, 10:00 UTC = 11:00 in Stockholm
NOW = datetime.fromisoformat("2026-11-09T10:00:00+00:00")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    # recorder_mock must come before anything that creates `hass`: the integration
    # depends on recorder, so every HA test needs it.
    yield


async def spin():
    """Let pending tasks run. The clock is frozen, so asyncio.sleep(>0) would never return."""
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.fixture
def options():
    return {}


@pytest.fixture
async def entry(hass, freezer, options):
    """A loaded Nutrition config entry at NOW, Stockholm time."""
    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Stockholm")
    config_entry = MockConfigEntry(domain=DOMAIN, title="Nutrition", options=options)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return config_entry
