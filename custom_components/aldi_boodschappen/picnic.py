"""Zoeken bij Picnic via de (niet-officiële) API van de Picnic-app.

Picnic heeft geen webwinkel; alleen de app. De app praat met een API die niet is
gedocumenteerd en alleen werkt met een Picnic-account. We gebruiken daarvoor de
bibliotheek python-picnic-api2, die ook de Picnic-integratie van Home Assistant gebruikt.

Zuinig gebruik: één verzoek per zoekopdracht, nooit sneller dan één per paar seconden,
resultaten tien minuten onthouden, niets op de achtergrond behalve het dagelijks
bijwerken van prijzen van producten die op je lijst staan.
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from typing import Any, Protocol

from .api import AldiApiError
from .const import PICNIC_COUNTRY, SEARCH_CACHE_SECONDS, SEARCH_LIMIT, STORE_LABELS, STORE_PICNIC

_LOGGER = logging.getLogger(__name__)

MIN_INTERVAL = 3.0  # seconden tussen twee verzoeken
IMAGE_URL = "https://storefront-prod.nl.picnicinternational.com/static/images/{id}/medium.png"


class PicnicError(AldiApiError):
    """Zoeken bij Picnic is mislukt."""


class PicnicLibraryError(PicnicError):
    """De bibliotheek python-picnic-api2 ontbreekt."""


class PicnicLoginError(PicnicError):
    """Inloggen mislukt of opnieuw nodig (wachtwoord, verificatiecode)."""


class PicnicNeeds2FA(PicnicLoginError):
    """Picnic wil een verificatiecode."""


class StoreLike(Protocol):
    async def async_load(self) -> Any: ...
    async def async_save(self, data: Any) -> None: ...


def _library() -> SimpleNamespace:
    try:
        import python_picnic_api2 as lib
    except ImportError as err:
        raise PicnicLibraryError(
            "De Picnic-bibliotheek (python-picnic-api2) is niet geïnstalleerd; herstart Home Assistant"
        ) from err
    return SimpleNamespace(
        API=lib.PicnicAPI,
        Auth=lib.PicnicAuthError,
        TwoFA=lib.Picnic2FARequired,
        TwoFAError=lib.Picnic2FAError,
    )


def parse_search(result: Any) -> list[dict[str, Any]]:
    """Producten uit een zoekresultaat van de bibliotheek (`result.items`)."""
    products: list[dict[str, Any]] = []
    for item in getattr(result, "items", None) or []:
        pid = getattr(item, "id", None)
        name = getattr(item, "name", None)
        cents = getattr(item, "display_price", None)
        if not pid or not isinstance(name, str) or not name.strip():
            continue
        # Prijs in hele centen; sommige actietegels hebben alleen een prijsrange: overslaan
        if isinstance(cents, bool) or not isinstance(cents, int) or cents <= 0:
            continue
        image_id = getattr(item, "image_id", None)
        size = getattr(item, "unit_quantity", None)
        products.append(
            {
                "code": f"{STORE_PICNIC}:{pid}",
                "name": " ".join(name.split()),
                "brand": None,
                "description": size.strip() if isinstance(size, str) and size.strip() else None,
                "price": cents / 100,
                "price_valid_until": None,
                "old_price": None,
                "base_price": None,
                "currency": "€",
                "image": IMAGE_URL.format(id=image_id) if image_id else None,
                "url": None,  # Picnic heeft geen productpagina's op het web
                "available": True,
                "store": STORE_PICNIC,
                "store_name": STORE_LABELS[STORE_PICNIC],
            }
        )
    return products


class PicnicSetup:
    """Inloggen vanuit de config flow, eventueel met een verificatiecode (2FA)."""

    def __init__(self, hass, country: str = PICNIC_COUNTRY) -> None:
        self._hass = hass
        self._country = country
        self._api: Any = None

    async def login(self, username: str, password: str) -> str:
        """Geeft een token. Raises PicnicNeeds2FA als er een code nodig is."""
        return await self._hass.async_add_executor_job(self._login, username, password)

    def _login(self, username: str, password: str) -> str:
        lib = _library()
        api = lib.API(country_code=self._country)  # zonder gegevens: inloggen doen we zelf
        try:
            api.login(username, password)
        except lib.TwoFA as err:
            self._api = api
            raise PicnicNeeds2FA(str(err)) from err
        except lib.Auth as err:
            raise PicnicLoginError("Inloggen bij Picnic mislukt") from err
        except Exception as err:  # noqa: BLE001
            raise PicnicError(f"Picnic niet bereikbaar: {err}") from err
        token = api.session.auth_token
        if not token:
            raise PicnicLoginError("Picnic gaf geen toegangssleutel terug")
        return token

    async def send_code(self, channel: str) -> None:
        await self._hass.async_add_executor_job(self._send_code, channel)

    def _send_code(self, channel: str) -> None:
        lib = _library()
        try:
            self._api.generate_2fa_code(channel)
        except lib.TwoFAError as err:
            raise PicnicError(f"Code versturen mislukt: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise PicnicError(f"Picnic niet bereikbaar: {err}") from err

    async def verify(self, code: str) -> str:
        return await self._hass.async_add_executor_job(self._verify, code)

    def _verify(self, code: str) -> str:
        lib = _library()
        try:
            self._api.verify_2fa_code(code.strip())
        except lib.TwoFAError as err:
            raise PicnicLoginError(f"Code geweigerd: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise PicnicError(f"Picnic niet bereikbaar: {err}") from err
        token = self._api.session.auth_token
        if not token:
            raise PicnicLoginError("Picnic gaf geen toegangssleutel terug")
        return token


class PicnicClient:
    """Zoekt producten bij Picnic; vertraagd en onthouden."""

    def __init__(
        self,
        hass,
        username: str,
        password: str,
        token: str | None,
        store: StoreLike | None = None,
        country: str = PICNIC_COUNTRY,
    ) -> None:
        self._hass = hass
        self._username = username or ""
        self._password = password or ""
        self._seed = token or ""  # token uit de instellingen
        self._token = token or ""
        self._store = store
        self._country = country
        self._api: Any = None
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    async def async_load(self) -> None:
        """Een bij gebruik vernieuwd token heeft voorrang, tenzij de instellingen veranderd zijn."""
        if self._store is None:
            return
        data = await self._store.async_load()
        if isinstance(data, dict) and data.get("seed") == self._seed and data.get("token"):
            self._token = data["token"]

    async def _remember_token(self) -> None:
        token = getattr(getattr(self._api, "session", None), "auth_token", None)
        if token and token != self._token:
            self._token = token
            if self._store is not None:
                await self._store.async_save({"seed": self._seed, "token": token})

    async def search(self, query: str, limit: int = SEARCH_LIMIT) -> list[dict[str, Any]]:
        key = " ".join(query.lower().split())
        if len(key) < 2:
            return []
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < SEARCH_CACHE_SECONDS:
            return [dict(p) for p in cached[1][:limit]]

        async with self._lock:
            wait = MIN_INTERVAL - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()
            products = await self._hass.async_add_executor_job(self._search_sync, key)
            await self._remember_token()
        self._cache[key] = (time.monotonic(), products)
        return [dict(p) for p in products[:limit]]

    def _connect(self, lib: SimpleNamespace, fresh_login: bool = False) -> Any:
        if self._token and not fresh_login:
            return lib.API(auth_token=self._token, country_code=self._country)
        if not (self._username and self._password):
            raise PicnicLoginError("Geen Picnic-gegevens ingesteld")
        api = lib.API(country_code=self._country)
        api.login(self._username, self._password)
        return api

    def _search_sync(self, query: str) -> list[dict[str, Any]]:
        lib = _library()
        try:
            if self._api is None:
                self._api = self._connect(lib)
            try:
                result = self._api.search(query)
            except lib.Auth:
                # Token verlopen: één keer opnieuw inloggen met het wachtwoord
                self._api = self._connect(lib, fresh_login=True)
                result = self._api.search(query)
        except lib.TwoFA as err:
            self._api = None
            raise PicnicLoginError(
                "Picnic vraagt een verificatiecode: log opnieuw in via de instellingen van de integratie"
            ) from err
        except lib.Auth as err:
            self._api = None
            raise PicnicLoginError(
                "Inloggen bij Picnic mislukt: controleer je gegevens in de instellingen"
            ) from err
        except PicnicError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Zoeken bij Picnic mislukt: %r", err)
            raise PicnicError(f"Picnic gaf een onverwacht antwoord: {err}") from err
        return parse_search(result)
