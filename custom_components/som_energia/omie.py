"""Download and parse OMIE compensation (marginal price) data.

Quarter-hour marginal prices from OMIE are averaged into hourly values
and returned as a PriceTimeline for use as compensation fallback.

Reference file: marginalpdbc_YYYYMMDD.1
Columns: AÑO;MES;DIA;HORA;PRECIO;TRAMO
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from statistics import fmean

import aiohttp
import async_timeout

from .price_timeline import MADRID_TZ, PriceTimeline

_LOGGER = logging.getLogger(__name__)

OMIE_COMPENSATION_URL = (
    "https://www.omie.es/es/file-download"
    "?parents%5B0%5D=marginalpdbc"
    "&filename=marginalpdbc_{date_str}.1"
)
OMIE_TIMEOUT = 30
# OMIE prices are in EUR/MWh; convert to EUR/kWh
OMIE_PRICE_DIVISOR = 1000.0


async def fetch_omie_compensation(
    session: aiohttp.ClientSession,
    target_date: date | None = None,
) -> PriceTimeline | None:
    """Download and parse today's OMIE marginal price as compensation.

    Returns a PriceTimeline with hourly averaged prices, or None if
    the file is not yet published (404) or the download fails.
    """
    if target_date is None:
        target_date = date.today()

    date_str = target_date.strftime("%Y%m%d")
    url = OMIE_COMPENSATION_URL.format(date_str=date_str)

    async with async_timeout.timeout(OMIE_TIMEOUT):
        try:
            response = await session.get(url)
        except (aiohttp.ClientError, TimeoutError):
            _LOGGER.debug("OMIE download failed for %s", date_str)
            return None

    if response.status == 404:
        _LOGGER.debug("OMIE file not yet published for %s", date_str)
        return None

    try:
        response.raise_for_status()
        body = await response.text()
    except (aiohttp.ClientResponseError, UnicodeDecodeError):
        _LOGGER.debug("OMIE response error for %s", date_str)
        return None

    return _parse_omie_csv(body, target_date)


def _parse_omie_csv(body: str, file_date: date) -> PriceTimeline | None:
    """Parse CSV content into an hourly compensation PriceTimeline.

    Averages quarter-hour marginal prices (TRAMO 1-4) into hourly
    values and converts EUR/MWh → EUR/kWh.
    """
    hourly_prices: dict[int, list[float]] = {}
    lines = body.strip().splitlines()

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(";")
        if len(parts) < 6:
            continue
        try:
            # Columns: AÑO;MES;DIA;HORA;PRECIO;TRAMO
            hour = int(parts[3])
            price = float(parts[4].replace(",", "."))
        except (ValueError, IndexError):
            continue
        hourly_prices.setdefault(hour, []).append(price)

    if not hourly_prices:
        _LOGGER.debug("OMIE file for %s contained no price data", file_date)
        return None

    # Hour range is 1-24, so we need 24 averaged slots. Map them to
    # proper Madrid-time datetimes for the PriceTimeline: hour 1 maps
    # to 00:00-01:00 Madrid time, so the PriceTimeline slot is hour 0.
    avg_prices: list[float] = []
    for h in range(1, 25):
        quarters = hourly_prices.get(h, [])
        if quarters:
            avg_prices.append(fmean(quarters) / OMIE_PRICE_DIVISOR)
        else:
            avg_prices.append(0.0)  # fallback: zero when missing

    # OMIE hour 1 covers 00:00–01:00 Madrid. PriceTimeline.get_price_at
    # expects first_date at hour 1 (the Som API convention) and adds a
    # +1 offset internally. Set first_date to hour 1 so the two offsets
    # cancel out and each Madrid hour maps to the correct OMIE price.
    first_midnight = datetime(
        file_date.year, file_date.month, file_date.day, 1, 0, 0, tzinfo=MADRID_TZ
    )
    last_midnight = first_midnight + timedelta(hours=23)

    return PriceTimeline(
        prices=avg_prices,
        first_date=first_midnight,
        last_date=last_midnight,
    )
