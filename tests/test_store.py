from datetime import date, datetime, timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.ha_nutrition.models import InvalidInput
from custom_components.ha_nutrition.store import STORE_KEY, NutritionStore, UnknownEntry

NOW = datetime.fromisoformat("2026-11-09T10:00:00+00:00")
D8, D7 = date(2026, 11, 8), date(2026, 11, 7)

BREAKFAST = {"category": "breakfast", "description": "Gröt med bär", "kcal": 400, "protein_g": 20, "time": "07:30"}
COFFEE = {"category": "drink", "description": "Kaffe", "kcal": 5}


@pytest.fixture
async def store(hass, freezer, hass_storage):
    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Stockholm")
    s = NutritionStore(hass)
    await s.async_load()
    changes: list[set[date]] = []
    s.async_add_listener(changes.append)
    s.changes = changes  # type: ignore[attr-defined]
    return s


async def test_log_and_reload(hass, store, hass_storage):
    result = await store.async_log_entries(D8, [BREAKFAST, COFFEE], source="llm")
    assert result["date"] == "2026-11-08"
    assert [e["description"] for e in result["entries"]] == ["Gröt med bär", "Kaffe"]
    assert all(e["id"] for e in result["entries"])
    assert result["totals"]["kcal"] == 405
    assert result["repeated"] is False
    assert store.changes == [{D8}]
    assert hass_storage[STORE_KEY]["version"] == 1

    again = NutritionStore(hass)
    await again.async_load()
    # timed entries first, untimed last
    assert [e.description for e in again.entries_on(D8)] == ["Gröt med bär", "Kaffe"]
    assert again.days_with_entries() == {D8}


async def test_default_date_is_local_today(store, freezer):
    freezer.move_to("2026-11-08T23:30:00+00:00")  # 00:30 on 11-09 in Stockholm
    result = await store.async_log_entries(None, [COFFEE], source="llm")
    assert result["date"] == "2026-11-09"


async def test_invalid_item_writes_nothing(store):
    with pytest.raises(InvalidInput, match="kcal"):
        await store.async_log_entries(D8, [BREAKFAST, {**COFFEE, "kcal": -1}], source="llm")
    assert store.all_entries() == []
    assert store.changes == []


@pytest.mark.parametrize("count", [0, 51])
async def test_item_count_limits(store, count):
    with pytest.raises(InvalidInput, match="entries"):
        await store.async_log_entries(D8, [COFFEE] * count, source="llm")


async def test_request_id_repeat_is_noop(store, freezer, hass_storage):
    first = await store.async_log_entries(D8, [COFFEE], source="llm", request_id="msg-1")
    second = await store.async_log_entries(D8, [{**COFFEE, "description": "Kaffe svart"}], source="llm",
                                           request_id="msg-1")
    assert second["repeated"] is True
    assert second["entries"] == first["entries"]
    assert len(store.all_entries()) == 1
    assert store.changes == [{D8}]

    freezer.move_to(NOW + timedelta(days=8))
    await store.async_log_entries(None, [COFFEE], source="llm", request_id="msg-2")
    assert set(hass_storage[STORE_KEY]["data"]["requests"]) == {"msg-2"}


async def test_request_id_reused_for_another_day_is_an_error(store):
    await store.async_log_entries(D8, [COFFEE], source="llm", request_id="msg-1")
    with pytest.raises(InvalidInput, match="request_id"):
        await store.async_log_entries(D7, [COFFEE], source="llm", request_id="msg-1")
    assert len(store.all_entries()) == 1


async def test_older_minor_version_loads(hass, freezer, hass_storage):
    hass_storage[STORE_KEY] = {"version": 1, "minor_version": 0, "key": STORE_KEY,
                               "data": {"entries": {}, "notes": {"2026-11-08": "old"}}}
    s = NutritionStore(hass)
    await s.async_load()
    assert s.note(D8) == "old"


async def test_possible_duplicates(store):
    first = await store.async_log_entries(D8, [COFFEE], source="llm")
    second = await store.async_log_entries(D8, [{**COFFEE, "description": "kaffe"}], source="llm")
    assert first["possible_duplicates"] == []
    assert [e["id"] for e in second["possible_duplicates"]] == [first["entries"][0]["id"]]
    assert len(store.entries_on(D8)) == 2


async def test_update_moves_date_notifies_both_days(store, freezer):
    [logged] = (await store.async_log_entries(D8, [BREAKFAST], source="llm"))["entries"]
    freezer.tick(60)
    result = await store.async_update_entry(logged["id"], {"date": "2026-11-07", "kcal": 450}, source="service")
    assert result["entry"]["date"] == "2026-11-07"
    assert result["entry"]["kcal"] == 450
    assert result["entry"]["created_at"] == logged["created_at"]
    assert result["entry"]["updated_at"] != logged["updated_at"]
    assert result["totals"]["date"] == "2026-11-07"
    assert store.changes[-1] == {D7, D8}
    assert store.entries_on(D8) == []


async def test_update_rejects_bad_fields(store):
    [logged] = (await store.async_log_entries(D8, [BREAKFAST], source="llm"))["entries"]
    with pytest.raises(InvalidInput, match="category"):
        await store.async_update_entry(logged["id"], {"category": "fika"}, source="llm")
    with pytest.raises(InvalidInput, match="description"):
        await store.async_update_entry(logged["id"], {"description": None}, source="llm")


async def test_delete_and_unknown_id(store):
    [logged] = (await store.async_log_entries(D8, [COFFEE], source="llm"))["entries"]
    result = await store.async_delete_entry(logged["id"])
    assert result == {"deleted": logged["id"], "date": "2026-11-08", "totals": result["totals"]}
    assert result["totals"]["entries"] == 0
    with pytest.raises(UnknownEntry):
        await store.async_delete_entry(logged["id"])
    with pytest.raises(UnknownEntry):
        await store.async_update_entry("nope", {"kcal": 1}, source="llm")


async def test_day_note_set_and_clear(store):
    assert await store.async_set_day_note(D8, "  Kalas hos Anna ") == {"date": "2026-11-08", "note": "Kalas hos Anna"}
    assert store.note(D8) == "Kalas hos Anna"
    assert store.notes_between(D7, D8) == {D8: "Kalas hos Anna"}
    await store.async_set_day_note(D8, "")
    assert store.note(D8) is None


async def test_export_import_round_trip(hass, store):
    await store.async_log_entries(D8, [BREAKFAST, COFFEE], source="llm")
    await store.async_set_day_note(D8, "note")
    exported = store.export()
    assert exported["format"] == "ha_nutrition.log"

    other = NutritionStore(hass, key="ha_nutrition.other")  # a second file in hass_storage
    await other.async_load()
    assert await other.async_import(exported, replace=False) == {"entries": 2, "notes": 1}
    assert [e.to_dict() for e in other.all_entries()] == [e.to_dict() for e in store.all_entries()]
    with pytest.raises(InvalidInput, match="replace"):
        await other.async_import(exported, replace=False)
    assert await other.async_import({"format": "ha_nutrition.log", "version": 1, "entries": [], "notes": {}},
                                    replace=True) == {"entries": 0, "notes": 0}


async def test_import_rejects_bad_entry(store):
    bad = {"format": "ha_nutrition.log", "version": 1, "notes": {},
           "entries": [{"id": "x", "date": "2026-11-08", "category": "fika", "description": "x", "kcal": 1}]}
    with pytest.raises(InvalidInput, match="category"):
        await store.async_import(bad, replace=False)
    assert store.all_entries() == []


async def test_future_date_rejected(store):
    with pytest.raises(InvalidInput, match="future"):
        await store.async_log_entries(date(2026, 11, 10), [COFFEE], source="llm")


async def test_entries_between(store):
    await store.async_log_entries(D7, [COFFEE], source="llm")
    await store.async_log_entries(D8, [COFFEE], source="llm")
    assert len(store.entries_between(D8, D8)) == 1
    assert len(store.entries_between(D7, dt_util.now().date())) == 2
