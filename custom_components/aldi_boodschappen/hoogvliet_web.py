"""Live zoeken in de webshop van hoogvliet.nl (optioneel, staat standaard uit).

De webshop haalt zijn producten zelf op via een GraphQL-API (`/api/graphql`, zonder
sleutel). Deze module doet precies zo'n verzoek, alleen als de gebruiker die bron kiest:
één verzoek per zoekopdracht, nooit sneller dan één per paar seconden, resultaten tien
minuten onthouden, niets op de achtergrond en een eerlijke User-Agent. Zie de
opmerking in de README: dit is een niet-gedocumenteerde API zonder toestemming.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import aiohttp

from .const import HOOGVLIET_API_URL, SEARCH_CACHE_SECONDS, USER_AGENT
from .hoogvliet import IMAGE_URL, HoogvlietError, product_id

_LOGGER = logging.getLogger(__name__)

MIN_INTERVAL = 3.0  # seconden tussen twee verzoeken
MAX_BYTES = 3_000_000
PAGE_SIZE = 24
PRODUCT_URL = "https://hoogvliet.nl/product/{key}"

# Alleen de velden die we nodig hebben (de webshop vraagt er veel meer op)
QUERY = (
    "query GetProductsSearch($pageSize: Int!, $currentPage: Int!, $inputText: String!, "
    "$filters: ProductAttributeFilterInput!, $sort: ProductAttributeSortInput) { "
    "products(pageSize: $pageSize, currentPage: $currentPage, search: $inputText, "
    "filter: $filters, sort: $sort) { items { ... on ProductInterface { "
    "id stock_status name url_key metadata "
    "price_range { minimum_price { regular_price { value } final_price { value } } } "
    "} } } }"
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def _metadata(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("metadata")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _size(meta: dict[str, Any]) -> str | None:
    """'4' + 'Stuks' -> '4 stuks'; '500' + 'Gram' -> '500 gram'."""
    amount, unit = meta.get("inhoud_ce"), meta.get("eenheid")
    if isinstance(amount, str):
        try:
            amount = float(amount.replace(",", "."))
        except ValueError:
            return None
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
        return None
    if not isinstance(unit, str) or not unit.strip():
        return None
    number = int(amount) if float(amount).is_integer() else amount
    return f"{number} {unit.strip().lower()}"


def parse_response(data: Any) -> list[dict[str, Any]]:
    """Producten uit het GraphQL-antwoord. Geeft een fout bij een onverwachte vorm."""
    if not isinstance(data, dict):
        raise HoogvlietError("Onverwacht antwoord van Hoogvliet")
    items = ((data.get("data") or {}).get("products") or {}).get("items")
    if not isinstance(items, list):
        errors = data.get("errors")
        message = errors[0].get("message") if isinstance(errors, list) and errors and isinstance(errors[0], dict) else None
        _LOGGER.warning("Onverwachte opbouw van het Hoogvliet-antwoord: %.300r", data)
        raise HoogvlietError(f"Hoogvliet gaf een fout: {message}" if message else "Onverwachte opbouw van het Hoogvliet-antwoord")

    found: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key, name = item.get("url_key"), item.get("name")
        if not isinstance(key, str) or not isinstance(name, str) or not name.strip():
            continue
        minimum = ((item.get("price_range") or {}).get("minimum_price")) or {}
        price = _number(((minimum.get("final_price")) or {}).get("value"))
        if price is None:
            continue
        regular = _number(((minimum.get("regular_price")) or {}).get("value"))
        meta = _metadata(item)
        brand = meta.get("merk") if isinstance(meta.get("merk"), str) and meta["merk"].strip() else None
        name = " ".join(name.split())
        # De webshop toont merk + naam ("Pink lady" + "Appels op schaal")
        full = f"{brand.strip()} {name}" if brand and not name.lower().startswith(brand.strip().lower()) else name
        pid = product_id(f"/{key}")
        found.append(
            {
                "name": full,
                "brand": brand.strip() if brand else None,
                "price": price,
                "old_price": regular if regular and regular > price else None,
                "size": _size(meta),
                "url": PRODUCT_URL.format(key=key),
                "pid": pid or str(item.get("id") or key),
                "image": IMAGE_URL.format(id=pid) if pid else None,
                "available": item.get("stock_status") in (None, "IN_STOCK"),
            }
        )
    return found


class HoogvlietWebSearch:
    """Eén verzoek per zoekopdracht naar de Hoogvliet-API, vertraagd en onthouden."""

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
            data = await self._fetch(key)

        products = parse_response(data)
        self._cache[key] = (time.monotonic(), products)
        return products

    async def _fetch(self, query: str) -> Any:
        params = {
            "operationName": "GetProductsSearch",
            "query": QUERY,
            "variables": json.dumps(
                {
                    "pageSize": PAGE_SIZE,
                    "currentPage": 1,
                    "inputText": query,
                    "filters": {"facets": []},
                    "sort": {},
                },
                separators=(",", ":"),
            ),
        }
        try:
            async with self._session.get(
                HOOGVLIET_API_URL,
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    raise HoogvlietError(f"Hoogvliet gaf status {resp.status}")
                body = await resp.content.read(MAX_BYTES)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HoogvlietError(f"Hoogvliet niet bereikbaar: {err}") from err
        try:
            return json.loads(body)
        except ValueError as err:
            _LOGGER.warning("Hoogvliet gaf geen JSON terug. Begin: %.300r", body[:300])
            raise HoogvlietError("Hoogvliet gaf geen geldig antwoord") from err
