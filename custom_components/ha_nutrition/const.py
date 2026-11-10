"""Constants for the Nutrition integration."""
from __future__ import annotations

DOMAIN = "ha_nutrition"

#: Meal/item categories, in display order. fika and sweets are snack; wine and
#: beer are drink (with alcohol_units); protein shakes are supplement.
CATEGORIES: tuple[str, ...] = ("breakfast", "lunch", "dinner", "snack", "drink", "supplement", "other")

#: Optional per-entry nutrients besides kcal.
NUTRIENT_FIELDS: tuple[str, ...] = ("protein_g", "sat_fat_g", "alcohol_units")

MAX_KCAL_PER_ENTRY = 10000
MAX_DESCRIPTION = 500

SOURCE_LLM = "llm"
SOURCE_SERVICE = "service"

# Options: where burn, BMR and weight come from (any integration's sensors).
CONF_BURN_ENTITY = "burn_entity"
CONF_BMR_ENTITY = "bmr_entity"
CONF_WEIGHT_ENTITY = "weight_entity"
CONF_BURN_STATISTIC = "burn_statistic"
DEFAULT_BURN_STATISTIC = "ha_samsung_health:total_calories"

#: Fired (no arguments) after the log or a goal changed.
SIGNAL_UPDATED = "ha_nutrition_updated"
#: Fired with a date or None to set the plan start date entity.
SIGNAL_PLAN_START = "ha_nutrition_plan_start"
#: Fired (no arguments) after the derived plan values were recomputed.
SIGNAL_PLAN_UPDATED = "ha_nutrition_plan_updated"
