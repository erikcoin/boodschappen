"""Aldi Boodschappen: boodschappenlijst met producten uit het Aldi-assortiment."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.storage import Store

from .api import AldiApiError, AldiClient
from .const import (
    CONF_API_KEY,
    CONF_APP_ID,
    CONF_HOOGVLIET_SOURCE,
    CONF_RESET_ENABLED,
    CONF_RESET_HOUR,
    CONF_RESET_WEEKDAY,
    CONF_STORES,
    DEFAULT_RESET_ENABLED,
    DEFAULT_RESET_HOUR,
    DEFAULT_APP_ID,
    DEFAULT_RESET_WEEKDAY,
    DEFAULT_STORES,
    DOMAIN,
    HOOGVLIET_SOURCE_WEBSITE,
    HOOGVLIET_STORAGE_KEY,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL,
    SIGNAL_UPDATED,
    STATIC_URL,
    STORAGE_VERSION,
    STORE_ALDI,
    STORE_HOOGVLIET,
    WEEKDAYS,
)
from .hoogvliet_web import HoogvlietWebSearch
from .hoogvliet import HoogvlietClient, clean_name, image_url, product_id
from .manager import ShoppingManager
from .search import SearchService

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.TODO]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_NEW_WEEK = "new_week"
SERVICE_ADD_ITEM = "add_item"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Eenmalige setup: websocket-commando's en frontend-bestanden."""
    hass.data.setdefault(DOMAIN, {})

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                STATIC_URL,
                str(Path(__file__).parent / "frontend"),
                cache_headers=False,
            )
        ]
    )

    websocket_api.async_register_command(hass, ws_search)
    websocket_api.async_register_command(hass, ws_items)
    websocket_api.async_register_command(hass, ws_add)
    websocket_api.async_register_command(hass, ws_update)
    websocket_api.async_register_command(hass, ws_remove)
    websocket_api.async_register_command(hass, ws_new_week)
    websocket_api.async_register_command(hass, ws_refresh_prices)
    websocket_api.async_register_command(hass, ws_subscribe)
    return True


def _runtime(hass: HomeAssistant) -> dict[str, Any] | None:
    return hass.data.get(DOMAIN, {}).get("runtime")


async def async_refresh_prices(hass: HomeAssistant) -> int:
    """Zoek de producten op de lijst opnieuw op en werk hun prijs bij."""
    runtime = _runtime(hass)
    if runtime is None:
        return 0
    manager: ShoppingManager = runtime["manager"]
    search: SearchService = runtime["search"]

    codes = {i["code"] for i in manager.items if i.get("code")}
    names = list(dict.fromkeys(i["name"] for i in manager.items if i.get("code")))
    updates: dict[str, dict[str, Any]] = {}
    for name in names[:40]:  # begrenzen: één zoekopdracht per uniek product
        try:
            results = await search.lookup(name)
        except AldiApiError as err:
            _LOGGER.warning("Prijzen bijwerken afgebroken: %s", err)
            break
        for product in results:
            if product["code"] in codes:
                updates[product["code"]] = {
                    "price": product["price"],
                    "price_valid_until": product["price_valid_until"],
                }
    return manager.update_prices(updates)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    options = {**entry.data, **entry.options}

    manager = ShoppingManager(hass)
    await manager.async_load()
    # Hoogvliet-producten die vóór de foto-ondersteuning zijn toegevoegd: foto uit de link afleiden
    manager.backfill_images(
        lambda item: image_url(item["url"]) if item.get("store") == STORE_HOOGVLIET else None
    )
    session = async_get_clientsession(hass)

    # Winkels waarin gezocht wordt (instelbaar). Bestaande installaties zonder
    # deze instelling zoeken alleen bij Aldi, zoals voorheen.
    stores = options.get(CONF_STORES) or DEFAULT_STORES
    clients: dict[str, Any] = {}
    if STORE_ALDI in stores:
        clients[STORE_ALDI] = AldiClient(
            session,
            options.get(CONF_APP_ID, DEFAULT_APP_ID),
            options.get(CONF_API_KEY, ""),
        )
    if STORE_HOOGVLIET in stores:
        live = options.get(CONF_HOOGVLIET_SOURCE) == HOOGVLIET_SOURCE_WEBSITE
        hoogvliet = HoogvlietClient(
            session,
            Store(hass, STORAGE_VERSION, HOOGVLIET_STORAGE_KEY),
            web=HoogvlietWebSearch(session) if live else None,
        )
        clients[STORE_HOOGVLIET] = hoogvliet

        def _repair_hoogvliet(item: dict) -> bool:
            if item.get("store") != STORE_HOOGVLIET:
                return False
            changed = False
            if item.get("url") and not product_id(item["url"]):
                # Eerdere versies bewaarden een gegokte link zonder productnummer: die klopt niet
                item["url"] = None
                changed = True
            cleaned = clean_name(item.get("name") or "")
            if cleaned and cleaned != item.get("name"):
                item["name"] = cleaned
                changed = True
            return changed

        manager.repair_items(_repair_hoogvliet)

        def _backfill_hoogvliet() -> None:
            # Producten die al op de lijst staan: ontbrekende link en foto aanvullen
            codes = {
                i["code"]
                for i in manager.items
                if i.get("store") == STORE_HOOGVLIET
                and i.get("code")
                and not (i.get("url") and i.get("image"))
            }
            if codes:
                manager.backfill_missing(hoogvliet.public_by_codes(codes))

        hoogvliet.add_listener(_backfill_hoogvliet)
        # De prijzen staan in een groot bestand: ophalen op de achtergrond, zodat
        # het opstarten van Home Assistant er niet op wacht, en daarna dagelijks.
        entry.async_create_background_task(
            hass, hoogvliet.async_refresh_safe(), "aldi_boodschappen hoogvliet"
        )

        async def _daily_hoogvliet(now: datetime) -> None:
            await hoogvliet.async_refresh_safe()

        entry.async_on_unload(
            async_track_time_interval(hass, _daily_hoogvliet, timedelta(hours=24))
        )

    hass.data[DOMAIN]["runtime"] = {
        "manager": manager,
        "search": SearchService(clients),
    }

    # Paneel in de zijbalk
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="aldi-boodschappen-panel",
        frontend_url_path=PANEL_URL,
        module_url=f"{STATIC_URL}/aldi-boodschappen-panel.js",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        require_admin=False,
        embed_iframe=False,
    )

    # Wekelijkse reset
    if options.get(CONF_RESET_ENABLED, DEFAULT_RESET_ENABLED):
        weekday = options.get(CONF_RESET_WEEKDAY, DEFAULT_RESET_WEEKDAY)
        hour = int(options.get(CONF_RESET_HOUR, DEFAULT_RESET_HOUR))

        @callback
        def _weekly(now: datetime) -> None:
            if WEEKDAYS[now.weekday()] == weekday:
                _LOGGER.debug("Automatische reset voor nieuwe week")
                manager.new_week()
                hass.async_create_task(async_refresh_prices(hass))

        entry.async_on_unload(
            async_track_time_change(hass, _weekly, hour=hour, minute=0, second=0)
        )

    # Services
    async def _handle_new_week(call: ServiceCall) -> None:
        manager.new_week()
        await async_refresh_prices(hass)

    async def _handle_add_item(call: ServiceCall) -> None:
        manager.add(
            call.data["name"],
            quantity=call.data.get("quantity", 1),
            recurring=call.data.get("recurring", False),
        )

    hass.services.async_register(DOMAIN, SERVICE_NEW_WEEK, _handle_new_week)
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_ITEM,
        _handle_add_item,
        schema=vol.Schema(
            {
                vol.Required("name"): cv.string,
                vol.Optional("quantity", default=1): vol.All(
                    vol.Coerce(int), vol.Range(min=1)
                ),
                vol.Optional("recurring", default=False): cv.boolean,
            }
        ),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.services.async_remove(DOMAIN, SERVICE_NEW_WEEK)
        hass.services.async_remove(DOMAIN, SERVICE_ADD_ITEM)
        from homeassistant.components import frontend

        frontend.async_remove_panel(hass, PANEL_URL)
        hass.data[DOMAIN].pop("runtime", None)
    return unloaded


# --------------------------------------------------------------------------- #
# Websocket API (gebruikt door het paneel)
# --------------------------------------------------------------------------- #


def _ensure_runtime(hass: HomeAssistant, connection, msg) -> dict[str, Any] | None:
    runtime = _runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_loaded", "Integratie is niet geladen")
    return runtime


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/search", vol.Required("query"): cv.string}
)
@websocket_api.async_response
async def ws_search(hass: HomeAssistant, connection, msg) -> None:
    runtime = _ensure_runtime(hass, connection, msg)
    if runtime is None:
        return
    try:
        results, warnings = await runtime["search"].search(msg["query"])
    except AldiApiError as err:
        connection.send_error(msg["id"], "search_failed", str(err))
        return
    connection.send_result(msg["id"], {"results": results, "warnings": warnings})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/items"})
@callback
def ws_items(hass: HomeAssistant, connection, msg) -> None:
    runtime = _ensure_runtime(hass, connection, msg)
    if runtime is None:
        return
    connection.send_result(msg["id"], {"items": runtime["manager"].items})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/add",
        vol.Required("name"): cv.string,
        vol.Optional("product"): dict,
        vol.Optional("quantity", default=1): vol.Coerce(int),
        vol.Optional("recurring", default=False): cv.boolean,
    }
)
@websocket_api.async_response
async def ws_add(hass: HomeAssistant, connection, msg) -> None:
    runtime = _ensure_runtime(hass, connection, msg)
    if runtime is None:
        return
    product = msg.get("product")
    if product:
        product = await runtime["search"].enrich(product)  # bijv. een foto, best effort
    item = runtime["manager"].add(
        msg["name"], product, msg["quantity"], msg["recurring"]
    )
    connection.send_result(msg["id"], {"item": item})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/update",
        vol.Required("item_id"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("checked"): cv.boolean,
        vol.Optional("recurring"): cv.boolean,
        vol.Optional("quantity"): vol.Coerce(int),
    }
)
@callback
def ws_update(hass: HomeAssistant, connection, msg) -> None:
    runtime = _ensure_runtime(hass, connection, msg)
    if runtime is None:
        return
    changes = {k: msg[k] for k in ("name", "checked", "recurring", "quantity") if k in msg}
    item = runtime["manager"].update(msg["item_id"], **changes)
    if item is None:
        connection.send_error(msg["id"], "not_found", "Item niet gevonden")
        return
    connection.send_result(msg["id"], {"item": item})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/remove", vol.Required("item_id"): cv.string}
)
@callback
def ws_remove(hass: HomeAssistant, connection, msg) -> None:
    runtime = _ensure_runtime(hass, connection, msg)
    if runtime is None:
        return
    connection.send_result(msg["id"], {"removed": runtime["manager"].remove(msg["item_id"])})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/new_week"})
@callback
def ws_new_week(hass: HomeAssistant, connection, msg) -> None:
    runtime = _ensure_runtime(hass, connection, msg)
    if runtime is None:
        return
    runtime["manager"].new_week()
    hass.async_create_task(async_refresh_prices(hass))
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/refresh_prices"})
@websocket_api.async_response
async def ws_refresh_prices(hass: HomeAssistant, connection, msg) -> None:
    if _ensure_runtime(hass, connection, msg) is None:
        return
    updated = await async_refresh_prices(hass)
    connection.send_result(msg["id"], {"updated": updated})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection, msg) -> None:
    runtime = _ensure_runtime(hass, connection, msg)
    if runtime is None:
        return
    manager: ShoppingManager = runtime["manager"]

    @callback
    def _send() -> None:
        connection.send_message(
            websocket_api.event_message(msg["id"], {"items": manager.items})
        )

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(
        hass, SIGNAL_UPDATED, _send
    )
    connection.send_result(msg["id"])
    _send()
