"""Helpers for mapping Som Energia hourly price timelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

MADRID_TZ = ZoneInfo("Europe/Madrid")


def parse_api_datetime(value: str) -> datetime:
    """Parse an API datetime as a Europe/Madrid aware datetime."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MADRID_TZ)


@dataclass
class PriceTimeline:
    """Container for hourly price data from the API."""

    prices: list[float | None]
    first_date: datetime
    last_date: datetime
    timestamps: list[datetime] = field(init=False)

    def __post_init__(self) -> None:
        """Build timestamp boundaries from the inclusive first sample timestamp."""
        first_date_utc = self.first_date.astimezone(UTC)
        self.timestamps = [
            first_date_utc + timedelta(hours=i) for i in range(len(self.prices))
        ]

    def get_price_at(self, dt: datetime) -> float | None:
        """Get the price for a specific Madrid local hour."""
        target_madrid = dt.astimezone(MADRID_TZ).replace(
            minute=0, second=0, microsecond=0
        )
        target_utc = target_madrid.astimezone(UTC)
        first_date_utc = self.first_date.astimezone(UTC)
        hours_diff = int((target_utc - first_date_utc).total_seconds() / 3600) + 1

        if 0 <= hours_diff < len(self.prices):
            return self.prices[hours_diff]
        return None

    def get_prices_for_date(self, target_date: date) -> list[float]:
        """Get all non-null prices for a Madrid local calendar date."""
        return [
            price
            for ts, price in zip(self.timestamps, self.prices, strict=False)
            if price is not None and ts.astimezone(MADRID_TZ).date() == target_date
        ]
