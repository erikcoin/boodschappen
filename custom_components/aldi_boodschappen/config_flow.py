"""Config flow en opties."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AldiApiError, AldiAuthError, AldiClient
from .picnic import PicnicLibraryError, PicnicLoginError, PicnicNeeds2FA, PicnicSetup
from .const import (
    CONF_API_KEY,
    CONF_HOOGVLIET_SOURCE,
    CONF_APP_ID,
    CONF_PICNIC_PASSWORD,
    CONF_PICNIC_TOKEN,
    CONF_PICNIC_USERNAME,
    CONF_RESET_ENABLED,
    CONF_RESET_HOUR,
    CONF_RESET_WEEKDAY,
    CONF_STORES,
    DEFAULT_APP_ID,
    DEFAULT_HOOGVLIET_SOURCE,
    HOOGVLIET_SOURCES,
    DEFAULT_RESET_ENABLED,
    DEFAULT_RESET_HOUR,
    DEFAULT_RESET_WEEKDAY,
    DEFAULT_STORES,
    DOMAIN,
    STORE_ALDI,
    STORE_PICNIC,
    STORES,
    WEEKDAYS,
)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_STORES, default=defaults.get(CONF_STORES) or DEFAULT_STORES
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=STORES, multiple=True, translation_key="stores"
                )
            ),
            vol.Required(
                CONF_HOOGVLIET_SOURCE,
                default=defaults.get(CONF_HOOGVLIET_SOURCE, DEFAULT_HOOGVLIET_SOURCE),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=HOOGVLIET_SOURCES, translation_key="hoogvliet_source"
                )
            ),
            vol.Required(
                CONF_APP_ID, default=defaults.get(CONF_APP_ID, DEFAULT_APP_ID)
            ): selector.TextSelector(),
            vol.Optional(
                CONF_API_KEY, default=defaults.get(CONF_API_KEY, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_PICNIC_USERNAME, default=defaults.get(CONF_PICNIC_USERNAME, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_PICNIC_PASSWORD, default=defaults.get(CONF_PICNIC_PASSWORD, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(
                CONF_RESET_ENABLED,
                default=defaults.get(CONF_RESET_ENABLED, DEFAULT_RESET_ENABLED),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_RESET_WEEKDAY,
                default=defaults.get(CONF_RESET_WEEKDAY, DEFAULT_RESET_WEEKDAY),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=WEEKDAYS, translation_key="weekday"
                )
            ),
            vol.Required(
                CONF_RESET_HOUR,
                default=defaults.get(CONF_RESET_HOUR, DEFAULT_RESET_HOUR),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=23, step=1, mode="box")
            ),
        }
    )


async def _validate(hass, user_input: dict[str, Any]) -> dict[str, str]:
    """Controleer de keuze; geeft een foutcode terug of een lege dict.

    Alleen Aldi heeft een sleutel en wordt met een proefzoekopdracht getest.
    Hoogvliet heeft geen instellingen nodig.
    """
    stores = user_input.get(CONF_STORES) or []
    if not stores:
        return {"base": "no_store"}
    if STORE_ALDI not in stores:
        return {}
    if not str(user_input.get(CONF_API_KEY, "")).strip():
        return {CONF_API_KEY: "api_key_required"}

    client = AldiClient(
        async_get_clientsession(hass),
        user_input[CONF_APP_ID],
        user_input[CONF_API_KEY],
    )
    try:
        await client.search("appels", limit=1)
    except AldiAuthError:
        return {"base": "invalid_auth"}
    except AldiApiError:
        return {"base": "cannot_connect"}
    return {}


class _PicnicSteps:
    """Inloggen bij Picnic, eventueel met een verificatiecode. Gedeeld door config flow en opties."""

    _picnic: PicnicSetup | None = None
    _data: dict[str, Any]

    def _finish(self, data: dict[str, Any]) -> ConfigFlowResult:  # overschreven per flow
        raise NotImplementedError

    async def _picnic_gate(
        self, data: dict[str, Any], previous: dict[str, Any]
    ) -> tuple[ConfigFlowResult | None, dict[str, str]]:
        """(resultaat, fouten): een resultaat betekent 'ga door met de 2FA-stap'; (None, {}) is klaar."""
        if STORE_PICNIC not in (data.get(CONF_STORES) or []):
            data.pop(CONF_PICNIC_TOKEN, None)
            return None, {}
        username = str(data.get(CONF_PICNIC_USERNAME, "")).strip()
        password = str(data.get(CONF_PICNIC_PASSWORD, ""))
        if not username or not password:
            return None, {CONF_PICNIC_USERNAME: "picnic_required"}
        data[CONF_PICNIC_USERNAME] = username

        same = (
            previous.get(CONF_PICNIC_TOKEN)
            and previous.get(CONF_PICNIC_USERNAME) == username
            and previous.get(CONF_PICNIC_PASSWORD) == password
        )
        if same:
            data[CONF_PICNIC_TOKEN] = previous[CONF_PICNIC_TOKEN]
            return None, {}

        self._picnic = PicnicSetup(self.hass)
        try:
            data[CONF_PICNIC_TOKEN] = await self._picnic.login(username, password)
        except PicnicNeeds2FA:
            self._data = data
            return await self.async_step_picnic_channel(), {}
        except PicnicLibraryError:
            return None, {"base": "picnic_lib"}
        except PicnicLoginError:
            return None, {"base": "picnic_auth"}
        except AldiApiError:
            return None, {"base": "picnic_connect"}
        return None, {}

    async def async_step_picnic_channel(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._picnic.send_code(user_input["channel"])
            except AldiApiError:
                errors["base"] = "picnic_send"
            else:
                return await self.async_step_picnic_code()
        return self.async_show_form(
            step_id="picnic_channel",
            data_schema=vol.Schema(
                {
                    vol.Required("channel", default="SMS"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["SMS", "EMAIL"], translation_key="picnic_channel"
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_picnic_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._data[CONF_PICNIC_TOKEN] = await self._picnic.verify(user_input["code"])
            except PicnicLoginError:
                errors["base"] = "picnic_code"
            except AldiApiError:
                errors["base"] = "picnic_connect"
            else:
                return self._finish(self._data)
        return self.async_show_form(
            step_id="picnic_code",
            data_schema=vol.Schema({vol.Required("code"): selector.TextSelector()}),
            errors=errors,
        )


class AldiBoodschappenConfigFlow(_PicnicSteps, ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_RESET_HOUR] = int(user_input[CONF_RESET_HOUR])
            errors = await _validate(self.hass, user_input)
            if not errors:
                follow_up, errors = await self._picnic_gate(user_input, {})
                if follow_up is not None:
                    return follow_up
                if not errors:
                    return self._finish(user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input or {}),
            errors=errors,
        )

    def _finish(self, data: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(title="Aldi Boodschappen", data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return AldiBoodschappenOptionsFlow()


class AldiBoodschappenOptionsFlow(_PicnicSteps, OptionsFlow):
    def _finish(self, data: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data=data)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_RESET_HOUR] = int(user_input[CONF_RESET_HOUR])
            errors = await _validate(self.hass, user_input)
            if not errors:
                previous = {**self.config_entry.data, **self.config_entry.options}
                follow_up, errors = await self._picnic_gate(user_input, previous)
                if follow_up is not None:
                    return follow_up
                if not errors:
                    return self._finish(user_input)

        current = user_input or {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=_schema(current), errors=errors
        )
