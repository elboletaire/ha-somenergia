"""The Som Energia integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import SomEnergiaPricingCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

type SomEnergiaPricingConfigEntry = ConfigEntry[SomEnergiaPricingCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: SomEnergiaPricingConfigEntry
) -> bool:
    """Set up Som Energia from a config entry."""
    coordinator = SomEnergiaPricingCoordinator(hass, entry)

    # Start coordinator (performs initial fetch and scheduling)
    await coordinator.async_start()

    # Store coordinator in runtime_data
    entry.runtime_data = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SomEnergiaPricingConfigEntry
) -> bool:
    """Unload a config entry."""
    # Stop coordinator scheduling
    entry.runtime_data.async_stop()

    # Unload platforms
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
