"""Config and options flow: where burn, BMR and weight come from. All optional."""
from __future__ import annotations

from functools import partial
from typing import Any

import voluptuous as vol
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import get_metadata
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig, TextSelector

from .const import (
    CONF_BMR_ENTITY,
    CONF_BURN_ENTITY,
    CONF_BURN_STATISTIC,
    CONF_WEIGHT_ENTITY,
    DEFAULT_BURN_STATISTIC,
    DOMAIN,
)

#: option -> unique_id suffix of the matching ha_samsung_health sensor
_SAMSUNG_SUFFIXES = {CONF_BURN_ENTITY: "_total_calories", CONF_BMR_ENTITY: "_bmr", CONF_WEIGHT_ENTITY: "_weight"}

OPTIONS_SCHEMA = vol.Schema({
    vol.Optional(CONF_BURN_ENTITY): EntitySelector(EntitySelectorConfig(domain="sensor")),
    vol.Optional(CONF_BMR_ENTITY): EntitySelector(EntitySelectorConfig(domain="sensor")),
    vol.Optional(CONF_WEIGHT_ENTITY): EntitySelector(EntitySelectorConfig(domain="sensor")),
    vol.Optional(CONF_BURN_STATISTIC): TextSelector(),
})


async def async_samsung_defaults(hass: HomeAssistant) -> dict[str, str]:
    """Pre-fill with the Samsung Health sensors and statistic, when they exist.

    Other sources (Withings, Garmin, Fitbit, Health Connect, ...) are picked by
    hand; their burn history then comes from the burn sensor's own statistics.
    """
    out: dict[str, str] = {}
    metadata = await get_instance(hass).async_add_executor_job(
        partial(get_metadata, hass, statistic_ids={DEFAULT_BURN_STATISTIC})
    )
    if metadata:
        out[CONF_BURN_STATISTIC] = DEFAULT_BURN_STATISTIC
    for entity in er.async_get(hass).entities.values():
        if entity.platform != "ha_samsung_health" or entity.domain != "sensor":
            continue
        for option, suffix in _SAMSUNG_SUFFIXES.items():
            if entity.unique_id.endswith(suffix):
                out.setdefault(option, entity.entity_id)
    return out


class NutritionConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Nutrition", data={}, options=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, await async_samsung_defaults(self.hass)),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NutritionOptionsFlow:
        return NutritionOptionsFlow()


class NutritionOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, self.config_entry.options),
        )
