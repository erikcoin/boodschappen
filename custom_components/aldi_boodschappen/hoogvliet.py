"""Hoogvliet-producten en -prijzen via de open data van Checkjebon.

hoogvliet.nl verbiedt in robots.txt het zoekpad en de Intershop-API voor bots,
dus daar gaan we niet doorheen. Checkjebon (github.com/supermarkt/checkjebon,
MIT-licentie) verzamelt de prijzen van o.a. Hoogvliet en stelt die beschikbaar
voor hergebruik. We halen dat bestand hooguit één keer per dag op, bewaren alleen
de Hoogvliet-producten lokaal en zoeken daar zelf in.

LET OP: dit zijn de prijzen zoals Checkjebon ze het laatst heeft vastgelegd, geen
live prijzen van de webshop.

Foto's: Checkjebon heeft die niet, maar Hoogvliet zet ze op een voorspelbaar adres,
https://static.hoogvliet.nl/ecom/product/<productnummer>.jpg, en het productnummer
staat achter in de productlink. We leiden het adres dus af uit de link, zonder iets
bij Hoogvliet op te halen; de browser laadt de foto zelf.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlparse

import aiohttp

from .api import AldiApiError
from .const import (
    CHECKJEBON_URL,
    HOOGVLIET_BASE_URL,
    HOOGVLIET_MAX_AGE,
    HOOGVLIET_MAX_BYTES,
    SEARCH_LIMIT,
    STORE_HOOGVLIET,
    STORE_LABELS,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)


class HoogvlietError(AldiApiError):
    """Fout bij het ophalen of doorzoeken van de Hoogvliet-data."""


class StoreLike(Protocol):
    async def async_load(self) -> Any: ...
    async def async_save(self, data: Any) -> None: ...


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def normalize(text: str) -> str:
    """Kleine letters, zonder accenten, leestekens als spatie."""
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _score(norm_name: str, terms: list[str]) -> int | None:
    """Lager = beter. None = geen match (alle zoekwoorden moeten voorkomen)."""
    words = norm_name.split()
    score = 0
    for term in terms:
        if term not in norm_name:
            return None
        if any(w == term for w in words):
            score += 0  # heel woord
        elif any(w.startswith(term) for w in words):
            score += 2  # begin van een woord
        else:
            score += 4  # midden in een woord
    if words and not words[0].startswith(terms[0]):
        score += 1
    return score


IMAGE_URL = "https://static.hoogvliet.nl/ecom/product/{id}.jpg"
_HOSTS = ("hoogvliet.com", "www.hoogvliet.com", "hoogvliet.nl", "www.hoogvliet.nl")
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](https://[^"\']+)["\']',
    re.IGNORECASE,
)
_OG_IMAGE_RE_REVERSED = re.compile(
    r'<meta[^>]+content=["\'](https://[^"\']+)["\'][^>]+property=["\']og:image["\']',
    re.IGNORECASE,
)


def product_id(url: Any) -> str | None:
    """'…/product/pink-lady-appels-op-schaal-726992000' -> '726992000'."""
    if not isinstance(url, str):
        return None
    path = urlparse(url).path.rstrip("/")
    match = re.search(r"[-/](\d{6,})(?:\.\w+)?$", path)
    return match.group(1) if match else None


def image_url(url: Any) -> str | None:
    """Foto-adres afleiden uit een productlink, of None als er geen productnummer in staat."""
    pid = product_id(url)
    return IMAGE_URL.format(id=pid) if pid else None


_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~%-]*")
_PATH_RE = re.compile(r"[A-Za-z0-9._~%/-]+")

# Verwerking van het Checkjebon-bestand. Ophogen als de uitkomst anders wordt, zodat
# een eerder opgeslagen catalogus meteen opnieuw wordt opgebouwd.
PARSER_VERSION = 3


def clean_name(name: str) -> str:
    """Haal een dubbel voorvoegsel weg: 'Pink lady Pink lady op schaal' -> 'Pink lady op schaal'."""
    words = name.split()
    for k in range(1, len(words) // 2 + 1):
        if [w.lower() for w in words[:k]] == [w.lower() for w in words[k : 2 * k]]:
            return " ".join(words[k:])
    return name


def _link(value: Any, base: Any = None) -> str | None:
    """Maak er een volledige productlink van, welke vorm Checkjebon die ook geeft.

    Herkent: een volledige URL, '//host/pad', '/pad', 'pad/naam', 'www.hoogvliet.com/pad'
    en alleen een naam-met-nummer ('pink-lady-appels-op-schaal-726992000'). Een winkel-
    breed voorvoegsel (`base`) uit het bestand gaat voor op ons eigen domein.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("//"):
        return f"https:{value}"
    if "/" in value and urlparse(f"//{value}").hostname in _HOSTS:
        return f"https://{value}"

    prefix = base.strip().rstrip("/") if isinstance(base, str) and base.startswith("http") else None
    root = prefix or HOOGVLIET_BASE_URL
    if value.startswith("/"):
        return f"{root}{value}"
    if "/" in value:
        return f"{root}/{value}" if _PATH_RE.fullmatch(value) else None
    if _SLUG_RE.fullmatch(value):
        has_path = bool(prefix and urlparse(prefix).path.strip("/"))
        return f"{root}/{value}" if has_path else f"{root}/product/{value}"
    return None


def parse_checkjebon(raw: bytes | str) -> list[dict[str, Any]]:
    """Haal de Hoogvliet-producten uit het Checkjebon-bestand.

    Verwachte vorm (afgeleid van de projectbeschrijving, niet zelf gecontroleerd):
    een lijst supermarkten, elk met een naam en een lijst producten met naam,
    prijs, hoeveelheid en link. De sleutels worden daarom op meerdere plekken
    gezocht.
    """
    try:
        data = json.loads(raw)
    except ValueError as err:
        raise HoogvlietError(f"Checkjebon-data is geen geldige JSON: {err}") from err

    if isinstance(data, dict):
        data = _first(data.get("supermarkets"), data.get("data"), data.get("stores")) or []
    if not isinstance(data, list):
        raise HoogvlietError("Onverwachte structuur in de Checkjebon-data")

    labels: list[str] = []
    raw_products: Any = None
    store: dict[str, Any] = {}
    for store in data:
        if not isinstance(store, dict):
            continue
        label = str(_first(store.get("n"), store.get("name"), store.get("c"), store.get("code")) or "")
        labels.append(label)
        if STORE_HOOGVLIET in label.lower():
            raw_products = _first(store.get("d"), store.get("products"), store.get("items"))
            break

    if not isinstance(raw_products, list):
        _LOGGER.warning(
            "Hoogvliet niet gevonden in de Checkjebon-data. Gevonden winkels: %s",
            labels[:25],
        )
        raise HoogvlietError("Hoogvliet staat niet in de Checkjebon-data (structuur veranderd?)")

    # Sommige bronnen zetten het voorvoegsel van de links één keer bij de winkel
    base = _first(store.get("u"), store.get("url"), store.get("base"), store.get("link"), store.get("l"))

    products: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw_products:
        if not isinstance(entry, dict):
            continue
        name = _first(entry.get("n"), entry.get("name"))
        price = _first(entry.get("p"), entry.get("price"))
        if not isinstance(name, str):
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        size = _first(entry.get("s"), entry.get("size"))
        link = _link(
            _first(entry.get("l"), entry.get("link"), entry.get("url"), entry.get("href"), entry.get("u")),
            base,
        )
        name = name.strip()
        # Checkjebon maakt de link zelf van de naam; de echte productlink eindigt op een
        # productnummer. Zonder nummer is de link onbruikbaar (404), dus niet bewaren.
        if link and not product_id(link):
            link = None
        # Altijd naam + hoeveelheid, ook als er een link is: zo blijft de code gelijk
        # tussen versies, en dus ook voor producten die al op de lijst staan.
        code = f"{name}|{size or ''}"
        if code in seen:
            continue
        seen.add(code)
        products.append(
            {
                "code": f"{STORE_HOOGVLIET}:{code}",
                "name": clean_name(name),
                "size": size if isinstance(size, str) and size else None,
                "price": price,
                "url": link,
                "norm": normalize(name),
            }
        )

    if not products:
        _LOGGER.warning(
            "Hoogvliet gevonden, maar zonder bruikbare producten. Voorbeeld: %r",
            raw_products[:2],
        )
        raise HoogvlietError("Geen bruikbare Hoogvliet-producten in de Checkjebon-data")
    if not any(p["url"] for p in products):
        _LOGGER.info(
            "Checkjebon geeft geen productnummers voor Hoogvliet: geen eigen link of foto per product"
        )
    return products


class HoogvlietClient:
    """Lokale Hoogvliet-catalogus, ververst hooguit één keer per dag."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        store: StoreLike | None = None,
        web: Any = None,
    ) -> None:
        self._session = session
        self._web = web  # HoogvlietWebSearch als de gebruiker live zoeken koos
        self._store = store
        self._products: list[dict[str, Any]] = []
        self._fetched_at = 0.0
        self._loaded = False
        self._task: asyncio.Task | None = None
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, listener: Callable[[], None]) -> None:
        """Aanroepen nadat de catalogus is geladen of ververst (ook als verversen mislukte)."""
        self._listeners.append(listener)

    def _notify(self) -> None:
        for listener in self._listeners:
            try:
                listener()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Fout in een Hoogvliet-luisteraar")

    def public_by_codes(self, codes: set[str]) -> dict[str, dict[str, Any]]:
        return {p["code"]: self._public(p) for p in self._products if p["code"] in codes}

    @property
    def ready(self) -> bool:
        return bool(self._products)

    async def _load_from_store(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._store is None:
            return
        data = await self._store.async_load()
        if isinstance(data, dict) and data.get("products"):
            self._products = data["products"]
            # Is de catalogus met een oudere verwerking gemaakt, dan meteen opnieuw ophalen
            fresh = data.get("version") == PARSER_VERSION
            self._fetched_at = float(data.get("fetched_at", 0)) if fresh else 0.0

    async def _download(self) -> bytes:
        try:
            async with self._session.get(
                CHECKJEBON_URL,
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=600),
            ) as resp:
                if resp.status != 200:
                    raise HoogvlietError(f"Checkjebon gaf status {resp.status}")
                length = resp.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > HOOGVLIET_MAX_BYTES:
                    raise HoogvlietError("Checkjebon-bestand is onverwacht groot")
                buffer = bytearray()
                async for chunk in resp.content.iter_chunked(1 << 20):
                    buffer.extend(chunk)
                    if len(buffer) > HOOGVLIET_MAX_BYTES:
                        raise HoogvlietError("Checkjebon-bestand is onverwacht groot")
                return bytes(buffer)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HoogvlietError(f"Kon Checkjebon niet bereiken: {err}") from err

    async def async_refresh(self, force: bool = False) -> None:
        await self._load_from_store()
        if self._products and not force and time.time() - self._fetched_at < HOOGVLIET_MAX_AGE:
            return
        raw = await self._download()
        # Het bestand is groot (alle supermarkten): verwerken buiten de event loop.
        products = await asyncio.get_running_loop().run_in_executor(None, parse_checkjebon, raw)
        del raw
        self._products = products
        self._fetched_at = time.time()
        _LOGGER.debug("Hoogvliet-prijzen ververst: %s producten", len(products))
        if self._store is not None:
            await self._store.async_save(
                {"version": PARSER_VERSION, "fetched_at": self._fetched_at, "products": products}
            )

    async def async_refresh_safe(self) -> None:
        """Verversen voor op de achtergrond: fouten loggen, nooit gooien."""
        try:
            await self.async_refresh()
        except AldiApiError as err:
            _LOGGER.warning("Hoogvliet-prijzen verversen mislukt: %s", err)
        finally:
            self._notify()

    def _kick_refresh(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self.async_refresh_safe())

    async def search(self, query: str, limit: int = SEARCH_LIMIT) -> list[dict[str, Any]]:
        if len(normalize(query)) < 2:
            return []
        if self._web is not None:
            try:
                found = await self._web.search(query)
            except AldiApiError as err:
                # Live zoeken mislukt: de gebruiker heeft er toch wat aan met de prijslijst
                _LOGGER.warning("Zoeken op hoogvliet.nl mislukt (%s); Checkjebon-data gebruikt", err)
            else:
                return [self._public_web(p) for p in found[:limit]]
        return await self.lookup(query, limit)

    async def lookup(self, query: str, limit: int = SEARCH_LIMIT) -> list[dict[str, Any]]:
        """Zoeken in de lokale Checkjebon-catalogus (geen verzoek naar Hoogvliet)."""
        terms = normalize(query).split()
        if not terms or len(normalize(query)) < 2:
            return []

        await self._load_from_store()
        if not self._products:
            self._kick_refresh()
            raise HoogvlietError(
                "De Hoogvliet-prijzen worden voor het eerst opgehaald; probeer het over een minuut opnieuw"
            )
        if time.time() - self._fetched_at >= HOOGVLIET_MAX_AGE:
            self._kick_refresh()  # de oude gegevens blijven bruikbaar

        scored: list[tuple[int, int, str, dict[str, Any]]] = []
        for product in self._products:
            score = _score(product["norm"], terms)
            if score is not None:
                scored.append((score, len(product["name"]), product["name"], product))
        scored.sort(key=lambda t: t[:3])

        return [self._public(p) for *_, p in scored[:limit]]

    @staticmethod
    def _public_web(product: dict[str, Any]) -> dict[str, Any]:
        key = product.get("pid") or f"{product['name']}|{product.get('size') or ''}"
        return {
            "code": f"{STORE_HOOGVLIET}:web:{key}",
            "name": product["name"],
            "brand": product.get("brand"),
            "description": product.get("size"),
            "price": product["price"],
            "price_valid_until": None,
            "old_price": product.get("old_price"),
            "base_price": None,
            "currency": "€",
            "image": product.get("image"),
            "url": product.get("url"),
            "available": product.get("available", True),
            "store": STORE_HOOGVLIET,
            "store_name": STORE_LABELS[STORE_HOOGVLIET],
        }

    @staticmethod
    def _public(product: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": product["code"],
            "name": product["name"],
            "brand": None,
            "description": product.get("size"),
            "price": product["price"],
            "price_valid_until": None,
            "old_price": None,
            "base_price": None,
            "currency": "€",
            "image": image_url(product.get("url")),
            "url": product.get("url"),
            "available": True,
            "store": STORE_HOOGVLIET,
            "store_name": STORE_LABELS[STORE_HOOGVLIET],
        }

    async def enrich(self, product: dict[str, Any]) -> dict[str, Any]:
        """Foto zoeken voor een product waar het adres niet uit de link af te leiden was.

        Eén verzoek naar de productpagina (een pad dat robots.txt toestaat), en alleen
        voor het product dat de gebruiker op de lijst zet. Mislukt het, dan blijft het
        product gewoon bruikbaar, zonder foto.
        """
        url = product.get("url")
        if product.get("image") or not isinstance(url, str):
            return product
        if urlparse(url).hostname not in _HOSTS:
            return product
        image = await self._resolve_image(url)
        return {**product, "image": image} if image else product

    async def _resolve_image(self, url: str) -> str | None:
        try:
            async with self._session.get(
                url,
                allow_redirects=False,
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if 300 <= resp.status < 400:
                    # hoogvliet.com/product/<naam> stuurt door naar .../<naam>-<productnummer>
                    return image_url(resp.headers.get("Location"))
                if resp.status == 200:
                    page = (await resp.content.read(300_000)).decode("utf-8", "ignore")
                    match = _OG_IMAGE_RE.search(page) or _OG_IMAGE_RE_REVERSED.search(page)
                    return match.group(1) if match else None
        except (aiohttp.ClientError, TimeoutError):
            return None
        return None
