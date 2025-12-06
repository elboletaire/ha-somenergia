"""Config flow for the Som Energia integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_COMPENSATION,
    CONF_TARIFF_20TD,
    CONF_TARIFF_30TD,
    CONF_TARIFF_61TD,
    DOMAIN,
    TARIFF_20TD,
    TARIFF_30TD,
    TARIFF_61TD,
)
from .coordinator import SomEnergiaPricingClient

_LOGGER = logging.getLogger(__name__)


class SomEnergiaPricingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Som Energia."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate at least one tariff selected
            selected_tariffs = [
                key
                for key in [
                    CONF_TARIFF_20TD,
                    CONF_TARIFF_30TD,
                    CONF_TARIFF_61TD,
                    CONF_COMPENSATION,
                ]
                if user_input.get(key, False)
            ]

            if not selected_tariffs:
                errors["base"] = "no_tariff_selected"
            else:
                # Test API connectivity with first selected tariff
                session = async_get_clientsession(self.hass)
                client = SomEnergiaPricingClient(session)

                try:
                    # Test with first non-compensation tariff, or compensation
                    if user_input.get(CONF_TARIFF_20TD):
                        await client.fetch_tariff_prices(TARIFF_20TD)
                    elif user_input.get(CONF_TARIFF_30TD):
                        await client.fetch_tariff_prices(TARIFF_30TD)
                    elif user_input.get(CONF_TARIFF_61TD):
                        await client.fetch_tariff_prices(TARIFF_61TD)
                    else:
                        await client.fetch_compensation_prices()

                except TimeoutError:
                    errors["base"] = "timeout_connect"
                except Exception:  # Allowed in config flow
                    _LOGGER.exception("Unexpected exception")
                    errors["base"] = "cannot_connect"
                else:
                    # Create unique_id based on selected tariffs
                    unique_id = "_".join(sorted(selected_tariffs))
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title="Som Energia",
                        data=user_input,
                    )

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_TARIFF_20TD, default=False): bool,
                vol.Optional(CONF_TARIFF_30TD, default=False): bool,
                vol.Optional(CONF_TARIFF_61TD, default=False): bool,
                vol.Optional(CONF_COMPENSATION, default=False): bool,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )
