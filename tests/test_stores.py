"""Tests voor eenheidsprijzen, Hoogvliet (Checkjebon) en het samenvoegen van winkels.

Draaien met: python tests/test_stores.py

LET OP: het Checkjebon-voorbeeld hieronder is NIET echt. De vorm (lijst winkels met
n/d en producten met n/p/s/l) is een aanname op basis van de projectbeschrijving.
De test bewijst dat de verwerking klopt voor die vorm, niet dat het echte bestand
zo is opgebouwd.
"""

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

root = Path(__file__).parent.parent / "custom_components" / "aldi_boodschappen"

pkg = types.ModuleType("ab2")
pkg.__path__ = [str(root)]
sys.modules["ab2"] = pkg
try:
    import aiohttp  # noqa: F401
except ImportError:
    sys.modules["aiohttp"] = types.SimpleNamespace(
        ClientSession=object, ClientError=Exception, ClientTimeout=lambda **k: None
    )

for name in ("const", "api", "pricing", "hoogvliet", "hoogvliet_web", "picnic", "search"):
    spec = importlib.util.spec_from_file_location(f"ab2.{name}", root / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"ab2.{name}"] = mod
    spec.loader.exec_module(mod)

api = sys.modules["ab2.api"]
pricing = sys.modules["ab2.pricing"]
hoogvliet = sys.modules["ab2.hoogvliet"]
search = sys.modules["ab2.search"]
web = sys.modules["ab2.hoogvliet_web"]
picnic = sys.modules["ab2.picnic"]

# ---------------------------------------------------------------- eenheden
ps = pricing.parse_size
assert ps("6 stuks") == (6.0, "stuk")
assert ps("500 g") == (0.5, "kg")
assert ps("1,5 l") == (1.5, "l")
assert ps("75 cl") == (0.75, "l")
assert ps("330 ml") == (0.33, "l")
assert ps("2 x 250 g") == (0.5, "kg")
assert ps("6 × 330 ml") == (1.98, "l") or abs(ps("6 × 330 ml")[0] - 1.98) < 1e-9
assert ps("ca. 1 kg") == (1.0, "kg")
assert ps("per stuk") is None and ps("4 pack") is None and ps(None) is None and ps("") is None
assert ps("0 g") is None

# ---------------------------------------------------------------- eenheidsprijs + goedkoopst
def p(code, price, size, store="aldi", available=True):
    return {"code": code, "price": price, "description": size, "available": available, "store": store}

products = [
    p("a1", 2.99, "6 stuks", "aldi"),        # 0,4983 per stuk
    p("h1", 2.49, "4 stuks", "hoogvliet"),   # 0,6225 per stuk
    p("a2", 1.49, "500 g", "aldi"),          # 2,98 per kg
    p("h2", 2.99, "1 kg", "hoogvliet"),      # 2,99 per kg  -> iets duurder dan a2
    p("a3", 3.00, "1 l", "aldi"),            # enige per liter: geen vergelijking mogelijk
    p("h3", 1.00, "per stuk", "hoogvliet"),  # geen te lezen hoeveelheid
    p("a4", None, "500 g", "aldi"),          # geen prijs
    p("a5", 0.10, "100 stuks", "aldi", available=False),  # niet beschikbaar: doet niet mee
]
pricing.annotate_unit_prices(products)
pricing.mark_cheapest(products)
by = {x["code"]: x for x in products}
assert by["a1"]["unit_label"] == "per stuk" and abs(by["a1"]["unit_price"] - 0.4983) < 1e-4
assert by["a2"]["unit_label"] == "per kg" and by["a2"]["unit_price"] == 2.98
assert by["h3"]["unit_price"] is None and by["a4"]["unit_price"] is None
assert by["a1"]["cheapest"] and not by["h1"]["cheapest"], "per stuk: a1 is goedkoper"
assert by["a2"]["cheapest"] and not by["h2"]["cheapest"], "per kg: a2 is goedkoper"
assert not by["a3"]["cheapest"], "één product per eenheid: niets om mee te vergelijken"
assert not by["a5"]["cheapest"], "niet-beschikbare producten tellen niet mee"

# ---------------------------------------------------------------- Checkjebon-verwerking (aangenomen vorm)
sample = [
    {"n": "Albert Heijn", "d": [{"n": "Melk", "p": 1.0, "s": "1 l", "l": "/ah/melk"}]},
    {
        "n": "Hoogvliet",
        "d": [
            {"n": "Pink Lady appels", "p": 2.49, "s": "4 stuks", "l": "/product/pink-lady-appels-726992000"},
            {"n": "Volle kwark", "p": 1.19, "s": "500 g", "l": "https://www.hoogvliet.com/product/volle-kwark-123456789"},
            {"n": "Magere kwark vanille", "p": 1.29, "s": "500 g"},
            {"n": "Kwarkbollen", "p": 1.99, "s": "6 stuks", "l": "/product/kwarkbollen-333333333"},
            {"n": "Volle kwark", "p": 1.19, "s": "500 g", "l": "https://www.hoogvliet.com/product/volle-kwark-123456789"},  # dubbel
            {"n": "Gratis ding", "p": 0, "s": "1 stuk"},        # prijs 0: overslaan
            {"n": "Kapot", "p": "veel", "s": "1 stuk"},         # prijs geen getal: overslaan
            {"p": 1.0},                                          # geen naam: overslaan
            "rommel",
        ],
    },
]
parsed = hoogvliet.parse_checkjebon(json.dumps(sample))
assert [x["name"] for x in parsed] == ["Pink Lady appels", "Volle kwark", "Magere kwark vanille", "Kwarkbollen"], parsed
assert parsed[0]["url"] == "https://www.hoogvliet.com/product/pink-lady-appels-726992000", "relatieve link krijgt het domein"
assert parsed[2]["url"] is None
assert parsed[0]["code"].startswith("hoogvliet:")

# Echte vorm (Checkjebon, okt 2026): winkel heeft 'u', producten alleen n/l/p/s, en 'l' is een
# slug die Checkjebon zelf uit de naam maakt, zonder productnummer -> dus geen bruikbare link
real_shape = [{"n": "hoogvliet", "u": "https://www.hoogvliet.com/product/", "d": [
    {"n": "Pink lady Pink lady op schaal", "l": "pink-lady-pink-lady-op-schaal", "p": 3.59, "s": "4 stuks"}]}]
rp = hoogvliet.parse_checkjebon(json.dumps(real_shape))[0]
assert rp["url"] is None, "slug zonder nummer is geen echte link"
assert rp["name"] == "Pink lady op schaal", rp["name"]
assert rp["code"] == "hoogvliet:Pink lady Pink lady op schaal|4 stuks", "code blijft op de originele naam"
assert hoogvliet.clean_name("Hak Pink Lady appelmoes") == "Hak Pink Lady appelmoes"
assert hoogvliet.clean_name("Melk") == "Melk"

# andere vormen en fouten geven een nette fout, geen crash
for bad in ("{niet json", json.dumps({"x": 1}), json.dumps([{"n": "Jumbo", "d": []}]),
            json.dumps([{"n": "Hoogvliet", "d": []}]), json.dumps("tekst")):
    try:
        hoogvliet.parse_checkjebon(bad)
    except api.AldiApiError:
        pass
    else:
        raise AssertionError(f"verwachtte fout voor {bad!r}")
# varianten van de sleutels worden ook herkend
alt = json.dumps({"supermarkets": [{"name": "Hoogvliet", "products": [{"name": "Melk", "price": 1.1, "size": "1 l"}]}]})
assert hoogvliet.parse_checkjebon(alt)[0]["name"] == "Melk"

# ---------------------------------------------------------------- zoeken in Hoogvliet
client = hoogvliet.HoogvlietClient(session=None)
client._products = parsed
client._fetched_at = 10**12
client._loaded = True
client._kick_refresh = lambda: None


def hv(q):
    return [x["name"] for x in asyncio.run(client.search(q))]


assert hv("kwark") == ["Volle kwark", "Magere kwark vanille", "Kwarkbollen"], hv("kwark")
assert hv("PINK lady") == ["Pink Lady appels"]
assert hv("volle kwark") == ["Volle kwark"] and hv("x") == [] and hv("bestaatniet") == []
res = asyncio.run(client.search("pink"))[0]
assert res["store"] == "hoogvliet" and res["store_name"] == "Hoogvliet" and res["price"] == 2.49
assert res["description"] == "4 stuks" and res["image"] == "https://static.hoogvliet.nl/ecom/product/726992000.jpg"

empty = hoogvliet.HoogvlietClient(session=None)
empty._loaded = True
empty._kick_refresh = lambda: None
try:
    asyncio.run(empty.search("kwark"))
except hoogvliet.HoogvlietError as err:
    assert "opgehaald" in str(err)
else:
    raise AssertionError("zonder data moet er een duidelijke fout komen")


# ---------------------------------------------------------------- winkels samenvoegen
class Fake:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    async def search(self, query, limit=48):
        self.calls.append((query, limit))
        if self.error:
            raise self.error
        return [dict(x) for x in self.result]


def mk(code, price, size, store):
    return {"code": code, "name": code, "price": price, "description": size, "available": True,
            "store": store, "store_name": store.title()}


aldi_items = [mk("a1", 2.99, "6 stuks", "aldi"), mk("a2", 1.49, "500 g", "aldi"), mk("a3", 9.0, "1 l", "aldi")]
hv_items = [mk("h1", 2.49, "4 stuks", "hoogvliet"), mk("h2", 2.99, "1 kg", "hoogvliet")]
svc = search.SearchService({"aldi": Fake(aldi_items), "hoogvliet": Fake(hv_items)})

merged, warnings = asyncio.run(svc.search("x1", limit=48))
assert warnings == []
assert [m["code"] for m in merged] == ["a1", "h1", "a2", "h2", "a3"], "om en om, op relevantie"
flags = {m["code"]: m["cheapest"] for m in merged}
assert flags == {"a1": True, "h1": False, "a2": True, "h2": False, "a3": False}, flags
assert len(asyncio.run(svc.search("x1", limit=3))[0]) == 3, "limiet geldt voor het totaal"

# één winkel valt uit: de andere resultaten blijven, met een waarschuwing
svc = search.SearchService({"aldi": Fake(error=api.AldiApiError("sleutel geweigerd")), "hoogvliet": Fake(hv_items)})
merged, warnings = asyncio.run(svc.search("x1"))
assert [m["code"] for m in merged] == ["h1", "h2"]
assert warnings == ["Aldi: sleutel geweigerd"], warnings

# alle winkels vallen uit: duidelijke fout in plaats van "niets gevonden"
svc = search.SearchService({"aldi": Fake(error=api.AldiApiError("a stuk")), "hoogvliet": Fake(error=api.AldiApiError("h stuk"))})
try:
    asyncio.run(svc.search("x1"))
except api.AldiApiError as err:
    assert "a stuk" in str(err) and "h stuk" in str(err)
else:
    raise AssertionError("verwachtte een fout als alles faalt")

# een onverwachte fout in één winkel breekt het zoeken ook niet af
svc = search.SearchService({"aldi": Fake(error=RuntimeError("boem")), "hoogvliet": Fake(hv_items)})
merged, warnings = asyncio.run(svc.search("x1"))
assert len(merged) == 2 and warnings == ["Aldi: onverwachte fout"], warnings

# lookup (prijzen bijwerken) kapt niet af en geeft alles van alle winkels
svc = search.SearchService({"aldi": Fake(aldi_items), "hoogvliet": Fake(hv_items)})
assert {x["code"] for x in asyncio.run(svc.lookup("x1"))} == {"a1", "a2", "a3", "h1", "h2"}

# ---------------------------------------------------------------- Hoogvliet-foto's
# Echte voorbeelden van hoogvliet.nl: de og:image van deze productpagina is
# https://static.hoogvliet.nl/ecom/product/726992000.jpg
real = "https://hoogvliet.nl/product/pink-lady-appels-op-schaal-726992000"
assert hoogvliet.product_id(real) == "726992000"
assert hoogvliet.image_url(real) == "https://static.hoogvliet.nl/ecom/product/726992000.jpg"
assert hoogvliet.image_url(real + "/?utm=1#x") == "https://static.hoogvliet.nl/ecom/product/726992000.jpg"
assert hoogvliet.image_url("https://www.hoogvliet.com/product/726992000") == "https://static.hoogvliet.nl/ecom/product/726992000.jpg"
assert hoogvliet.image_url("https://www.hoogvliet.com/product/pink-lady-appels-op-schaal") is None, "geen nummer in de link"
assert hoogvliet.image_url("https://x.nl/product/jaar-2026") is None, "te kort om een productnummer te zijn"
assert hoogvliet.image_url(None) is None and hoogvliet.image_url("") is None

with_id = hoogvliet.HoogvlietClient(session=None)
with_id._products = [{"code": "hoogvliet:1", "name": "Pink Lady appels", "size": "4 stuks", "price": 2.49, "url": real, "norm": "pink lady appels"}]
with_id._fetched_at, with_id._loaded = 10**12, True
found = asyncio.run(with_id.search("pink"))[0]
assert found["image"] == "https://static.hoogvliet.nl/ecom/product/726992000.jpg", "foto zonder enig verzoek afgeleid"


class FakeResp:
    def __init__(self, status, headers=None, body=b""):
        self.status, self.headers = status, headers or {}
        self.content = types.SimpleNamespace(read=self._read)
        self._body = body

    async def _read(self, n):
        return self._body[:n]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    def __init__(self, resp=None, error=None):
        self.resp, self.error, self.urls = resp, error, []

    def get(self, url, **kw):
        self.urls.append((url, kw.get("allow_redirects")))
        if self.error:
            raise self.error
        return self.resp


def enrich(session, product):
    return asyncio.run(hoogvliet.HoogvlietClient(session=session).enrich(product))


no_id = {"url": "https://www.hoogvliet.com/product/pink-lady-appels-op-schaal", "image": None, "name": "x"}
# 1. de link zonder nummer stuurt door naar een link mét nummer: daar halen we de foto uit
redirect = FakeSession(FakeResp(302, {"Location": real}))
out = enrich(redirect, no_id)
assert out["image"] == "https://static.hoogvliet.nl/ecom/product/726992000.jpg"
assert redirect.urls == [(no_id["url"], False)], "één verzoek, zonder de doorverwijzing te volgen"
# 2. geen doorverwijzing, wel een og:image in de pagina
page = b'<head><meta property="og:image" content="https://static.hoogvliet.nl/ecom/product/5555555.jpg"></head>'
assert enrich(FakeSession(FakeResp(200, body=page)), no_id)["image"].endswith("/5555555.jpg")
# 3. mislukt, onbekend of niets te vinden: product blijft gewoon bruikbaar, zonder foto
assert enrich(FakeSession(FakeResp(404)), no_id) == no_id
assert enrich(FakeSession(error=TimeoutError()), no_id) == no_id, "time-out: product blijft bruikbaar"
assert enrich(FakeSession(FakeResp(200, body=b"<html></html>")), no_id) == no_id
# 4. geen verzoek als het niet nodig is of niet van Hoogvliet is
untouched = FakeSession(FakeResp(500))
already = {**no_id, "image": "https://static.hoogvliet.nl/ecom/product/1.jpg"}
assert enrich(untouched, already) == already
assert enrich(untouched, {**no_id, "url": "https://evil.example/product/x"})["image"] is None
assert enrich(untouched, {"url": None, "image": None}) == {"url": None, "image": None}
assert untouched.urls == [], "geen enkel verzoek gedaan"


class Boom:
    async def search(self, q, limit=48):
        return []

    async def enrich(self, product):
        raise RuntimeError("boem")


class NoEnrich:
    async def search(self, q, limit=48):
        return []


prod = {"store": "aldi", "name": "x"}
svc = search.SearchService({"aldi": NoEnrich(), "hoogvliet": Boom()})
assert asyncio.run(svc.enrich(prod)) == prod, "winkel zonder enrich: ongewijzigd"
assert asyncio.run(svc.enrich({"store": "hoogvliet", "name": "y"})) == {"store": "hoogvliet", "name": "y"}, "fout: ongewijzigd"
assert asyncio.run(svc.enrich({"name": "los"})) == {"name": "los"}, "zonder winkel: ongewijzigd"

print("ok: eenheden, goedkoopst, Checkjebon-verwerking, zoeken en samenvoegen kloppen")


# ---------------------------------------------------------------- linkvormen van Checkjebon
_l = hoogvliet._link
full = "https://www.hoogvliet.com/product/pink-lady-726992000"
assert _l(full) == full
assert _l("/product/pink-lady-726992000") == full
assert _l("product/pink-lady-726992000") == full
assert _l("www.hoogvliet.com/product/pink-lady-726992000") == full
assert _l("pink-lady-726992000") == full
assert _l("pink lady!") is None and _l("") is None and _l(None) is None
assert _l("pink-lady-726992000", "https://www.hoogvliet.com/product/") == full
assert hoogvliet.image_url(_l("pink-lady-726992000")) == "https://static.hoogvliet.nl/ecom/product/726992000.jpg"
print("linkvormen ok")


# ---------------------------------------------------------------- live zoeken (echte vorm van /api/graphql)
def gq(key, name, price, regular=None, meta=None, stock="IN_STOCK"):
    return {
        "id": 6193, "stock_status": stock, "name": name, "url_key": key,
        "metadata": json.dumps(meta) if meta is not None else None,
        "price_range": {"maximum_price": None, "minimum_price": {
            "regular_price": {"value": regular or price, "currency": "EUR"},
            "final_price": {"value": price, "currency": "EUR"}, "discount": {"percent_off": 0}}},
    }


answer = {"data": {"products": {"items": [
    gq("pink-lady-appels-op-schaal-726992000", "Appels op schaal", 3.39,
       meta={"merk": "Pink lady", "inhoud_ce": 4, "eenheid": "Stuks", "ean": "8711905017940"}),
    gq("volle-melk-111111111", "Volle melk", 1.09, regular=1.29, meta={"merk": "Melkan", "inhoud_ce": 1.5, "eenheid": "Liter"}),
    gq("melkan-karnemelk-222222222", "Melkan karnemelk", 1.0, meta={"merk": "Melkan"}, stock="OUT_OF_STOCK"),
    gq("geen-prijs-333333333", "Zonder prijs", 0),
    {"name": "Zonder key", "price_range": {}},
]}}}
res = web.parse_response(answer)
assert [x["name"] for x in res] == ["Pink lady Appels op schaal", "Melkan Volle melk", "Melkan karnemelk"], [x["name"] for x in res]
a, b, c = res
assert a["url"] == "https://hoogvliet.nl/product/pink-lady-appels-op-schaal-726992000"
assert a["image"] == "https://static.hoogvliet.nl/ecom/product/726992000.jpg" and a["pid"] == "726992000"
assert a["size"] == "4 stuks" and a["price"] == 3.39 and a["old_price"] is None and a["available"]
assert b["size"] == "1.5 liter" and b["old_price"] == 1.29
assert c["size"] is None and c["available"] is False, "uitverkocht"
assert pricing.parse_size(a["size"]) == (4.0, "stuk") and pricing.parse_size(b["size"]) == (1.5, "l")
assert web.parse_response({"data": {"products": {"items": []}}}) == []
for bad in ("tekst", {"errors": [{"message": "kapot"}]}, {"data": None}):
    try:
        web.parse_response(bad)
    except hoogvliet.HoogvlietError:
        pass
    else:
        raise AssertionError(f"verwachtte fout voor {bad!r}")


class WebSession:
    def __init__(self, body, status=200):
        self.body, self.status, self.calls, self.last = body, status, 0, None

    def get(self, url, **kw):
        self.calls += 1
        self.last = (url, kw)
        outer = self

        class Ctx:
            async def __aenter__(s):
                return types.SimpleNamespace(
                    status=outer.status,
                    content=types.SimpleNamespace(read=lambda n: _aread(outer.body)),
                )

            async def __aexit__(s, *a):
                return False

        return Ctx()


async def _aread(b):
    return b


web.MIN_INTERVAL = 0
sess = WebSession(json.dumps(answer).encode())
ws = web.HoogvlietWebSearch(sess)
assert len(asyncio.run(ws.search("Pink Lady"))) == 3
assert len(asyncio.run(ws.search("pink  lady"))) == 3 and sess.calls == 1, "tweede keer uit het geheugen"
url, kw = sess.last
assert url == "https://hoogvliet.nl/api/graphql" and kw["params"]["operationName"] == "GetProductsSearch"
assert json.loads(kw["params"]["variables"])["inputText"] == "pink lady"
assert "HomeAssistant" in kw["headers"]["User-Agent"], "eerlijke User-Agent"
for bad in (WebSession(b"<html></html>"), WebSession(b"", status=403), WebSession(b'{"errors":[{"message":"x"}]}')):
    try:
        asyncio.run(web.HoogvlietWebSearch(bad).search("x"))
    except hoogvliet.HoogvlietError:
        pass
    else:
        raise AssertionError("verwachtte fout")

# de Hoogvliet-client gebruikt live resultaten, en valt bij een fout terug op Checkjebon
live = hoogvliet.HoogvlietClient(session=None, web=web.HoogvlietWebSearch(WebSession(json.dumps(answer).encode())))
out = asyncio.run(live.search("pink lady"))
assert out[0]["name"] == "Pink lady Appels op schaal" and out[0]["code"] == "hoogvliet:web:726992000"
assert out[0]["image"].endswith("726992000.jpg") and out[0]["store"] == "hoogvliet" and out[0]["brand"] == "Pink lady"
assert out[2]["available"] is False
fallback = hoogvliet.HoogvlietClient(session=None, web=web.HoogvlietWebSearch(WebSession(b"", status=503)))
fallback._products = parsed
fallback._fetched_at = 10**12
fallback._loaded = True
assert [x["name"] for x in asyncio.run(fallback.search("kwark"))][:1] == ["Volle kwark"], "terugval op Checkjebon"
print("ok: live zoeken (GraphQL, terugval) klopt")


# ---------------------------------------------------------------- prijzen verversen
# alleen de winkels waar het product van is, en bij Hoogvliet live + prijslijst samengevoegd
aldi_c, hv_c = Fake(aldi_items), Fake(hv_items)
svc = search.SearchService({"aldi": aldi_c, "hoogvliet": hv_c})
got = asyncio.run(svc.lookup("melk", {"aldi"}))
assert {x["code"] for x in got} == {"a1", "a2", "a3"} and hv_c.calls == [], "Hoogvliet niet gevraagd"
assert asyncio.run(svc.lookup("melk", {"picnic"})) == [], "onbekende winkel: niets, geen fout"
assert len(asyncio.run(svc.lookup("melk"))) == 5 and len(hv_c.calls) == 1

web_item = {"name": "Pink lady Appels op schaal", "price": 3.49, "size": "4 stuks",
            "url": "https://hoogvliet.nl/product/pink-lady-appels-op-schaal-726992000",
            "pid": "726992000", "image": None, "available": True}


class FakeWeb:
    def __init__(self, items=None, error=None):
        self.items, self.error, self.calls = items or [], error, 0

    async def search(self, q):
        self.calls += 1
        if self.error:
            raise self.error
        return self.items


hvc = hoogvliet.HoogvlietClient(session=None, web=FakeWeb([web_item]))
hvc._products = parsed
hvc._fetched_at = 10**12
hvc._loaded = True
codes = [x["code"] for x in asyncio.run(hvc.refresh_search("pink lady"))]
assert "hoogvliet:web:726992000" in codes, "webshop-item krijgt zijn nieuwe prijs"
down = hoogvliet.HoogvlietClient(session=None, web=FakeWeb(error=api.AldiApiError("down")))
down._products, down._fetched_at, down._loaded = parsed, 10**12, True
assert [x["name"] for x in asyncio.run(down.refresh_search("kwark"))][:1] == ["Volle kwark"], "valt terug op de prijslijst"
plain = hoogvliet.HoogvlietClient(session=None)
plain._products, plain._fetched_at, plain._loaded = parsed, 10**12, True
assert asyncio.run(plain.refresh_search("kwark")), "zonder webshop: prijslijst"
print("ok: prijzen verversen klopt")


# ---------------------------------------------------------------- Picnic (met een nepbibliotheek)
S = types.SimpleNamespace


class PAuth(Exception): ...
class P2FA(Exception): ...
class P2FAError(Exception): ...


class FakeSessionP:
    auth_token = None


class FakePicnic:
    """Lijkt op python_picnic_api2.PicnicAPI, met te sturen gedrag."""
    behaviour = {"login": "ok", "search": "ok", "verify": "ok"}
    instances = []
    searches = 0

    def __init__(self, username=None, password=None, country_code="NL", auth_token=None):
        self.session = FakeSessionP()
        self.session.auth_token = auth_token
        self.logins = 0
        FakePicnic.instances.append(self)

    def login(self, username, password):
        self.logins += 1
        if self.behaviour["login"] == "bad":
            raise PAuth("fout")
        self.session.auth_token = "token-na-login"
        if self.behaviour["login"] == "2fa":
            raise P2FA("code nodig")

    def generate_2fa_code(self, channel):
        self.channel = channel

    def verify_2fa_code(self, code):
        if code != "123456":
            raise P2FAError("verkeerd")
        self.session.auth_token = "token-na-2fa"

    def search(self, term):
        FakePicnic.searches += 1
        if self.behaviour["search"] == "expired" and self.session.auth_token == "oud":
            raise PAuth("verlopen")
        return S(items=[
            S(id="s100", name="Pink Lady appels", display_price=349, image_id="img1", unit_quantity="4 stuks"),
            S(id="s101", name="Actietegel", display_price=None, image_id=None, unit_quantity=None),
            S(id="s102", name="Gratis", display_price=0, image_id=None, unit_quantity="1 stuk"),
            S(id="", name="Zonder id", display_price=100, image_id=None, unit_quantity=None),
        ])


sys.modules["python_picnic_api2"] = S(PicnicAPI=FakePicnic, PicnicAuthError=PAuth, Picnic2FARequired=P2FA, Picnic2FAError=P2FAError)

res = picnic.parse_search(S(items=[
    S(id="s100", name="Pink Lady appels", display_price=349, image_id="img1", unit_quantity="4 stuks"),
    S(id="s101", name="Actietegel", display_price=None, image_id=None, unit_quantity=None),
    S(id="s102", name="Gratis", display_price=0, image_id=None, unit_quantity="1 stuk"),
]))
assert len(res) == 1 and res[0]["code"] == "picnic:s100" and res[0]["price"] == 3.49 and res[0]["store"] == "picnic"
assert res[0]["image"].endswith("/img1/medium.png") and res[0]["description"] == "4 stuks" and res[0]["url"] is None
assert pricing.parse_size(res[0]["description"]) == (4.0, "stuk")
assert picnic.parse_search(S(items=None)) == [] and picnic.parse_search(None) == []


class FakeHass:
    async def async_add_executor_job(self, fn, *args):
        return fn(*args)


class MemStore:
    def __init__(self, data=None): self.data, self.saved = data, []
    async def async_load(self): return self.data
    async def async_save(self, d): self.saved.append(d); self.data = d


picnic.MIN_INTERVAL = 0
# met token: geen login nodig, resultaten onthouden
FakePicnic.behaviour = {"login": "ok", "search": "ok", "verify": "ok"}
FakePicnic.searches, FakePicnic.instances = 0, []
store = MemStore()
pc = picnic.PicnicClient(FakeHass(), "a@b.nl", "geheim", "tok", store)
asyncio.run(pc.async_load())
out = asyncio.run(pc.search("appels"))
assert [x["code"] for x in out] == ["picnic:s100"] and FakePicnic.instances[0].logins == 0
asyncio.run(pc.search("Appels"))
assert FakePicnic.searches == 1, "tweede keer uit het geheugen"
assert store.saved == [], "token niet veranderd: niets opgeslagen"

# verlopen token: één keer opnieuw inloggen, nieuw token bewaard
FakePicnic.behaviour = {"login": "ok", "search": "expired", "verify": "ok"}
FakePicnic.searches, FakePicnic.instances = 0, []
store = MemStore()
pc = picnic.PicnicClient(FakeHass(), "a@b.nl", "geheim", "oud", store)
assert asyncio.run(pc.search("melk"))[0]["code"] == "picnic:s100"
assert pc._token == "token-na-login" and store.saved[-1] == {"seed": "oud", "token": "token-na-login"}
# opnieuw laden: het bewaarde token telt alleen voor dezelfde instellingen
again = picnic.PicnicClient(FakeHass(), "a@b.nl", "geheim", "oud", store)
asyncio.run(again.async_load()); assert again._token == "token-na-login"
other = picnic.PicnicClient(FakeHass(), "a@b.nl", "geheim", "nieuw-uit-instellingen", store)
asyncio.run(other.async_load()); assert other._token == "nieuw-uit-instellingen"

# fouten: wachtwoord fout, 2FA nodig, geen gegevens, onverwacht
def expect(client, exc, query="x1"):
    try:
        asyncio.run(client.search(query))
    except exc as err:
        return err
    raise AssertionError(f"verwachtte {exc}")

FakePicnic.behaviour = {"login": "bad", "search": "expired", "verify": "ok"}
expect(picnic.PicnicClient(FakeHass(), "a@b.nl", "fout", "oud"), picnic.PicnicLoginError)
FakePicnic.behaviour = {"login": "2fa", "search": "expired", "verify": "ok"}
assert "verificatiecode" in str(expect(picnic.PicnicClient(FakeHass(), "a@b.nl", "x", "oud"), picnic.PicnicLoginError))
expect(picnic.PicnicClient(FakeHass(), "", "", None), picnic.PicnicLoginError)
assert isinstance(picnic.PicnicLoginError("x"), api.AldiApiError), "wordt als waarschuwing getoond"

# inloggen vanuit de config flow, met en zonder 2FA
FakePicnic.behaviour = {"login": "ok", "search": "ok", "verify": "ok"}
setup = picnic.PicnicSetup(FakeHass())
assert asyncio.run(setup.login("a@b.nl", "pw")) == "token-na-login"
FakePicnic.behaviour = {"login": "2fa", "search": "ok", "verify": "ok"}
setup = picnic.PicnicSetup(FakeHass())
try:
    asyncio.run(setup.login("a@b.nl", "pw"))
except picnic.PicnicNeeds2FA:
    pass
else:
    raise AssertionError("2FA verwacht")
asyncio.run(setup.send_code("EMAIL"))
assert setup._api.channel == "EMAIL"
try:
    asyncio.run(setup.verify("000000"))
except picnic.PicnicLoginError:
    pass
else:
    raise AssertionError("foute code")
assert asyncio.run(setup.verify("123456")) == "token-na-2fa"
FakePicnic.behaviour = {"login": "bad", "search": "ok", "verify": "ok"}
try:
    asyncio.run(picnic.PicnicSetup(FakeHass()).login("a@b.nl", "fout"))
except picnic.PicnicLoginError as err:
    assert not isinstance(err, picnic.PicnicNeeds2FA)

# zonder bibliotheek: duidelijke fout
del sys.modules["python_picnic_api2"]
import builtins
real_import = builtins.__import__
def no_lib(name, *a, **k):
    if name.startswith("python_picnic_api2"):
        raise ImportError(name)
    return real_import(name, *a, **k)
builtins.__import__ = no_lib
try:
    expect(picnic.PicnicClient(FakeHass(), "a", "b", "tok"), picnic.PicnicLibraryError)
finally:
    builtins.__import__ = real_import

# samen met de andere winkels
class PicnicFake(Fake): ...
svc = search.SearchService({"aldi": Fake(aldi_items), "picnic": PicnicFake([mk("picnic:s1", 1.0, "1 kg", "picnic")])})
merged, warns = asyncio.run(svc.search("x"))
assert {x["store"] for x in merged} == {"aldi", "picnic"} and warns == []
print("ok: Picnic (zoeken, token, 2FA, fouten) klopt")
