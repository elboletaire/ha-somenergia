"""Data update coordinator for Som Energia."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

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
    GEO_ZONE,
    MAX_RETRIES,
    RETRY_INTERVAL_MINUTES,
    TARIFF_20TD,
    TARIFF_30TD,
    TARIFF_61TD,
)
from .price_timeline import PriceTimeline, parse_api_datetime

_LOGGER = logging.getLogger(__name__)


PriceData = PriceTimeline


@dataclass
class CoordinatorData:
    """Data structure returned by coordinator."""

    tariff_20td: PriceTimeline | None = None
    tariff_30td: PriceTimeline | None = None
    tariff_61td: PriceTimeline | None = None
    compensation: PriceTimeline | None = None
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
            error_message = self._format_update_error(err)

            # Keep previously fetched data available while we retry
            if self.data is not None:
                self._log_update_error(
                    f"{error_message}; keeping previously fetched prices"
                )
            else:
                self._log_update_error(error_message)

            raise UpdateFailed(error_message) from err

    async def _fetch_and_parse_tariff(self, tariff: str) -> PriceData:
        """Fetch and parse tariff price data."""
        response = await self._client.fetch_tariff_prices(tariff)

        # Parse response
        curves = response["data"]["curves"]
        price_array = curves["price_euros_kwh"]

        first_date = parse_api_datetime(response["data"]["first_date"])
        last_date = parse_api_datetime(response["data"]["last_date"])

        return PriceData(
            prices=price_array,
            first_date=first_date,
            last_date=last_date,
        )

    async def _fetch_and_parse_compensation(self) -> PriceData:
        """Fetch and parse compensation price data."""
        response = await self._client.fetch_compensation_prices()

        curves = response["data"]["curves"]
        price_array = curves["compensation_euros_kwh"]  # Different field name

        first_date = parse_api_datetime(response["data"]["first_date"])
        last_date = parse_api_datetime(response["data"]["last_date"])

        return PriceData(
            prices=price_array,
            first_date=first_date,
            last_date=last_date,
        )

    def _format_update_error(self, err: Exception) -> str:
        """Format an API update error for logging."""
        if isinstance(err, aiohttp.ClientResponseError):
            status_text = err.message or err.reason or "unknown error"
            return f"API request failed ({err.status}): {status_text}"
        if isinstance(err, asyncio.TimeoutError):
            return "API request timed out"
        return f"Error communicating with API: {err}"

    def _log_update_error(self, message: str) -> None:
        """Log an API update error with retry context."""
        _LOGGER.error(
            "%s; retrying in %s minute(s) (attempt %s/%s)",
            message,
            RETRY_INTERVAL_MINUTES,
            self._retry_count + 1,
            MAX_RETRIES,
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

    @callback
    def _schedule_retry(self) -> None:
        """Schedule a retry after a failed update."""
        if self._retry_count >= MAX_RETRIES:
            _LOGGER.error(
                "Maximum retry attempts reached (%s). Next attempt at scheduled daily update; "
                "try reloading the Som Energia integration if this persists.",
                MAX_RETRIES,
            )
            self._retry_count = 0
            self._schedule_daily_update()
            return

        retry_time = dt_util.utcnow() + timedelta(minutes=RETRY_INTERVAL_MINUTES)

        if self._retry_update_listener:
            self._retry_update_listener()

        self._retry_update_listener = async_track_point_in_time(
            self.hass, self._async_daily_update, retry_time
        )
        _LOGGER.info(
            "Retrying data fetch in %s minute(s) (attempt %s/%s)",
            RETRY_INTERVAL_MINUTES,
            self._retry_count + 1,
            MAX_RETRIES,
        )
        self._retry_count += 1

    async def _async_daily_update(self, _now: datetime) -> None:
        """Handle daily data fetch."""
        # Clear any pending retry handle that just fired
        if self._retry_update_listener:
            self._retry_update_listener()
        self._retry_update_listener = None

        try:
            await self.async_refresh()
        except UpdateFailed:
            # Error already logged in _async_update_data, schedule retry
            pass
        except Exception as err:  # pragma: no cover - defensive logging
            _LOGGER.exception("Unexpected error while refreshing Som Energia data: %s", err)

        if self.last_update_success:
            self._schedule_daily_update()
            return

        self._schedule_retry()

    async def _async_hourly_refresh(self, _now: datetime) -> None:
        """Refresh entity states without fetching data."""
        # Trigger listener callbacks without new data fetch
        self.async_update_listeners()

    async def async_start(self) -> None:
        """Start the coordinator scheduling."""
        # Schedule recurring updates
        self._schedule_hourly_refresh()
        await self._async_daily_update(dt_util.utcnow())

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
