"""Unit tests for coordinator fallback and retry behavior."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo


MADRID_TZ = ZoneInfo("Europe/Madrid")


# ---------------------------------------------------------------------------
# Mock unavailable dependencies before any module loading
# ---------------------------------------------------------------------------

def _ensure_mock(mod_name: str) -> MagicMock:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()
    return sys.modules[mod_name]


def _ensure_package(name: str) -> MagicMock:
    """Create a fake package in sys.modules with __path__ set."""
    if name not in sys.modules:
        pkg = type(sys)(name)
        pkg.__path__ = []
        pkg.__package__ = name
        sys.modules[name] = pkg
    return sys.modules[name]


# aiohttp needs real exception classes for except clauses
_aiohttp_mod = _ensure_mock("aiohttp")


class _FakeClientError(Exception):
    pass


class _FakeClientResponseError(_FakeClientError):
    def __init__(self, *args, **kwargs):
        super().__init__(*args)
        self.status = kwargs.get("status", 0)
        self.message = kwargs.get("message", "")
        self.reason = kwargs.get("reason", "")


_aiohttp_mod.ClientError = _FakeClientError
_aiohttp_mod.ClientResponseError = _FakeClientResponseError

_ensure_mock("async_timeout")

# Build the homeassistant package hierarchy
_ensure_package("homeassistant")
_ha_const = _ensure_mock("homeassistant.const")
_ha_const.CURRENCY_EURO = "€"
_ha_const.UnitOfEnergy = MagicMock()
_ha_const.UnitOfEnergy.KILO_WATT_HOUR = "kWh"

# homeassistant.util package + dt module with utcnow()
_ha_util_pkg = _ensure_package("homeassistant.util")
_ha_util_dt = MagicMock()
_ha_util_dt.utcnow = lambda: datetime(2026, 7, 5, 18, 0, 0, tzinfo=MADRID_TZ)
sys.modules["homeassistant.util.dt"] = _ha_util_dt
_ha_util_pkg.dt = _ha_util_dt

# homeassistant.config_entries
_ha_config_entries = _ensure_mock("homeassistant.config_entries")

# homeassistant.core
_ha_core = _ensure_mock("homeassistant.core")
_ha_core.callback = lambda f: f

# homeassistant.helpers sub-packages
_ensure_package("homeassistant.helpers")
_ha_aiohttp_client = _ensure_mock("homeassistant.helpers.aiohttp_client")
_ha_aiohttp_client.async_get_clientsession = MagicMock()
_ha_event = _ensure_mock("homeassistant.helpers.event")
_ha_event.async_track_point_in_time = MagicMock(return_value=MagicMock())
_ha_event.async_track_time_change = MagicMock(return_value=MagicMock())

# homeassistant.helpers.update_coordinator
_ha_update_coordinator = _ensure_mock("homeassistant.helpers.update_coordinator")


class _FakeDataUpdateCoordinator:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, name, update_interval, config_entry):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.config_entry = config_entry
        self.data = None
        self.last_update_success = False

    async def async_refresh(self):
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
        except Exception:
            self.last_update_success = False
            raise

    def async_update_listeners(self):
        pass


class _FakeUpdateFailed(Exception):
    pass


_ha_update_coordinator.DataUpdateCoordinator = _FakeDataUpdateCoordinator
_ha_update_coordinator.UpdateFailed = _FakeUpdateFailed


# ---------------------------------------------------------------------------
# Package setup for relative imports
# ---------------------------------------------------------------------------

_CUSTOM_COMPONENTS = (
    Path(__file__).resolve().parents[1] / "custom_components" / "som_energia"
)


def _get_module(name: str, file: str) -> object:
    """Load a module from the som_energia package, or return cached."""
    full_name = f"som_energia.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    if "som_energia" not in sys.modules:
        pkg = type(sys)("som_energia")
        pkg.__path__ = [str(_CUSTOM_COMPONENTS)]
        pkg.__package__ = "som_energia"
        sys.modules["som_energia"] = pkg

    module_path = _CUSTOM_COMPONENTS / file
    spec = importlib.util.spec_from_file_location(full_name, module_path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "som_energia"
    assert spec is not None and spec.loader is not None
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Pre-load dependencies
_get_module("price_timeline", "price_timeline.py")
_get_module("const", "const.py")
_get_module("omie", "omie.py")

# Now load coordinator (needs all of the above)
coordinator_mod = _get_module("coordinator", "coordinator.py")

PriceTimeline = sys.modules["som_energia.price_timeline"].PriceTimeline


# ---------------------------------------------------------------------------
# Helper: build PriceTimelines
# ---------------------------------------------------------------------------

def _timestamp_for_hour(
    day: int, hour: int, month: int = 1, year: int = 2026
) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=MADRID_TZ)


def _make_timeline(
    prices: list[float],
    first_hour: int = 0,
    day: int = 1,
    month: int = 1,
    year: int = 2026,
    num_days: int = 1,
):
    """Build a PriceTimeline covering num_days * 24 hours."""
    total = num_days * 24
    if len(prices) < total:
        prices = prices + [0.0] * (total - len(prices))
    first = _timestamp_for_hour(day, first_hour, month, year)
    last = first + timedelta(hours=total - 1)
    return PriceTimeline(prices=list(prices), first_date=first, last_date=last)


# ---------------------------------------------------------------------------
# OMIE fallback tests
# ---------------------------------------------------------------------------


class OmieFallbackTests(unittest.TestCase):
    """Test that compensation uses OMIE fallback when Som lacks today data."""

    def setUp(self) -> None:
        self.mod = coordinator_mod
        self.hass = MagicMock()
        self.config_entry = MagicMock()
        self.config_entry.data = {
            "tariff_20td": False,
            "tariff_30td": False,
            "tariff_61td": False,
            "compensation": True,
        }

    def _make_coordinator(self):
        with (
            patch(
                "som_energia.coordinator.async_get_clientsession",
            ),
            patch(
                "som_energia.coordinator.async_track_point_in_time",
                return_value=MagicMock(),
            ),
            patch(
                "som_energia.coordinator.async_track_time_change",
                return_value=MagicMock(),
            ),
        ):
            return self.mod.SomEnergiaPricingCoordinator(self.hass, self.config_entry)

    def test_compensation_returns_som_when_today_present(self) -> None:
        """Som has today prices → return Som data, no OMIE call."""
        coord = self._make_coordinator()
        today = date(2026, 7, 5)

        today_timeline = _make_timeline(
            [0.1] * 408, day=today.day, month=today.month, year=today.year
        )

        # Mock _fetch_compensation_from_som to return today data
        coord._fetch_compensation_from_som = AsyncMock(return_value=today_timeline)

        async def _run():
            return await coord._fetch_and_parse_compensation()

        result = asyncio.new_event_loop().run_until_complete(_run())

        self.assertIsNotNone(result)
        self.assertTrue(result.has_usable_prices_for_date(today))
        coord._fetch_compensation_from_som.assert_called_once()

    def test_compensation_falls_back_to_omie_when_som_lacks_today(self) -> None:
        """Som returns data but lacks today prices → OMIE fallback used."""
        coord = self._make_coordinator()
        today = date(2026, 7, 5)

        # Som returns old data (no today)
        past_timeline = _make_timeline([0.2] * 408, day=1, month=1, year=2026)
        coord._fetch_compensation_from_som = AsyncMock(return_value=past_timeline)

        # OMIE returns today data
        today_timeline = _make_timeline(
            [0.05] * 24, day=today.day, month=today.month, year=today.year, num_days=1
        )

        with patch(
            "som_energia.coordinator.omie.fetch_omie_compensation",
            AsyncMock(return_value=today_timeline),
        ):
            async def _run():
                return await coord._fetch_and_parse_compensation()

            result = asyncio.new_event_loop().run_until_complete(_run())

        self.assertIsNotNone(result)
        self.assertTrue(result.has_usable_prices_for_date(today))
        coord._fetch_compensation_from_som.assert_called_once()

    def test_compensation_som_exception_falls_back_to_omie(self) -> None:
        """When Som raises an exception and OMIE returns data, use OMIE."""
        coord = self._make_coordinator()
        today = date(2026, 7, 5)

        # Som raises a ClientError
        coord._fetch_compensation_from_som = AsyncMock(
            side_effect=_FakeClientError("connection error")
        )

        # OMIE returns today data
        today_timeline = _make_timeline(
            [0.05] * 24, day=today.day, month=today.month, year=today.year, num_days=1
        )

        with patch(
            "som_energia.coordinator.omie.fetch_omie_compensation",
            AsyncMock(return_value=today_timeline),
        ):
            async def _run():
                return await coord._fetch_and_parse_compensation()

            result = asyncio.new_event_loop().run_until_complete(_run())

        self.assertIsNotNone(result)
        self.assertTrue(result.has_usable_prices_for_date(today))
        coord._fetch_compensation_from_som.assert_called_once()

    def test_compensation_returns_som_when_omie_also_fails(self) -> None:
        """Both Som and OMIE fail for today → returns Som data anyway."""
        coord = self._make_coordinator()

        # Som returns old data (no today prices)
        past_timeline = _make_timeline([0.15] * 408, day=1, month=1, year=2026)
        coord._fetch_compensation_from_som = AsyncMock(return_value=past_timeline)

        # OMIE returns None (not published)
        with patch(
            "som_energia.coordinator.omie.fetch_omie_compensation",
            AsyncMock(return_value=None),
        ):
            async def _run():
                return await coord._fetch_and_parse_compensation()

            result = asyncio.new_event_loop().run_until_complete(_run())

        # Should still return the Som data even though it lacks today
        self.assertIsNotNone(result)

    def test_compensation_raises_when_som_fails_and_omie_fails(self) -> None:
        """When Som raises and OMIE also returns None, re-raise Som error."""
        coord = self._make_coordinator()

        som_error = _FakeClientError("connection refused")
        coord._fetch_compensation_from_som = AsyncMock(side_effect=som_error)

        with patch(
            "som_energia.coordinator.omie.fetch_omie_compensation",
            AsyncMock(return_value=None),
        ):
            async def _run():
                return await coord._fetch_and_parse_compensation()

            loop = asyncio.new_event_loop()
            with self.assertRaises(_FakeClientError):
                loop.run_until_complete(_run())
            loop.close()


# ---------------------------------------------------------------------------
# Retry-on-missing-data tests
# ---------------------------------------------------------------------------


class RetryOnMissingDataTests(unittest.TestCase):
    """Test that coordinator retries when enabled endpoints lack today data."""

    def setUp(self) -> None:
        self.mod = coordinator_mod
        self.today = date(2026, 7, 5)

    def test_check_today_data_raises_when_tariff_missing(self) -> None:
        """Enabled tariff lacks today data → UpdateFailed raised."""
        config_entry = MagicMock()
        config_entry.data = {
            "tariff_20td": True,
            "tariff_30td": False,
            "tariff_61td": False,
            "compensation": False,
        }
        hass = MagicMock()

        with (
            patch("som_energia.coordinator.async_get_clientsession"),
            patch("som_energia.coordinator.async_track_point_in_time"),
            patch("som_energia.coordinator.async_track_time_change"),
        ):
            coord = self.mod.SomEnergiaPricingCoordinator(hass, config_entry)

        past_timeline = _make_timeline([0.1] * 408, day=1, month=1, year=2026)
        data = self.mod.CoordinatorData(tariff_20td=past_timeline)

        with self.assertRaises(self.mod.UpdateFailed) as ctx:
            coord._check_today_data(data)

        self.assertIn("tariff 2.0TD", str(ctx.exception))

    def test_check_today_data_passes_when_all_have_today(self) -> None:
        """All enabled endpoints have today data → no error."""
        config_entry = MagicMock()
        config_entry.data = {
            "tariff_20td": True,
            "tariff_30td": False,
            "tariff_61td": False,
            "compensation": True,
        }
        hass = MagicMock()

        with (
            patch("som_energia.coordinator.async_get_clientsession"),
            patch("som_energia.coordinator.async_track_point_in_time"),
            patch("som_energia.coordinator.async_track_time_change"),
        ):
            coord = self.mod.SomEnergiaPricingCoordinator(hass, config_entry)

        today_timeline = _make_timeline(
            [0.1] * 408,
            day=self.today.day,
            month=self.today.month,
            year=self.today.year,
        )
        data = self.mod.CoordinatorData(
            tariff_20td=today_timeline,
            compensation=today_timeline,
        )

        coord._check_today_data(data)

    def test_check_today_data_raises_when_compensation_missing(self) -> None:
        """Enabled compensation lacks today data → UpdateFailed raised."""
        config_entry = MagicMock()
        config_entry.data = {
            "tariff_20td": False,
            "tariff_30td": False,
            "tariff_61td": False,
            "compensation": True,
        }
        hass = MagicMock()

        with (
            patch("som_energia.coordinator.async_get_clientsession"),
            patch("som_energia.coordinator.async_track_point_in_time"),
            patch("som_energia.coordinator.async_track_time_change"),
        ):
            coord = self.mod.SomEnergiaPricingCoordinator(hass, config_entry)

        data = self.mod.CoordinatorData(compensation=None)

        with self.assertRaises(self.mod.UpdateFailed) as ctx:
            coord._check_today_data(data)

        self.assertIn("compensation", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
