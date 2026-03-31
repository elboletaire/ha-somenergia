from __future__ import annotations

from datetime import date, datetime
import importlib.util
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo


def load_price_timeline_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "som_energia"
        / "price_timeline.py"
    )
    spec = importlib.util.spec_from_file_location("price_timeline", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PriceTimelineTests(unittest.TestCase):
    def test_first_and_last_dates_are_inclusive_sample_timestamps(self) -> None:
        module = load_price_timeline_module()
        madrid_tz = ZoneInfo("Europe/Madrid")
        timeline = module.PriceTimeline(
            prices=[10.0, 20.0, 30.0],
            first_date=datetime(2026, 3, 31, 0, 0, tzinfo=madrid_tz),
            last_date=datetime(2026, 3, 31, 2, 0, tzinfo=madrid_tz),
        )

        self.assertEqual(len(timeline.timestamps), 3)
        self.assertEqual(
            timeline.timestamps[0].astimezone(madrid_tz),
            datetime(2026, 3, 31, 0, 0, tzinfo=madrid_tz),
        )
        self.assertEqual(
            timeline.timestamps[-1].astimezone(madrid_tz),
            datetime(2026, 3, 31, 2, 0, tzinfo=madrid_tz),
        )

    def test_prices_are_grouped_by_madrid_calendar_date_not_utc(self) -> None:
        module = load_price_timeline_module()
        madrid_tz = ZoneInfo("Europe/Madrid")
        timeline = module.PriceTimeline(
            prices=[1.0, 2.0, 3.0],
            first_date=datetime(2026, 3, 31, 0, 0, tzinfo=madrid_tz),
            last_date=datetime(2026, 3, 31, 2, 0, tzinfo=madrid_tz),
        )

        self.assertEqual(timeline.get_prices_for_date(date(2026, 3, 31)), [1.0, 2.0, 3.0])

    def test_timeline_supports_407_samples_across_dst_gap(self) -> None:
        module = load_price_timeline_module()
        madrid_tz = ZoneInfo("Europe/Madrid")
        prices = [float(i) for i in range(407)]
        timeline = module.PriceTimeline(
            prices=prices,
            first_date=datetime(2026, 3, 16, 1, 0, tzinfo=madrid_tz),
            last_date=datetime(2026, 4, 2, 0, 0, tzinfo=madrid_tz),
        )

        self.assertEqual(len(timeline.timestamps), 407)
        self.assertEqual(
            timeline.timestamps[0].astimezone(madrid_tz),
            datetime(2026, 3, 16, 1, 0, tzinfo=madrid_tz),
        )
        self.assertEqual(
            timeline.timestamps[-1].astimezone(madrid_tz),
            datetime(2026, 4, 2, 0, 0, tzinfo=madrid_tz),
        )
        self.assertEqual(
            timeline.get_price_at(datetime(2026, 3, 16, 0, 0, tzinfo=madrid_tz)),
            0.0,
        )
        self.assertEqual(
            timeline.get_price_at(datetime(2026, 4, 2, 0, 0, tzinfo=madrid_tz)),
            None,
        )

    def test_timeline_supports_408_samples_without_dst_gap(self) -> None:
        module = load_price_timeline_module()
        madrid_tz = ZoneInfo("Europe/Madrid")
        prices = [float(i) for i in range(408)]
        timeline = module.PriceTimeline(
            prices=prices,
            first_date=datetime(2026, 1, 1, 0, 0, tzinfo=madrid_tz),
            last_date=datetime(2026, 1, 17, 23, 0, tzinfo=madrid_tz),
        )

        self.assertEqual(len(timeline.timestamps), 408)
        self.assertEqual(
            timeline.timestamps[0].astimezone(madrid_tz),
            datetime(2026, 1, 1, 0, 0, tzinfo=madrid_tz),
        )
        self.assertEqual(
            timeline.timestamps[-1].astimezone(madrid_tz),
            datetime(2026, 1, 17, 23, 0, tzinfo=madrid_tz),
        )

    def test_point_lookup_uses_api_hour_offset(self) -> None:
        module = load_price_timeline_module()
        madrid_tz = ZoneInfo("Europe/Madrid")
        timeline = module.PriceTimeline(
            prices=[10.0, None],
            first_date=datetime(2026, 4, 1, 1, 0, tzinfo=madrid_tz),
            last_date=datetime(2026, 4, 1, 2, 0, tzinfo=madrid_tz),
        )

        self.assertEqual(
            timeline.get_price_at(datetime(2026, 4, 1, 0, 0, tzinfo=madrid_tz)),
            10.0,
        )
        self.assertIsNone(
            timeline.get_price_at(datetime(2026, 4, 1, 1, 0, tzinfo=madrid_tz))
        )


if __name__ == "__main__":
    unittest.main()
