"""Data update coordinator for Som Energia."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import async_timeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_time_change,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    API_BASE_URL,
    API_TIMEOUT,
    CONF_COMPENSATION,
    CONF_TARIFF_20TD,
    CONF_TARIFF_30TD,
    CONF_TARIFF_61TD,
    DAILY_UPDATE_TIME,
    DOMAIN,
    EXPECTED_DATA_POINTS,
    GEO_ZONE,
    MAX_RETRIES,
    RETRY_INTERVAL_MINUTES,
    TARIFF_20TD,
    TARIFF_30TD,
    TARIFF_61TD,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class PriceData:
    """Container for hourly price data."""

    timestamps: list[datetime]
    prices: list[float | None]
    first_date: datetime
    last_date: datetime

    def get_price_at(self, dt: datetime) -> float | None:
        """Get price for specific datetime (rounded to hour in Madrid timezone)."""
        # API data represents Madrid local hours, calculate offset from first_date
        madrid_tz = ZoneInfo("Europe/Madrid")

        # Convert input time to Madrid timezone and round to hour
        dt_madrid = dt.astimezone(madrid_tz)
        target_madrid = dt_madrid.replace(minute=0, second=0, microsecond=0)

        # Convert first_date to Madrid timezone
        first_date_madrid = self.first_date.astimezone(madrid_tz)

        # Calculate hours difference
        hours_diff = int((target_madrid - first_date_madrid).total_seconds() / 3600)

        # API quirk: first_date appears to be 1 hour before the actual first price period
        # Example: first_date "01:00:00" but prices[0] is actually for 00:00-01:00
        # This explains why last_date ends at "00:00:00" (midnight after last full day)
        # We adjust by adding 1 to align with the actual price array indexing
        hours_diff += 1

        # Check bounds and return price
        if 0 <= hours_diff < len(self.prices):
            return self.prices[hours_diff]
        return None

    def get_prices_for_date(self, target_date: date) -> list[float]:
        """Get all non-null prices for a specific date."""
        return [
            price
            for ts, price in zip(self.timestamps, self.prices, strict=False)
            if ts.date() == target_date and price is not None
        ]


@dataclass
class CoordinatorData:
    """Data structure returned by coordinator."""

    tariff_20td: PriceData | None = None
    tariff_30td: PriceData | None = None
    tariff_61td: PriceData | None = None
    compensation: PriceData | None = None
    last_update: datetime | None = None


class SomEnergiaPricingClient:
    """API client for Som Energia."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize the client."""
        self._session = session

    async def fetch_tariff_prices(self, tariff: str) -> dict[str, Any]:
        """Fetch prices for a specific tariff."""
        url = f"{API_BASE_URL}/indexed_prices"
        params = {"tariff": tariff, "geo_zone": GEO_ZONE}

        async with async_timeout.timeout(API_TIMEOUT):
            response = await self._session.get(url, params=params)
            response.raise_for_status()
            return await response.json()

    async def fetch_compensation_prices(self) -> dict[str, Any]:
        """Fetch compensation prices."""
        url = f"{API_BASE_URL}/compensation_indexed_prices"
        params = {"geo_zone": GEO_ZONE}

        async with async_timeout.timeout(API_TIMEOUT):
            response = await self._session.get(url, params=params)
            response.raise_for_status()
            return await response.json()


class SomEnergiaPricingCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Coordinator for Som Energia pricing data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,  # Manual scheduling
            config_entry=config_entry,
        )

        self._client = SomEnergiaPricingClient(async_get_clientsession(hass))
        self._enabled_tariffs = config_entry.data
        self._retry_count = 0
        self._daily_update_listener: Callable[[], None] | None = None
        self._retry_update_listener: Callable[[], None] | None = None
        self._hourly_update_listener: Callable[[], None] | None = None

    async def _async_update_data(self) -> CoordinatorData:
        """Fetch data from API (called daily at 18:00 UTC)."""
        new_data = CoordinatorData()

        try:
            # Fetch each enabled tariff
            if self._enabled_tariffs.get(CONF_TARIFF_20TD):
                new_data.tariff_20td = await self._fetch_and_parse_tariff(TARIFF_20TD)

            if self._enabled_tariffs.get(CONF_TARIFF_30TD):
                new_data.tariff_30td = await self._fetch_and_parse_tariff(TARIFF_30TD)

            if self._enabled_tariffs.get(CONF_TARIFF_61TD):
                new_data.tariff_61td = await self._fetch_and_parse_tariff(TARIFF_61TD)

            if self._enabled_tariffs.get(CONF_COMPENSATION):
                new_data.compensation = await self._fetch_and_parse_compensation()

            new_data.last_update = dt_util.utcnow()
            self._retry_count = 0  # Reset on success

            return new_data

        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            # Preserve old data on failure
            if self.data is not None:
                _LOGGER.warning("Failed to fetch data, preserving previous values: %s", err)
                return self.data
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def _fetch_and_parse_tariff(self, tariff: str) -> PriceData:
        """Fetch and parse tariff price data."""
        response = await self._client.fetch_tariff_prices(tariff)

        # Parse response
        curves = response["data"]["curves"]
        price_array = curves["price_euros_kwh"]

        # Parse timestamps - API returns naive datetimes in Europe/Madrid timezone
        madrid_tz = ZoneInfo("Europe/Madrid")
        first_date_naive = dt_util.parse_datetime(response["data"]["first_date"])
        first_date = first_date_naive.replace(tzinfo=madrid_tz)
        first_date_utc = dt_util.as_utc(first_date)

        timestamps = [
            first_date_utc + timedelta(hours=i) for i in range(EXPECTED_DATA_POINTS)
        ]

        last_date_naive = dt_util.parse_datetime(response["data"]["last_date"])
        last_date = last_date_naive.replace(tzinfo=madrid_tz)
        last_date_utc = dt_util.as_utc(last_date)

        return PriceData(
            timestamps=timestamps,
            prices=price_array,
            first_date=first_date_utc,
            last_date=last_date_utc,
        )

    async def _fetch_and_parse_compensation(self) -> PriceData:
        """Fetch and parse compensation price data."""
        response = await self._client.fetch_compensation_prices()

        curves = response["data"]["curves"]
        price_array = curves["compensation_euros_kwh"]  # Different field name

        # Parse timestamps - API returns naive datetimes in Europe/Madrid timezone
        madrid_tz = ZoneInfo("Europe/Madrid")
        first_date_naive = dt_util.parse_datetime(response["data"]["first_date"])
        first_date = first_date_naive.replace(tzinfo=madrid_tz)
        first_date_utc = dt_util.as_utc(first_date)

        timestamps = [
            first_date_utc + timedelta(hours=i) for i in range(EXPECTED_DATA_POINTS)
        ]

        last_date_naive = dt_util.parse_datetime(response["data"]["last_date"])
        last_date = last_date_naive.replace(tzinfo=madrid_tz)
        last_date_utc = dt_util.as_utc(last_date)

        return PriceData(
            timestamps=timestamps,
            prices=price_array,
            first_date=first_date_utc,
            last_date=last_date_utc,
        )

    @callback
    def _schedule_daily_update(self) -> None:
        """Schedule daily data fetch at 18:00 UTC."""
        if self._daily_update_listener:
            self._daily_update_listener()
            self._daily_update_listener = None

        now = dt_util.utcnow()
        target_time = now.replace(
            hour=DAILY_UPDATE_TIME.hour,
            minute=DAILY_UPDATE_TIME.minute,
            second=0,
            microsecond=0,
        )

        # If past today's update time, schedule for tomorrow
        if now >= target_time:
            target_time += timedelta(days=1)

        self._daily_update_listener = async_track_point_in_time(
            self.hass, self._async_daily_update, target_time
        )

    @callback
    def _schedule_hourly_refresh(self) -> None:
        """Schedule hourly state refresh at :00."""
        self._hourly_update_listener = async_track_time_change(
            self.hass, self._async_hourly_refresh, minute=0, second=0
        )

    async def _async_daily_update(self, _now: datetime) -> None:
        """Handle daily data fetch."""
        # Clear any pending retry handle that just fired
        self._retry_update_listener = None

        await self.async_refresh()

        # Schedule retry if failed
        if self._retry_count < MAX_RETRIES and not self.last_update_success:
            self._retry_count += 1
            retry_time = dt_util.utcnow() + timedelta(minutes=RETRY_INTERVAL_MINUTES)
            if self._retry_update_listener:
                self._retry_update_listener()

            self._retry_update_listener = async_track_point_in_time(
                self.hass, self._async_daily_update, retry_time
            )
        else:
            # Schedule next daily update
            self._schedule_daily_update()

    async def _async_hourly_refresh(self, _now: datetime) -> None:
        """Refresh entity states without fetching data."""
        # Trigger listener callbacks without new data fetch
        self.async_update_listeners()

    async def async_start(self) -> None:
        """Start the coordinator scheduling."""
        # Fetch data immediately on startup
        await self.async_refresh()

        # Schedule recurring updates
        self._schedule_daily_update()
        self._schedule_hourly_refresh()

    @callback
    def async_stop(self) -> None:
        """Stop the coordinator."""
        if self._daily_update_listener:
            self._daily_update_listener()
            self._daily_update_listener = None
        if self._retry_update_listener:
            self._retry_update_listener()
            self._retry_update_listener = None
        if self._hourly_update_listener:
            self._hourly_update_listener()
            self._hourly_update_listener = None
