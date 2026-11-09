"""Live zoeken in de webshop van Lidl (optioneel, staat standaard uit).

LET OP, lees dit voor je het aanzet:
- Lidl verbiedt in robots.txt automatisch ophalen, en zet zijn webshop achter een beveiliging
  (Myra) die geautomatiseerde verzoeken kan blokkeren (status 401/403/503 of een 'Security
  Check'-pagina). Eerdere versies liepen daar op vast.
- Deze module doet daarom precies één gewoon verzoek per zoekopdracht, met een eerlijke
  User-Agent. Wordt dat geweigerd, dan stopt het: we doen ons niet voor als browser, sturen
  geen cookies mee en lossen geen 'checks' op. De integratie valt dan terug op de
  Checkjebon-prijslijst en zet de reden in het log.
- Niet getest tegen het echte antwoord van Lidl: de verwerking is bewust ruim. Lukt het niet,
  dan staat het begin van het antwoord in het log.

Zuinig: nooit sneller dan één verzoek per paar seconden, resultaten tien minuten onthouden.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import aiohttp

from .const import LIDL_BASE_URL, LIDL_SEARCH_URL, SEARCH_CACHE_SECONDS, USER_AGENT
from .hoogvliet import HoogvlietError, _first, read_limited

_LOGGER = logging.getLogger(__name__)

MIN_INTERVAL = 3.0
MAX_BYTES = 3_000_000
PAGE_SIZE = 48


class LidlBlockedError(HoogvlietError):
    """Lidl weigert het verzoek (beveiliging)."""


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_item(raw: Any) -> dict[str, Any] | None:
    """Eén item uit het Lidl-antwoord (ruim opgezet: de velden staan op meerdere plekken)."""
    if not isinstance(raw, dict):
        return None
    gridbox = raw.get("gridbox") if isinstance(raw.get("gridbox"), dict) else {}
    data = _first(gridbox.get("data"), raw.get("data"), raw)
    if not isinstance(data, dict):
        return None

    title = _first(data.get("fullTitle"), data.get("title"), raw.get("title"))
    if not isinstance(title, str) or not title.strip():
        return None

    price_obj = data.get("price") if isinstance(data.get("price"), dict) else {}
    price = _as_float(_first(price_obj.get("price"), data.get("price")))
    if price is None:
        return None
    old_price = _as_float(price_obj.get("oldPrice"))
    base_price = price_obj.get("basePrice")
    base_text = base_price.get("text") if isinstance(base_price, dict) else None

    image = _first(data.get("image"), data.get("imageUrl"))
    if not image:
        images = data.get("imageList") or []
        if images and isinstance(images[0], dict):
            image = images[0].get("image")
        elif images and isinstance(images[0], str):
            image = images[0]
    if not (isinstance(image, str) and image.startswith(("http://", "https://"))):
        image = None

    url = _first(data.get("canonicalUrl"), data.get("canonicalPath"), data.get("url"))
    if isinstance(url, str):
        url = f"{LIDL_BASE_URL}{url}" if url.startswith("/") else url
        if not url.startswith("http"):
            url = None
    else:
        url = None

    pid = _first(data.get("productId"), data.get("erpNumber"), raw.get("code"), data.get("itemId"), url, title)
    brand = data.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    keyfacts = data.get("keyfacts") if isinstance(data.get("keyfacts"), dict) else {}
    size = _first(keyfacts.get("supplementalDescription"), keyfacts.get("description"), data.get("subtitle"))
    return {
        "name": " ".join(title.split()),
        "brand": brand if isinstance(brand, str) else None,
        "price": price,
        "old_price": old_price if old_price and old_price > price else None,
        "base_price": base_text if isinstance(base_text, str) else None,
        "size": size.strip() if isinstance(size, str) and size.strip() else None,
        "url": url,
        "pid": str(pid),
        "image": image,
        "available": True,
    }


def parse_response(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise HoogvlietError("Onverwacht antwoord van Lidl")
    items = payload.get("items")
    if not isinstance(items, list):
        _LOGGER.warning("Onverwachte opbouw van het Lidl-antwoord. Velden: %s", sorted(payload)[:20])
        raise HoogvlietError("Onverwachte opbouw van het Lidl-antwoord")
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in items:
        product = parse_item(raw)
        if product and product["pid"] not in seen:
            seen.add(product["pid"])
            found.append(product)
    if items and not found:
        sample = json.dumps(items[0], ensure_ascii=False, default=str)[:400]
        _LOGGER.warning("Geen producten herkend in het Lidl-antwoord. Eerste item: %s", sample)
        raise HoogvlietError("Het antwoord van Lidl had een onbekende opbouw")
    return found


class LidlWebSearch:
    """Eén verzoek per zoekopdracht naar de Lidl-webshop, vertraagd en onthouden."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    async def search(self, query: str) -> list[dict[str, Any]]:
        key = " ".join(query.lower().split())
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < SEARCH_CACHE_SECONDS:
            return cached[1]
        async with self._lock:
            wait = MIN_INTERVAL - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()
            payload = await self._fetch(key)
        products = parse_response(payload)
        self._cache[key] = (time.monotonic(), products)
        return products

    async def _fetch(self, query: str) -> Any:
        params = {
            "q": query,
            "offset": 0,
            "fetchsize": PAGE_SIZE,
            "locale": "nl_NL",
            "assortment": "NL",
            "version": "2.1.0",
        }
        try:
            async with self._session.get(
                LIDL_SEARCH_URL,
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                status = resp.status
                body = await read_limited(resp.content, MAX_BYTES)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HoogvlietError(f"Lidl niet bereikbaar: {err}") from err
        if status in (401, 403, 429, 503):
            _LOGGER.warning("Lidl weigerde het verzoek (status %s). Begin van het antwoord: %.200r", status, body[:200])
            raise LidlBlockedError(
                f"Lidl blokkeert automatische verzoeken (status {status}); we proberen het niet te omzeilen"
            )
        if status != 200:
            raise HoogvlietError(f"Lidl gaf status {status}")
        try:
            return json.loads(body)
        except ValueError as err:
            # Meestal een 'Security Check'-pagina in plaats van JSON
            _LOGGER.warning("Lidl gaf geen JSON terug. Begin van het antwoord: %.200r", body[:200])
            raise LidlBlockedError("Lidl gaf een beveiligingspagina in plaats van producten") from err
