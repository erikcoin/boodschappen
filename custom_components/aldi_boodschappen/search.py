"""Zoekt in alle ingestelde winkels tegelijk en voegt de resultaten samen."""

from __future__ import annotations

import asyncio
import logging
from itertools import zip_longest
from typing import Any, Protocol

from .api import AldiApiError
from .const import SEARCH_LIMIT, STORE_LABELS
from .pricing import annotate_unit_prices, mark_cheapest

_LOGGER = logging.getLogger(__name__)


class StoreClient(Protocol):
    async def search(self, query: str, limit: int = ...) -> list[dict[str, Any]]: ...


class SearchService:
    """Eén zoekopdracht naar elke ingestelde winkel; één mislukte winkel is geen ramp."""

    def __init__(self, clients: dict[str, StoreClient]) -> None:
        self._clients = clients

    @property
    def stores(self) -> list[str]:
        return list(self._clients)

    async def _search_each(
        self,
        query: str,
        limit: int,
        background: bool = False,
        only: set[str] | None = None,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
        names = [n for n in self._clients if only is None or n in only]
        if not names:
            return {}, []
        outcomes = await asyncio.gather(
            *(self._search_one(n, query, limit, background) for n in names),
            return_exceptions=True,
        )
        found: dict[str, list[dict[str, Any]]] = {}
        warnings: list[str] = []
        failures = 0
        for name, outcome in zip(names, outcomes):
            if isinstance(outcome, AldiApiError):
                failures += 1
                warnings.append(f"{STORE_LABELS.get(name, name)}: {outcome}")
            elif isinstance(outcome, BaseException):
                failures += 1
                _LOGGER.exception("Onverwachte fout bij zoeken in %s", name, exc_info=outcome)
                warnings.append(f"{STORE_LABELS.get(name, name)}: onverwachte fout")
            else:
                found[name] = outcome
        if failures == len(names):
            # Niets gelukt: laat de gebruiker de oorzaak zien in plaats van "niets gevonden"
            raise AldiApiError("; ".join(warnings))
        return found, warnings

    async def _search_one(self, name: str, query: str, limit: int, background: bool):
        client = self._clients[name]
        # Bij het verversen van prijzen: de winkel kan daar een eigen werkwijze voor hebben
        # (Hoogvliet: live als gekozen, anders de lokale prijslijst). Anders gewoon zoeken.
        refresh = None
        if background:
            refresh = getattr(client, "refresh_search", None) or getattr(client, "lookup", None)
        return await (refresh or client.search)(query, limit)

    async def search(
        self, query: str, limit: int = SEARCH_LIMIT
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Resultaten van alle winkels, om en om (op relevantie) en met eenheidsprijzen."""
        found, warnings = await self._search_each(query, limit)
        lists = [found[n] for n in self._clients if n in found]
        # Kopieën: de clients bewaren hun resultaten in een cache, en de eenheidsprijzen
        # en het "goedkoopst"-label horen bij deze ene zoekopdracht.
        merged = [
            dict(product)
            for group in zip_longest(*lists)
            for product in group
            if product is not None
        ][:limit]
        annotate_unit_prices(merged)
        mark_cheapest(merged)
        return merged, warnings

    async def lookup(self, query: str, stores: set[str] | None = None) -> list[dict[str, Any]]:
        """Alle resultaten (van de genoemde winkels), zonder afkappen, om prijzen bij te werken."""
        found, _ = await self._search_each(query, SEARCH_LIMIT, background=True, only=stores)
        return [product for products in found.values() for product in products]

    async def enrich(self, product: dict[str, Any]) -> dict[str, Any]:
        """Vul een gekozen product aan (bijv. met een foto) als de winkel dat kan.

        Best effort: een fout hier mag het toevoegen aan de lijst nooit tegenhouden.
        """
        client = self._clients.get(product.get("store"))
        enrich = getattr(client, "enrich", None)
        if enrich is None:
            return product
        try:
            return await enrich(product)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Product aanvullen mislukt", exc_info=True)
            return product
