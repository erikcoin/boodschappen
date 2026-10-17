"""Constanten voor Aldi Boodschappen."""

from __future__ import annotations

DOMAIN = "aldi_boodschappen"

CONF_APP_ID = "app_id"
CONF_API_KEY = "api_key"
CONF_STORES = "stores"
CONF_RESET_ENABLED = "reset_enabled"
CONF_RESET_WEEKDAY = "reset_weekday"
CONF_RESET_HOUR = "reset_hour"

# Application ID is geen geheim (het staat in elke zoek-URL van aldi.nl); de
# API-sleutel vul je zelf in bij het instellen.
DEFAULT_APP_ID = "2HU29PF6BH"
ALGOLIA_INDEX = "an_prd_nl_nl_products2"
PRODUCT_BASE_URL = "https://www.aldi.nl/product"
USER_AGENT = "HomeAssistant-AldiBoodschappen/0.3 (persoonlijk gebruik)"

STORE_ALDI = "aldi"
STORE_HOOGVLIET = "hoogvliet"
STORE_PICNIC = "picnic"
STORES = [STORE_ALDI, STORE_HOOGVLIET, STORE_PICNIC]
STORE_LABELS = {STORE_ALDI: "Aldi", STORE_HOOGVLIET: "Hoogvliet", STORE_PICNIC: "Picnic"}

# Picnic: geen website, alleen de app. We gebruiken dezelfde (niet-gedocumenteerde) API als de
# app, via de bibliotheek python-picnic-api2 die ook de Picnic-integratie van Home Assistant gebruikt.
CONF_PICNIC_USERNAME = "picnic_username"
CONF_PICNIC_PASSWORD = "picnic_password"
CONF_PICNIC_TOKEN = "picnic_token"
PICNIC_COUNTRY = "NL"
PICNIC_STORAGE_KEY = f"{DOMAIN}.picnic"
DEFAULT_STORES = [STORE_ALDI]

# Hoogvliet: hoogvliet.nl verbiedt in robots.txt het zoekpad en de Intershop-API
# voor bots. Daarom gebruiken we de open prijsdata van Checkjebon (MIT-project,
# data "may be reused in other projects"), die we hooguit één keer per dag ophalen.
CHECKJEBON_URL = (
    "https://raw.githubusercontent.com/supermarkt/checkjebon/main/data/supermarkets.json"
)
HOOGVLIET_BASE_URL = "https://www.hoogvliet.com"
HOOGVLIET_API_URL = "https://hoogvliet.nl/api/graphql"
CONF_HOOGVLIET_SOURCE = "hoogvliet_source"
HOOGVLIET_SOURCE_CHECKJEBON = "checkjebon"
HOOGVLIET_SOURCE_WEBSITE = "website"
HOOGVLIET_SOURCES = [HOOGVLIET_SOURCE_CHECKJEBON, HOOGVLIET_SOURCE_WEBSITE]
DEFAULT_HOOGVLIET_SOURCE = HOOGVLIET_SOURCE_CHECKJEBON
HOOGVLIET_STORAGE_KEY = f"{DOMAIN}.hoogvliet"
HOOGVLIET_MAX_AGE = 24 * 3600
HOOGVLIET_MAX_BYTES = 250 * 1024 * 1024  # veiligheidsgrens voor de download

DEFAULT_RESET_ENABLED = True
DEFAULT_RESET_WEEKDAY = "sat"
DEFAULT_RESET_HOUR = 6

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

STORAGE_KEY = f"{DOMAIN}.items"
STORAGE_VERSION = 1

SIGNAL_UPDATED = f"{DOMAIN}_updated"

PANEL_URL = "aldi-boodschappen"
PANEL_TITLE = "Boodschappen"
PANEL_ICON = "mdi:cart"
STATIC_URL = "/aldi_boodschappen_static"

SEARCH_LIMIT = 48
SEARCH_CACHE_SECONDS = 600
