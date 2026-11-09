"""Lidl-producten en -prijzen via de open data van Checkjebon (die ze van boodschaapje.nl haalt).

Lidl zet zijn webshop achter een beveiliging die geautomatiseerde verzoeken blokkeert; die
omzeilen we niet. Checkjebon (MIT-licentie) verzamelt de Lidl-prijzen via boodschaapje.nl en
stelt ze beschikbaar voor hergebruik. We gebruiken dat bestand op dezelfde manier als voor
Hoogvliet: hooguit één keer per dag ophalen, alleen de Lidl-producten lokaal bewaren, en daar
zelf in zoeken.

LET OP: dit zijn de prijzen zoals boodschaapje.nl/Checkjebon ze het laatst hebben vastgelegd,
geen live prijzen van Lidl. Er zijn geen foto's en vaak geen hoeveelheid.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .const import STORE_LABELS, STORE_LIDL
from .hoogvliet import HoogvlietClient, HoogvlietError, _first, normalize

_LOGGER = logging.getLogger(__name__)


def parse_lidl(raw: bytes | str) -> list[dict[str, Any]]:
    """Haal de Lidl-producten uit het Checkjebon-bestand."""
    try:
        data = json.loads(raw)
    except ValueError as err:
        raise HoogvlietError(f"Checkjebon-data is geen geldige JSON: {err}") from err
    if isinstance(data, dict):
        data = _first(data.get("supermarkets"), data.get("data"), data.get("stores")) or []
    if not isinstance(data, list):
        raise HoogvlietError("Onverwachte structuur in de Checkjebon-data")

    store = next(
        (s for s in data if isinstance(s, dict) and STORE_LIDL in str(_first(s.get("n"), s.get("name")) or "").lower()),
        None,
    )
    raw_products = _first(store.get("d"), store.get("products")) if store else None
    if not isinstance(raw_products, list):
        _LOGGER.warning("Lidl niet gevonden in de Checkjebon-data")
        raise HoogvlietError("Lidl staat niet in de Checkjebon-data (structuur veranderd?)")

    base = store.get("u") if isinstance(store.get("u"), str) and store["u"].startswith("http") else None
    products: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw_products:
        if not isinstance(entry, dict):
            continue
        name = _first(entry.get("n"), entry.get("name"))
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            price = float(_first(entry.get("p"), entry.get("price")))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        size = _first(entry.get("s"), entry.get("size"))
        link = _first(entry.get("l"), entry.get("link"))
        link = str(link).strip() if link not in (None, "") else None
        name = " ".join(name.split())
        # Het nummer van boodschaapje.nl is stabiel; zonder nummer val terug op naam + hoeveelheid
        key = link or f"{name}|{size or ''}"
        if key in seen:
            continue
        seen.add(key)
        url = None
        if link:
            url = link if link.startswith("http") else (f"{base}{link.lstrip('/')}" if base else None)
        products.append(
            {
                "code": f"{STORE_LIDL}:{key}",
                "name": name,
                "size": size if isinstance(size, str) and size.strip() else None,
                "price": price,
                "url": url,
                "norm": normalize(name),
            }
        )
    if not products:
        raise HoogvlietError("Geen bruikbare Lidl-producten in de Checkjebon-data")
    return products


class LidlClient(HoogvlietClient):
    """Lokale Lidl-catalogus (zelfde werkwijze als Hoogvliet, zonder webshop-zoekfunctie)."""

    _label = "Lidl"
    _parser = staticmethod(parse_lidl)

    @staticmethod
    def _public_web(product: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": f"{STORE_LIDL}:web:{product['pid']}",
            "name": product["name"],
            "brand": product.get("brand"),
            "description": product.get("size"),
            "price": product["price"],
            "price_valid_until": None,
            "old_price": product.get("old_price"),
            "base_price": product.get("base_price"),
            "currency": "€",
            "image": product.get("image"),
            "url": product.get("url"),
            "available": product.get("available", True),
            "store": STORE_LIDL,
            "store_name": STORE_LABELS[STORE_LIDL],
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
            "image": None,
            "url": product.get("url"),
            "available": True,
            "store": STORE_LIDL,
            "store_name": STORE_LABELS[STORE_LIDL],
        }
