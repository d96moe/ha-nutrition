import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.ha_nutrition.const import DOMAIN

LUNCH = {"category": "lunch", "description": "Kycklinggryta", "kcal": 650, "protein_g": 45}


async def call(hass, service, **data):
    return await hass.services.async_call(DOMAIN, service, data, blocking=True, return_response=True)


async def test_log_food_returns_ids(hass, entry):
    result = await call(hass, "log_food", date="2026-11-08", entries=[LUNCH], request_id="r1")
    assert result["date"] == "2026-11-08"
    assert result["entries"][0]["id"]
    assert result["entries"][0]["source"] == "service"
    assert result["totals"]["kcal"] == 650


async def test_log_food_default_today(hass, entry):
    assert (await call(hass, "log_food", entries=[LUNCH]))["date"] == "2026-11-09"


async def test_bad_input_is_a_validation_error(hass, entry):
    with pytest.raises(ServiceValidationError, match="category"):
        await call(hass, "log_food", entries=[{**LUNCH, "category": "fika"}])
    with pytest.raises(ServiceValidationError, match="date"):
        await call(hass, "log_food", date="igår", entries=[LUNCH])
    with pytest.raises(ServiceValidationError, match="no entry"):
        await call(hass, "delete_entry", entry_id="nope")


async def test_nan_is_rejected(hass, entry):
    [logged] = (await call(hass, "log_food", entries=[LUNCH]))["entries"]
    with pytest.raises(ServiceValidationError, match="kcal"):
        await call(hass, "update_entry", entry_id=logged["id"], kcal="nan")


async def test_update_delete_and_note(hass, entry):
    [logged] = (await call(hass, "log_food", entries=[LUNCH]))["entries"]
    updated = await call(hass, "update_entry", entry_id=logged["id"], kcal=700, protein_g=None)
    assert updated["entry"]["kcal"] == 700
    assert updated["entry"]["protein_g"] is None
    deleted = await call(hass, "delete_entry", entry_id=logged["id"])
    assert deleted["totals"]["entries"] == 0
    assert await call(hass, "set_day_note", date="2026-11-09", note="Julbord") == {
        "date": "2026-11-09", "note": "Julbord"}


async def test_export_import_round_trip(hass, entry):
    await call(hass, "log_food", entries=[LUNCH])
    exported = await call(hass, "export")
    assert len(exported["entries"]) == 1
    with pytest.raises(ServiceValidationError, match="replace"):
        await call(hass, "import", data=exported)
    assert await call(hass, "import", data=exported, replace=True) == {"entries": 1, "notes": 0}


async def test_no_loaded_entry(hass, entry):
    await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError, match="not set up"):
        await call(hass, "log_food", entries=[LUNCH])
