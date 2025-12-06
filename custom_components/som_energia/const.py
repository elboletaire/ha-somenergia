"""Constants for the Som Energia integration."""

from __future__ import annotations

from datetime import time
from typing import Final

from homeassistant.const import CURRENCY_EURO, UnitOfEnergy

DOMAIN: Final = "som_energia"

# API Configuration
API_BASE_URL: Final = "https://api.somenergia.coop/data"
API_TIMEOUT: Final = 30  # seconds

# Tariff Types (config flow options)
CONF_TARIFF_20TD: Final = "tariff_20td"
CONF_TARIFF_30TD: Final = "tariff_30td"
CONF_TARIFF_61TD: Final = "tariff_61td"
CONF_COMPENSATION: Final = "compensation"

# API Tariff Identifiers
TARIFF_20TD: Final = "2.0TD"
TARIFF_30TD: Final = "3.0TD"
TARIFF_61TD: Final = "6.1TD"

# Geographic Zone (fixed to Peninsula)
GEO_ZONE: Final = "PENINSULA"

# Data Update Configuration
DAILY_UPDATE_TIME: Final = time(18, 0, 0)  # 18:00 UTC
RETRY_INTERVAL_MINUTES: Final = 30
MAX_RETRIES: Final = 3

# Expected data points (17 days * 24 hours)
EXPECTED_DATA_POINTS: Final = 408

# Sensor Keys (per tariff)
SENSOR_CURRENT_PRICE: Final = "current_price"
SENSOR_NEXT_HOUR_PRICE: Final = "next_hour_price"
SENSOR_TODAY_MIN_PRICE: Final = "today_min_price"
SENSOR_TODAY_MAX_PRICE: Final = "today_max_price"
SENSOR_TODAY_AVG_PRICE: Final = "today_avg_price"
SENSOR_TOMORROW_MIN_PRICE: Final = "tomorrow_min_price"
SENSOR_TOMORROW_MAX_PRICE: Final = "tomorrow_max_price"
SENSOR_TOMORROW_AVG_PRICE: Final = "tomorrow_avg_price"

# Sensor Configurations
SENSOR_TYPES: Final = [
    SENSOR_CURRENT_PRICE,
    SENSOR_NEXT_HOUR_PRICE,
    SENSOR_TODAY_MIN_PRICE,
    SENSOR_TODAY_MAX_PRICE,
    SENSOR_TODAY_AVG_PRICE,
    SENSOR_TOMORROW_MIN_PRICE,
    SENSOR_TOMORROW_MAX_PRICE,
    SENSOR_TOMORROW_AVG_PRICE,
]

# Unit of measurement
PRICE_UNIT: Final = f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}"
