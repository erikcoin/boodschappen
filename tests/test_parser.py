"""Test van het verwerken van het Aldi-zoekantwoord (python tests/test_parser.py)."""

import importlib.util
import sys
import types
from pathlib import Path

root = Path(__file__).parent.parent / "custom_components" / "aldi_boodschappen"

pkg = types.ModuleType("ab")
pkg.__path__ = [str(root)]
sys.modules["ab"] = pkg
try:
    import aiohttp  # noqa: F401
except ImportError:
    sys.modules["aiohttp"] = types.SimpleNamespace(
        ClientSession=object, ClientError=Exception, ClientTimeout=lambda **k: None
    )

for name in ("const", "api"):
    spec = importlib.util.spec_from_file_location(f"ab.{name}", root / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"ab.{name}"] = mod
    spec.loader.exec_module(mod)

api = sys.modules["ab.api"]

# Hit uit het echte antwoord van aldi.nl (zoeken op "Pink lady"), plus twee varianten
pink_lady = {
    "objectID": "1204033",
    "isAvailable": True,
    "brandName": "PINK LADY",
    "categoryIDs": ["fruit"],
    "assets": [
        {"type": "gallery", "url": "https://s7g10.scene7.com/is/image/aldinord/variant_1204033_1"},
        {"type": "primary", "url": "https://s7g10.scene7.com/is/image/aldinord/product_1204033_main_cms_20_nl_nl_appels_cw01_2024"},
    ],
    "salesUnit": "6 stuks",
    "productReferences": [{"type": "KVArticleNumber", "value": "0004555"}],
    "name": "Appels",
    "currentPrice": {"priceValue": 2.99, "validFrom": 1791151200, "validUntil": 1792706399},
    "shortDescription": "Pink Lady appels bij ALDI: knapperige en zoete smaak.",
    "productSlug": "appels-1204033",
    "mainCategoryID": "fruit",
}
unavailable = {"name": "Elstar appels", "isAvailable": False, "productSlug": "elstar-1", "objectID": "1"}
no_brand_no_price = {"name": "Volle kwark", "productSlug": "volle-kwark-91244809", "brandName": "Almhof"}

payload = {"results": [{"hits": [unavailable, pink_lady, no_brand_no_price, pink_lady, {"x": 1}, "rommel"]}]}
products = api.parse_response(payload)

# dubbele hit en kapotte hits verdwijnen; beschikbare producten staan voorop
assert [p["code"] for p in products] == ["1204033", "volle-kwark-91244809", "1"], products

p = products[0]
assert p["name"] == "Appels Pink Lady", p["name"]
assert p["description"] == "6 stuks"
assert p["price"] == 2.99
assert p["price_valid_until"] == 1792706399
assert p["image"].endswith("appels_cw01_2024"), "primaire foto gaat voor galerij"
assert p["url"] == "https://www.aldi.nl/product/appels-1204033.html"

assert products[1]["name"] == "Volle kwark Almhof"  # merk met kleine letters blijft zoals het is
assert products[1]["price"] is None and products[1]["image"] is None
assert products[2]["available"] is False

# Onverwachte antwoorden geven een nette fout in plaats van een crash
for bad in ({}, {"results": []}, {"results": [{}]}, [], None):
    try:
        api.parse_response(bad)
    except api.AldiApiError:
        pass
    else:
        raise AssertionError(f"verwachtte AldiApiError voor {bad!r}")


# ---- manager: prijzen bijwerken (HA-modules worden hier nagebootst) ----
for mod in ("homeassistant", "homeassistant.core", "homeassistant.helpers",
            "homeassistant.helpers.dispatcher", "homeassistant.helpers.storage"):
    sys.modules[mod] = types.ModuleType(mod)
sys.modules["homeassistant.core"].HomeAssistant = object
sys.modules["homeassistant.helpers.dispatcher"].async_dispatcher_send = lambda *a, **k: None


class FakeStore:
    def __init__(self, *a, **k): pass
    def async_delay_save(self, *a, **k): pass


sys.modules["homeassistant.helpers.storage"].Store = FakeStore
spec = importlib.util.spec_from_file_location("ab.manager", root / "manager.py")
manager_mod = importlib.util.module_from_spec(spec)
sys.modules["ab.manager"] = manager_mod
spec.loader.exec_module(manager_mod)

m = manager_mod.ShoppingManager(hass=None)
a = m.add("Appels Pink Lady", products[0], quantity=2, recurring=True)
b = m.add("Melk")  # losse tekst, geen code
assert a["price"] == 2.99 and a["price_valid_until"] == 1792706399 and b.get("price") is None

n = m.update_prices({"1204033": {"price": 3.49, "price_valid_until": 1793000000}, "onbekend": {"price": 9.99}})
assert n == 2, n
assert m.get(a["id"])["price"] == 3.49 and m.get(a["id"])["price_valid_until"] == 1793000000
assert m.get(b["id"]).get("price") is None  # losse tekst blijft ongemoeid
assert m.update_prices({"1204033": {"price": 3.49, "price_valid_until": 1793000000}}) == 0  # niets nieuws

# totaal zoals het paneel dat rekent: prijs x aantal, alleen producten die nog gehaald moeten worden
m.update(a["id"], quantity=3)
total = sum(i["price"] * i["quantity"] for i in m.items if not i["checked"] and i.get("price") is not None)
assert round(total, 2) == 10.47, total

# bestaande items zonder foto krijgen er een als die afleidbaar is; bestaande foto's blijven
m2 = manager_mod.ShoppingManager(hass=None)
m2.items = [
    {"id": "1", "name": "a", "url": "https://hoogvliet.nl/product/x-726992000", "image": None, "store": "hoogvliet"},
    {"id": "2", "name": "b", "url": "https://hoogvliet.nl/product/y-111111111", "image": "https://al.eens/foto.jpg", "store": "hoogvliet"},
    {"id": "3", "name": "c", "url": "https://www.aldi.nl/product/z-1.html", "image": None, "store": "aldi"},
    {"id": "4", "name": "d", "image": None},
]
n = m2.backfill_images(lambda i: "IMG:" + i["id"] if i.get("store") == "hoogvliet" else None)
assert n == 1 and m2.items[0]["image"] == "IMG:1" and m2.items[1]["image"] == "https://al.eens/foto.jpg"
assert m2.items[2]["image"] is None and m2.items[3]["image"] is None
assert m2.backfill_images(lambda i: None) == 0

# ontbrekende link en foto aanvullen via productcode; bestaande waarden blijven
m3 = manager_mod.ShoppingManager(hass=None)
m3.items = [
    {"id": "1", "code": "a|1", "url": None, "image": None},
    {"id": "2", "code": "b|1", "url": "oud", "image": None},
    {"id": "3", "code": "zzz", "url": None, "image": None},
]
n = m3.backfill_missing({"a|1": {"url": "U", "image": "I"}, "b|1": {"url": "nieuw", "image": "J"}})
assert n == 3 and m3.items[0]["url"] == "U" and m3.items[1]["url"] == "oud" and m3.items[1]["image"] == "J"
assert m3.items[2]["url"] is None

# ---- meerdere lijsten ----
import asyncio
from datetime import date

mm = manager_mod
assert mm.week_name(date(2026, 10, 12)) == "Week 42 (12 okt)"
assert mm.current_week_start(date(2026, 10, 7)) == date(2026, 10, 5), "woensdag: deze week"
assert mm.current_week_start(date(2026, 10, 9)) == date(2026, 10, 12), "vanaf vrijdag: volgende week"

L = mm.ShoppingManager(hass=None)
assert len(L.lists) == 1 and L.items is L.active["items"]
first = L.active_id
milk = L.add("Melk", {"code": "a:1", "name": "Melk", "price": 1.0}, 2, recurring=True)
L.add("Kaas", {"code": "a:2", "name": "Kaas", "price": 3.0}, 1, recurring=False)
L.add("Brood")  # losse tekst
L.update(milk["id"], checked=True)

# alvast een volgende week beginnen: kopieert alleen terugkerende producten, niet afgevinkt
nxt = L.create_list(source_id=first, copy="recurring")
assert L.active_id == first, "de huidige lijst blijft de huidige"
assert [i["name"] for i in nxt["items"]] == ["Melk"] and nxt["items"][0]["checked"] is False
assert nxt["items"][0]["id"] != milk["id"] and nxt["items"][0]["quantity"] == 2
assert date.fromisoformat(nxt["week_start"]) == date.fromisoformat(L.get_list(first)["week_start"]) + __import__("datetime").timedelta(days=7)
assert [s["upcoming"] for s in L.summaries()] == [False, True]
third = L.create_list("Feestweek", source_id=first, copy="none")
assert third["name"] == "Feestweek" and third["items"] == []
assert date.fromisoformat(third["week_start"]) == date.fromisoformat(nxt["week_start"]) + __import__("datetime").timedelta(days=7)

# toevoegen aan een andere lijst dan de huidige
L.add("Eieren", {"code": "a:3", "name": "Eieren", "price": 2.0}, 1, False, nxt["id"])
assert [i["name"] for i in nxt["items"]] == ["Melk", "Eieren"] and "Eieren" not in [i["name"] for i in L.items]
assert L.add("Eieren", {"code": "a:3", "name": "Eieren"}, 1, False, nxt["id"])["quantity"] == 2, "zelfde product samengevoegd in die lijst"

# producten van een andere lijst overnemen
kaas = next(i for i in L.items if i["name"] == "Kaas")
brood = next(i for i in L.items if i["name"] == "Brood")
assert L.copy_items([kaas["id"], brood["id"], "bestaat-niet"], third["id"]) == 2
assert [(i["name"], i["checked"]) for i in third["items"]] == [("Kaas", False), ("Brood", False)]
assert kaas in L.items and kaas["id"] not in [i["id"] for i in third["items"]], "kopie, geen verplaatsing"

# prijzen: huidige en komende lijsten wel, geschiedenis niet
L.set_active(nxt["id"])
assert L.update_prices({"a:1": {"price": 1.5}}) == 1, "Melk staat in de geschiedenis (vorige week) en in nxt: alleen nxt telt"
assert milk["price"] == 1.0 and nxt["items"][0]["price"] == 1.5
L.set_active(first)
assert L.update_prices({"a:3": {"price": 2.5}}) == 1 and nxt["items"][1]["price"] == 2.5, "komende lijst wordt bijgewerkt"

# nieuwe week: een klaargezette lijst wordt de huidige; anders ontstaat er een nieuwe
assert L.new_week()["id"] == nxt["id"] and L.active_id == nxt["id"]
assert L.new_week()["id"] == third["id"]
fresh = L.new_week()
assert L.active_id == fresh["id"] and len(L.lists) == 4 and fresh["items"] == [], "Feestweek had geen terugkerende producten"
assert [l["id"] for l in L.sorted_lists()] == [first, nxt["id"], third["id"], fresh["id"]]

# hernoemen en verwijderen
assert L.rename_list(first, "  Week 41  ") and L.get_list(first)["name"] == "Week 41"
assert not L.rename_list(first, "  ") and not L.rename_list("x", "a")
assert L.delete_list(fresh["id"]) and L.active_id == third["id"], "huidige verwijderd: buurlijst wordt huidige"
assert not L.delete_list("x")
for lid in [first, nxt["id"]]:
    L.delete_list(lid)
assert len(L.lists) == 1 and not L.delete_list(L.active_id), "de laatste lijst blijft"

# laden: oude opslag (één lijst) en nieuwe opslag
class LoadStore:
    def __init__(self, data): self.data = data
    async def async_load(self): return self.data
    def async_delay_save(self, *a, **k): pass


old = mm.ShoppingManager(hass=None)
old._store = LoadStore({"items": [{"id": "1", "name": "Melk", "quantity": 1, "checked": False, "recurring": False}]})
asyncio.run(old.async_load())
assert len(old.lists) == 1 and old.items[0]["name"] == "Melk" and old.active_id == old.lists[0]["id"]
saved = old._payload()
assert saved["version"] == 2 and saved["lists"][0]["items"][0]["name"] == "Melk"
again = mm.ShoppingManager(hass=None)
again._store = LoadStore(saved)
asyncio.run(again.async_load())
assert again.active_id == old.active_id and again.items == old.items
empty = mm.ShoppingManager(hass=None)
empty._store = LoadStore(None)
asyncio.run(empty.async_load())
assert len(empty.lists) == 1 and empty.items == []

print("ok:", len(products), "producten uit antwoord verwerkt, prijzen en totaal kloppen")
