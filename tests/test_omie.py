"""Unit tests for OMIE compensation parsing."""

from __future__ import annotations

from datetime import date, datetime
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo


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

    # Ensure parent package exists
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


def load_omie_module():
    """Load omie module, mocking unavailable dependencies."""
    for dep in ("aiohttp", "async_timeout"):
        if dep not in sys.modules:
            sys.modules[dep] = MagicMock()
    return _get_module("omie", "omie.py")


def load_price_timeline_module():
    """Load price_timeline module."""
    return _get_module("price_timeline", "price_timeline.py")


# Pre-load price_timeline (omie depends on it)
load_price_timeline_module()


class OmieParseCsvTests(unittest.TestCase):
    """Tests for _parse_omie_csv."""

    def setUp(self) -> None:
        self.module = load_omie_module()
        self.file_date = date(2026, 7, 5)

    def test_parses_quarter_hour_csv_into_hourly_averages(self) -> None:
        """24 hours * 4 quarters = 96 lines → 24 averaged hourly slots."""
        lines = []
        for h in range(1, 25):
            for q in range(1, 5):
                price = 1000.0 + (h - 1) * 10 + (q - 1) * 2
                lines.append(f"2026;07;05;{h};{price};{q}")
        body = "\n".join(lines)

        result = self.module._parse_omie_csv(body, self.file_date)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(len(result.prices), 24)
        self.assertAlmostEqual(result.prices[0], 1.003, places=5)
        self.assertAlmostEqual(result.prices[23], 1.233, places=5)

    def test_handles_comma_decimal_separator(self) -> None:
        """OMIE files use comma as decimal separator."""
        body = "2026;07;05;1;50,25;1\n2026;07;05;1;50,75;2\n2026;07;05;1;51,00;3\n2026;07;05;1;51,00;4"

        result = self.module._parse_omie_csv(body, self.file_date)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result.prices[0], 0.05075, places=5)

    def test_returns_none_for_empty_input(self) -> None:
        """Empty or comment-only body returns None."""
        self.assertIsNone(self.module._parse_omie_csv("", self.file_date))
        self.assertIsNone(self.module._parse_omie_csv("# comment only", self.file_date))

    def test_skips_malformed_lines(self) -> None:
        """Lines with too few fields or non-numeric prices are skipped."""
        body = (
            "2026;07;05;1;50.0;1\n"
            "bad;line\n"
            "2026;07;05;1;60.0;2\n"
            "2026;07;05;1;70.0;3\n"
            "2026;07;05;1;80.0;4\n"
        )

        result = self.module._parse_omie_csv(body, self.file_date)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result.prices[0], 0.065, places=5)

    def test_timeline_has_correct_dates(self) -> None:
        """Resulting PriceTimeline covers the full Madrid day."""
        body = "2026;07;05;1;100.0;1\n2026;07;05;1;100.0;2\n2026;07;05;1;100.0;3\n2026;07;05;1;100.0;4"

        result = self.module._parse_omie_csv(body, self.file_date)

        self.assertIsNotNone(result)
        assert result is not None
        madrid_tz = ZoneInfo("Europe/Madrid")
        self.assertEqual(
            result.first_date,
            datetime(2026, 7, 5, 1, 0, tzinfo=madrid_tz),
        )
        self.assertEqual(
            result.last_date,
            datetime(2026, 7, 6, 0, 0, tzinfo=madrid_tz),
        )
        self.assertEqual(len(result.timestamps), 24)

    def test_get_price_at_maps_omie_hours_correctly(self) -> None:
        """get_price_at returns the correct OMIE price for each Madrid hour.

        Before the hour-1 first_date fix, get_price_at(hour 0) would return
        OMIE hour 2's price (index 1) instead of hour 1's (index 0), and
        get_price_at(hour 23) would be out-of-bounds (None) instead of
        returning hour 24's price (index 23).
        """
        # Build 24 distinct hourly prices so every hour has a unique value
        lines = []
        for h in range(1, 25):
            for q in range(1, 5):
                lines.append(f"2026;07;05;{h};{float(h)};{q}")
        body = "\n".join(lines)

        result = self.module._parse_omie_csv(body, self.file_date)
        self.assertIsNotNone(result)
        assert result is not None

        madrid = ZoneInfo("Europe/Madrid")

        # OMIE hour 1 (00:00-01:00) → Madrid hour 0
        dt_h0 = datetime(2026, 7, 5, 0, 0, 0, tzinfo=madrid)
        self.assertAlmostEqual(result.get_price_at(dt_h0), 1.0 / 1000.0, places=5)

        # OMIE hour 12 (11:00-12:00) → Madrid hour 11
        dt_h11 = datetime(2026, 7, 5, 11, 0, 0, tzinfo=madrid)
        self.assertAlmostEqual(result.get_price_at(dt_h11), 12.0 / 1000.0, places=5)

        # OMIE hour 24 (23:00-00:00) → Madrid hour 23
        dt_h23 = datetime(2026, 7, 5, 23, 0, 0, tzinfo=madrid)
        self.assertAlmostEqual(result.get_price_at(dt_h23), 24.0 / 1000.0, places=5)

        # Boundary: hour 24 Madrid (next day) → out of range
        dt_h24 = datetime(2026, 7, 6, 0, 0, 0, tzinfo=madrid)
        self.assertIsNone(result.get_price_at(dt_h24))

    def test_has_usable_prices_for_today_returns_true(self) -> None:
        """OMIE-based timeline has usable prices for the target date."""
        body = "2026;07;05;12;42.0;1\n2026;07;05;12;43.0;2\n2026;07;05;12;44.0;3\n2026;07;05;12;45.0;4"

        result = self.module._parse_omie_csv(body, self.file_date)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.has_usable_prices_for_date(date(2026, 7, 5)))
        # OMIE hour 24 maps to 2026-07-06 00:00 Madrid (one slot leaks
        # into the next day), but a date two days later is out of range.
        self.assertFalse(result.has_usable_prices_for_date(date(2026, 7, 7)))


if __name__ == "__main__":
    unittest.main()
