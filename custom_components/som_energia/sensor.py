"""Sensor platform for Som Energia."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import statistics

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COMPENSATION,
    CONF_TARIFF_20TD,
    CONF_TARIFF_30TD,
    CONF_TARIFF_61TD,
    DOMAIN,
    PRICE_UNIT,
    SENSOR_CURRENT_PRICE,
    SENSOR_NEXT_HOUR_PRICE,
    SENSOR_TODAY_AVG_PRICE,
    SENSOR_TODAY_MAX_PRICE,
    SENSOR_TODAY_MIN_PRICE,
    SENSOR_TOMORROW_AVG_PRICE,
    SENSOR_TOMORROW_MAX_PRICE,
    SENSOR_TOMORROW_MIN_PRICE,
)
from .coordinator import CoordinatorData, PriceData, SomEnergiaPricingCoordinator
from .price_timeline import MADRID_TZ


@dataclass(frozen=True, kw_only=True)
class SomEnergiaSensorEntityDescription(SensorEntityDescription):
    """Describe Som Energia sensor entity."""

    value_fn: Callable[[PriceData, datetime], float | None]


def _current_price(data: PriceData, now: datetime) -> float | None:
    """Get current hour price."""
    return data.get_price_at(now)


def _next_hour_price(data: PriceData, now: datetime) -> float | None:
    """Get next hour price."""
    next_hour = now + timedelta(hours=1)
    return data.get_price_at(next_hour)


def _today_min_price(data: PriceData, now: datetime) -> float | None:
    """Get today's minimum price."""
    prices = data.get_prices_for_date(now.astimezone(MADRID_TZ).date())
    return min(prices) if prices else None


def _today_max_price(data: PriceData, now: datetime) -> float | None:
    """Get today's maximum price."""
    prices = data.get_prices_for_date(now.astimezone(MADRID_TZ).date())
    return max(prices) if prices else None


def _today_avg_price(data: PriceData, now: datetime) -> float | None:
    """Get today's average price."""
    prices = data.get_prices_for_date(now.astimezone(MADRID_TZ).date())
    return round(statistics.mean(prices), 5) if prices else None


def _tomorrow_min_price(data: PriceData, now: datetime) -> float | None:
    """Get tomorrow's minimum price."""
    tomorrow = now.astimezone(MADRID_TZ).date() + timedelta(days=1)
    prices = data.get_prices_for_date(tomorrow)
    return min(prices) if prices else None


def _tomorrow_max_price(data: PriceData, now: datetime) -> float | None:
    """Get tomorrow's maximum price."""
    tomorrow = now.astimezone(MADRID_TZ).date() + timedelta(days=1)
    prices = data.get_prices_for_date(tomorrow)
    return max(prices) if prices else None


def _tomorrow_avg_price(data: PriceData, now: datetime) -> float | None:
    """Get tomorrow's average price."""
    tomorrow = now.astimezone(MADRID_TZ).date() + timedelta(days=1)
    prices = data.get_prices_for_date(tomorrow)
    return round(statistics.mean(prices), 5) if prices else None


SENSOR_DESCRIPTIONS: dict[str, SomEnergiaSensorEntityDescription] = {
    SENSOR_CURRENT_PRICE: SomEnergiaSensorEntityDescription(
        key=SENSOR_CURRENT_PRICE,
        translation_key="current_price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_current_price,
    ),
    SENSOR_NEXT_HOUR_PRICE: SomEnergiaSensorEntityDescription(
        key=SENSOR_NEXT_HOUR_PRICE,
        translation_key="next_hour_price",
        native_unit_of_measurement=PRICE_UNIT,
        value_fn=_next_hour_price,
    ),
    SENSOR_TODAY_MIN_PRICE: SomEnergiaSensorEntityDescription(
        key=SENSOR_TODAY_MIN_PRICE,
        translation_key="today_min_price",
        native_unit_of_measurement=PRICE_UNIT,
        value_fn=_today_min_price,
    ),
    SENSOR_TODAY_MAX_PRICE: SomEnergiaSensorEntityDescription(
        key=SENSOR_TODAY_MAX_PRICE,
        translation_key="today_max_price",
        native_unit_of_measurement=PRICE_UNIT,
        value_fn=_today_max_price,
    ),
    SENSOR_TODAY_AVG_PRICE: SomEnergiaSensorEntityDescription(
        key=SENSOR_TODAY_AVG_PRICE,
        translation_key="today_avg_price",
        native_unit_of_measurement=PRICE_UNIT,
        value_fn=_today_avg_price,
    ),
    SENSOR_TOMORROW_MIN_PRICE: SomEnergiaSensorEntityDescription(
        key=SENSOR_TOMORROW_MIN_PRICE,
        translation_key="tomorrow_min_price",
        native_unit_of_measurement=PRICE_UNIT,
        value_fn=_tomorrow_min_price,
    ),
    SENSOR_TOMORROW_MAX_PRICE: SomEnergiaSensorEntityDescription(
        key=SENSOR_TOMORROW_MAX_PRICE,
        translation_key="tomorrow_max_price",
        native_unit_of_measurement=PRICE_UNIT,
        value_fn=_tomorrow_max_price,
    ),
    SENSOR_TOMORROW_AVG_PRICE: SomEnergiaSensorEntityDescription(
        key=SENSOR_TOMORROW_AVG_PRICE,
        translation_key="tomorrow_avg_price",
        native_unit_of_measurement=PRICE_UNIT,
        value_fn=_tomorrow_avg_price,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Som Energia sensors."""
    coordinator: SomEnergiaPricingCoordinator = entry.runtime_data

    entities: list[SomEnergiaPricingSensor] = []

    # Create sensors for each enabled tariff
    if entry.data.get(CONF_TARIFF_20TD):
        entities.extend(
            [
                SomEnergiaPricingSensor(
                    coordinator, description, "tariff_20td", "2.0TD Indexada"
                )
                for description in SENSOR_DESCRIPTIONS.values()
            ]
        )

    if entry.data.get(CONF_TARIFF_30TD):
        entities.extend(
            [
                SomEnergiaPricingSensor(
                    coordinator, description, "tariff_30td", "3.0TD Indexada"
                )
                for description in SENSOR_DESCRIPTIONS.values()
            ]
        )

    if entry.data.get(CONF_TARIFF_61TD):
        entities.extend(
            [
                SomEnergiaPricingSensor(
                    coordinator, description, "tariff_61td", "6.1TD Indexada"
                )
                for description in SENSOR_DESCRIPTIONS.values()
            ]
        )

    if entry.data.get(CONF_COMPENSATION):
        entities.extend(
            [
                SomEnergiaPricingSensor(
                    coordinator, description, "compensation", "Compensació d'excedents"
                )
                for description in SENSOR_DESCRIPTIONS.values()
            ]
        )

    async_add_entities(entities)


class SomEnergiaPricingSensor(
    CoordinatorEntity[SomEnergiaPricingCoordinator], SensorEntity
):
    """Representation of a Som Energia pricing sensor."""

    entity_description: SomEnergiaSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SomEnergiaPricingCoordinator,
        description: SomEnergiaSensorEntityDescription,
        tariff_key: str,
        tariff_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._tariff_key = tariff_key

        # Coordinator always has config_entry in this integration
        assert coordinator.config_entry is not None
        entry_id = coordinator.config_entry.entry_id

        # Unique ID: {entry_id}_{tariff_key}_{sensor_key}
        self._attr_unique_id = f"{entry_id}_{tariff_key}_{description.key}"

        # Device info groups sensors by tariff
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{tariff_key}")},
            name=tariff_name,
            manufacturer="Som Energia",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return None

        # Get price data for this tariff
        price_data: PriceData | None = getattr(
            self.coordinator.data, self._tariff_key, None
        )

        if price_data is None:
            return None

        # Calculate value using description's value_fn
        # Use UTC to match API timestamps (parsed as UTC-aware)
        now = dt_util.utcnow()
        return self.entity_description.value_fn(price_data, now)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            super().available
            and self.coordinator.data is not None
            and getattr(self.coordinator.data, self._tariff_key, None) is not None
        )
