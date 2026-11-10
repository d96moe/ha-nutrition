from homeassistant.config_entries import ConfigEntryState

from custom_components.ha_nutrition.const import DOMAIN
from custom_components.ha_nutrition.store import STORE_KEY

COFFEE = {"category": "drink", "description": "Kaffe", "kcal": 5}


async def _log(hass, **data):
    return await hass.services.async_call(DOMAIN, "log_food", {"entries": [COFFEE], **data},
                                          blocking=True, return_response=True)


async def test_setup_unload_and_remove_keep_the_log(hass, entry, hass_storage):
    assert entry.state is ConfigEntryState.LOADED
    assert {"log_food", "update_entry", "delete_entry", "set_day_note", "export", "import"} <= set(
        hass.services.async_services_for_domain(DOMAIN)
    )
    await _log(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass_storage[STORE_KEY]["data"]["entries"]) == 1  # deliberately kept




async def test_re_added_entry_sees_the_old_log(hass, entry):
    await _log(hass)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    again = MockConfigEntry(domain=DOMAIN, title="Nutrition")
    again.add_to_hass(hass)
    assert await hass.config_entries.async_setup(again.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(again.runtime_data.store.all_entries()) == 1
