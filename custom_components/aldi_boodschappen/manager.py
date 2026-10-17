"""Beheert de boodschappenlijst en slaat die op."""

from __future__ import annotations

import time
import uuid
from datetime import date, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import SIGNAL_UPDATED, STORAGE_KEY, STORAGE_VERSION

PRODUCT_FIELDS = (
    "code",
    "brand",
    "description",
    "price",
    "price_valid_until",
    "old_price",
    "base_price",
    "currency",
    "image",
    "url",
    "store",
    "store_name",
)


MONTHS = ("jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec")


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def current_week_start(today: date | None = None) -> date:
    """Maandag van de week waarvoor je nu boodschappen doet: vanaf vrijdag is dat de volgende week."""
    today = today or date.today()
    monday = monday_of(today)
    return monday + timedelta(days=7) if today.weekday() >= 4 else monday


def week_name(week_start: date) -> str:
    return f"Week {week_start.isocalendar()[1]} ({week_start.day} {MONTHS[week_start.month - 1]})"


class ShoppingManager:
    """Meerdere boodschappenlijsten (één per week). Eén daarvan is de huidige.

    Een lijst is {"id", "name", "week_start", "created", "items"}; een item is een dict.
    Lijsten staan op volgorde van week. De "huidige" lijst is die van de todo-entiteit,
    de services en de automatische nieuwe week; lijsten die erna komen zijn "komende"
    weken waar je alvast aan werkt, lijsten ervoor zijn geschiedenis.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.lists: list[dict[str, Any]] = []
        self.active_id = ""
        self._ensure_one()

    # ---- opslag -------------------------------------------------------- #

    def _new_list(self, name: str | None, week_start: date, items: list | None = None) -> dict[str, Any]:
        return {
            "id": uuid.uuid4().hex,
            "name": (name or "").strip() or week_name(week_start),
            "week_start": week_start.isoformat(),
            "created": time.time(),
            "items": items or [],
        }

    def _ensure_one(self) -> None:
        if not self.lists:
            first = self._new_list(None, current_week_start())
            self.lists = [first]
        if not any(l["id"] == self.active_id for l in self.lists):
            self.active_id = self.lists[0]["id"]

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        if isinstance(data.get("lists"), list) and data["lists"]:
            self.lists = []
            for raw in data["lists"]:
                start = current_week_start()
                try:
                    start = date.fromisoformat(raw.get("week_start", ""))
                except (TypeError, ValueError):
                    pass
                lst = self._new_list(raw.get("name"), start, raw.get("items") or [])
                lst["id"] = raw.get("id") or lst["id"]
                lst["created"] = raw.get("created", lst["created"])
                self.lists.append(lst)
            self.active_id = data.get("active", "")
        elif data.get("items"):
            # Oude opslag (één lijst): wordt de eerste lijst
            self.lists = [self._new_list(None, current_week_start(), data["items"])]
            self.active_id = self.lists[0]["id"]
        self._ensure_one()

    def _payload(self) -> dict[str, Any]:
        return {"version": 2, "active": self.active_id, "lists": self.lists}

    def _changed(self) -> None:
        self._store.async_delay_save(self._payload, 1)
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    # ---- lijsten ------------------------------------------------------- #

    @staticmethod
    def _key(lst: dict[str, Any]) -> tuple[str, float]:
        return (lst["week_start"], lst["created"])

    def sorted_lists(self) -> list[dict[str, Any]]:
        return sorted(self.lists, key=self._key)

    def get_list(self, list_id: str | None) -> dict[str, Any] | None:
        if not list_id:
            return self.active
        return next((l for l in self.lists if l["id"] == list_id), None)

    @property
    def active(self) -> dict[str, Any]:
        return next(l for l in self.lists if l["id"] == self.active_id)

    @property
    def items(self) -> list[dict[str, Any]]:
        """Items van de huidige lijst."""
        return self.active["items"]

    @items.setter
    def items(self, value: list[dict[str, Any]]) -> None:
        self.active["items"] = value

    def upcoming(self) -> list[dict[str, Any]]:
        """Lijsten na de huidige: weken waar je alvast aan werkt."""
        key = self._key(self.active)
        return [l for l in self.sorted_lists() if self._key(l) > key]

    def live_items(self) -> list[dict[str, Any]]:
        """Items waarvan de prijs actueel moet blijven: huidige en komende lijsten, niet de geschiedenis."""
        return [i for l in [self.active, *self.upcoming()] for i in l["items"]]

    def all_items(self) -> list[dict[str, Any]]:
        return [i for l in self.lists for i in l["items"]]

    def summaries(self) -> list[dict[str, Any]]:
        upcoming = {l["id"] for l in self.upcoming()}
        out = []
        for lst in self.sorted_lists():
            out.append(
                {
                    "id": lst["id"],
                    "name": lst["name"],
                    "week_start": lst["week_start"],
                    "active": lst["id"] == self.active_id,
                    "upcoming": lst["id"] in upcoming,
                    "count": len(lst["items"]),
                    "open": sum(1 for i in lst["items"] if not i["checked"]),
                }
            )
        return out

    def _next_week_start(self) -> date:
        latest = date.fromisoformat(self.sorted_lists()[-1]["week_start"])
        return max(latest + timedelta(days=7), current_week_start())

    def create_list(
        self,
        name: str | None = None,
        source_id: str | None = None,
        copy: str = "recurring",
        activate: bool = False,
    ) -> dict[str, Any]:
        """Nieuwe lijst voor de volgende week. `copy`: 'recurring', 'all' of 'none' uit de bronlijst."""
        source = self.get_list(source_id)
        lst = self._new_list(name, self._next_week_start())
        if source and copy in ("recurring", "all"):
            for item in source["items"]:
                if copy == "all" or item.get("recurring"):
                    lst["items"].append({**item, "id": uuid.uuid4().hex, "checked": False})
        self.lists.append(lst)
        if activate:
            self.active_id = lst["id"]
        self._changed()
        return lst

    def rename_list(self, list_id: str, name: str) -> bool:
        lst = self.get_list(list_id)
        name = (name or "").strip()
        if not lst or not name:
            return False
        lst["name"] = name
        self._changed()
        return True

    def delete_list(self, list_id: str) -> bool:
        lst = self.get_list(list_id)
        if not lst or len(self.lists) < 2:
            return False
        ordered = self.sorted_lists()
        idx = ordered.index(lst)
        self.lists = [l for l in self.lists if l["id"] != list_id]
        if self.active_id == list_id:
            neighbour = ordered[idx - 1] if idx > 0 else ordered[idx + 1]
            self.active_id = neighbour["id"]
        self._changed()
        return True

    def set_active(self, list_id: str) -> bool:
        if not self.get_list(list_id):
            return False
        self.active_id = list_id
        self._changed()
        return True

    def new_week(self) -> dict[str, Any]:
        """Naar de volgende week: een alvast klaargezette lijst wordt de huidige, anders komt er een
        nieuwe met de terugkerende producten. De oude lijst blijft bewaard."""
        upcoming = self.upcoming()
        if upcoming:
            self.active_id = upcoming[0]["id"]
            self._changed()
            return upcoming[0]
        return self.create_list(source_id=self.active_id, copy="recurring", activate=True)

    # ---- items --------------------------------------------------------- #

    def _find(self, item_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        for lst in self.lists:
            for item in lst["items"]:
                if item["id"] == item_id:
                    return lst, item
        return None

    def get(self, item_id: str) -> dict[str, Any] | None:
        found = self._find(item_id)
        return found[1] if found else None

    def items_of(self, list_id: str | None) -> list[dict[str, Any]] | None:
        lst = self.get_list(list_id)
        return lst["items"] if lst else None

    def add(
        self,
        name: str,
        product: dict[str, Any] | None = None,
        quantity: int = 1,
        recurring: bool = False,
        list_id: str | None = None,
    ) -> dict[str, Any]:
        """Voeg een item toe. Hetzelfde product (zelfde winkel en code) wordt samengevoegd."""
        target = self.get_list(list_id) or self.active
        items = target["items"]
        code = product.get("code") if product else None
        if code:
            existing = next((i for i in items if i.get("code") == code), None)
            if existing:
                if existing["checked"]:
                    existing["checked"] = False
                    existing["quantity"] = quantity
                else:
                    existing["quantity"] += quantity
                existing["recurring"] = existing["recurring"] or recurring
                self._changed()
                return existing

        item: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "name": name.strip(),
            "quantity": max(1, int(quantity)),
            "checked": False,
            "recurring": bool(recurring),
        }
        if product:
            item["name"] = product.get("name") or item["name"]
            for field in PRODUCT_FIELDS:
                item[field] = product.get(field)
        items.append(item)
        self._changed()
        return item

    def copy_items(self, item_ids: list[str], target_id: str) -> int:
        """Zet kopieën van items (uit welke lijst dan ook) op de doellijst. Geeft het aantal."""
        target = self.get_list(target_id)
        if not target:
            return 0
        count = 0
        for item_id in item_ids:
            found = self._find(item_id)
            if not found:
                continue
            item = found[1]
            product = {f: item.get(f) for f in PRODUCT_FIELDS}
            product["name"] = item["name"]
            if not item.get("code"):
                product = None  # losse tekst heeft geen productgegevens om samen te voegen
            self.add(item["name"], product, item.get("quantity", 1), item.get("recurring", False), target["id"])
            count += 1
        return count

    def update(self, item_id: str, **changes: Any) -> dict[str, Any] | None:
        item = self.get(item_id)
        if not item:
            return None
        for key in ("name", "checked", "recurring"):
            if key in changes and changes[key] is not None:
                item[key] = changes[key]
        if changes.get("quantity") is not None:
            item["quantity"] = max(1, int(changes["quantity"]))
        self._changed()
        return item

    def remove(self, item_id: str) -> bool:
        found = self._find(item_id)
        if not found:
            return False
        found[0]["items"] = [i for i in found[0]["items"] if i["id"] != item_id]
        self._changed()
        return True

    def update_prices(self, updates: dict[str, dict[str, Any]]) -> int:
        """Werk prijzen bij op basis van productcode, voor huidige en komende lijsten."""
        changed = 0
        for item in self.live_items():
            new = updates.get(item.get("code") or "")
            if not new:
                continue
            for field in ("price", "price_valid_until"):
                if field in new and item.get(field) != new[field]:
                    item[field] = new[field]
                    changed += 1
        if changed:
            self._changed()
        return changed

    def backfill_images(self, image_for) -> int:
        """Geef bestaande items zonder foto er een, als `image_for(item)` die kan bepalen."""
        changed = 0
        for item in self.all_items():
            if item.get("image") or not item.get("url"):
                continue
            image = image_for(item)
            if image:
                item["image"] = image
                changed += 1
        if changed:
            self._changed()
        return changed

    def backfill_missing(self, products_by_code: dict[str, dict[str, Any]]) -> int:
        """Vul ontbrekende link en foto aan van items die al op een lijst stonden."""
        changed = 0
        for item in self.all_items():
            product = products_by_code.get(item.get("code") or "")
            if not product:
                continue
            for field in ("url", "image"):
                if not item.get(field) and product.get(field):
                    item[field] = product[field]
                    changed += 1
        if changed:
            self._changed()
        return changed

    def repair_items(self, fix) -> int:
        """Pas `fix(item) -> bool` toe op alle items (bv. foute link of naam herstellen)."""
        changed = sum(1 for item in self.all_items() if fix(item))
        if changed:
            self._changed()
        return changed
