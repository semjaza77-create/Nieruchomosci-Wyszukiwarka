#!/usr/bin/env python3
"""
Rdzeń wyszukiwania wielo-portalowego: Otodom + nieruchomosci-online.pl +
Morizon.pl (celowo BEZ OLX - blokuje boty przez CloudFront, wymagałby
Jiny i szybko zjadał limit zapytań).

Ważne: ten plik NIE modyfikuje otodom_core.py ani nie jest przez niego
importowany - agent mailowy (otodom_agent.py) zostaje całkowicie
nietknięty. Zamiast tego KAŻDE źródło normalizuje swoje oferty do
dokładnie tego samego kształtu co surowe dane Otodom (title, totalPrice,
areaInSquareMeters, roomsNumber jako ONE..TEN, location.address.city.name,
isPrivateOwner, shortDescription) - dzięki temu reużywamy NIEZMIENIONE
funkcje filtrujące z otodom_core.py (meets_rooms_requirement,
meets_area_requirement, meets_owner_requirement, mentions_required,
meets_amenity_requirement) zamiast pisać je od nowa dla każdego portalu.

Cena: Otodom filtruje ją po swojej stronie (URL), Morizon też (ps[price_from]/
ps[price_to] - podpatrzone w jego własnych danych). nieruchomosci-online.pl
nie ma znanego parametru URL na cenę (sprawdzone empirycznie), więc dla
niej - i jako siatka bezpieczeństwa dla wszystkich źródeł - cena jest
zawsze też sprawdzana po naszej stronie (meets_price_requirement).

URUCHOM (szybki test w terminalu): python3 multi_core.py
"""

import re
import json
from pathlib import Path

import requests

import otodom_core as core

HTTP_HEADERS = core.HTTP_HEADERS

CITY_SLUGS_ASCII = {
    "Kraków": "krakow",
    "Warszawa": "warszawa",
    "Wrocław": "wroclaw",
    "Poznań": "poznan",
    "Gdańsk": "gdansk",
}


def rooms_int_to_enum(n):
    """Zamienia liczbę pokoi (int, tak dają nieruchomosci-online/Morizon) na
    etykietę ONE..TEN, której używa otodom_core (tak numeruje sam Otodom)."""
    if n is None:
        return None
    idx = max(1, min(int(n), len(core.ROOMS_ORDER))) - 1
    return core.ROOMS_ORDER[idx]


def meets_price_requirement(item: dict, cfg: dict) -> bool:
    price = (item.get("totalPrice") or {}).get("value")
    if price is None:
        return True
    if cfg.get("price_min") and price < cfg["price_min"]:
        return False
    if cfg.get("price_max") and price > cfg["price_max"]:
        return False
    return True


def _parse_json_ld_blocks(html: str) -> list:
    blocks = re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    parsed = []
    for b in blocks:
        try:
            parsed.append(json.loads(b))
        except json.JSONDecodeError:
            continue
    return parsed


# ============================================================
# nieruchomosci-online.pl - JSON-LD (schema.org) wprost w wynikach
# wyszukiwania (cena, url, opis, adres, metraż, pokoje). Strona pojedynczej
# oferty ma współrzędne w polu "geo" tego samego typu JSON-LD, oraz tekst
# "ogłoszenie prywatne" dla ofert bez pośrednika - obie rzeczy czytamy
# JEDNYM zapytaniem.
# ============================================================
NO_SEARCH_URL = "https://www.nieruchomosci-online.pl/szukaj.html"


def build_no_url(cfg: dict) -> str:
    transaction = "wynajem" if cfg.get("transaction") == "wynajem" else "sprzedaz"
    city = cfg.get("city_display", "Kraków")
    return f"{NO_SEARCH_URL}?3,mieszkanie,{transaction},,{city}"


def fetch_no_items(cfg: dict) -> list:
    resp = requests.get(build_no_url(cfg), headers=HTTP_HEADERS, timeout=30)
    resp.raise_for_status()
    items = []
    for data in _parse_json_ld_blocks(resp.text):
        offers = ((data.get("mainEntity") or {}).get("offers")) or []
        if isinstance(offers, dict):
            offers = [offers]
        for agg in offers:
            for offer in agg.get("offers", []):
                acc = offer.get("itemOffered", {}) or {}
                address = acc.get("address", {}) or {}
                floor_size = (acc.get("floorSize") or {}).get("value")
                items.append({
                    "title": offer.get("name", ""),
                    "totalPrice": {"value": float(offer["price"])} if offer.get("price") else {},
                    "areaInSquareMeters": float(floor_size) if floor_size else None,
                    "roomsNumber": rooms_int_to_enum(acc.get("numberOfRooms")),
                    "shortDescription": acc.get("description", ""),
                    "location": {"address": {"city": {"name": address.get("addressLocality")}}},
                    "isPrivateOwner": None,  # nieznane na etapie listy - ustalane przy pobieraniu szczegółów
                    "_source": "nieruchomosci-online",
                    "_full_url": offer.get("url"),
                })
    return items


def fetch_no_details(url: str):
    """Jedno zapytanie o stronę oferty -> (lat, lon) | None, is_private | None."""
    resp = requests.get(url, headers=HTTP_HEADERS, timeout=30)
    resp.raise_for_status()
    html = resp.text
    m = re.search(r'"geo":\{"@type":"GeoCoordinates","latitude":"([\d.]+)","longitude":"([\d.]+)"', html)
    coords = (float(m.group(1)), float(m.group(2))) if m else None
    is_private = "ogłoszenie prywatne" in html.lower() or "ogloszenie prywatne" in html.lower()
    return coords, is_private


# ============================================================
# Morizon.pl - JSON-LD wprost w wynikach (ten sam schemat co wyżej).
# Strona pojedynczej oferty NIE ma współrzędnych w JSON-LD - są schowane
# w __NUXT_DATA__, własnym formacie Nuxt: płaska lista, gdzie obiekty/listy
# odwołują się do innych elementów przez INDEKS zamiast trzymać wartości
# wprost (dedupe). _resolve_nuxt to rozwiązuje rekurencyjnie.
# ============================================================
def build_morizon_url(cfg: dict) -> str:
    transaction = "do-wynajecia" if cfg.get("transaction") == "wynajem" else "na-sprzedaz"
    city_slug = CITY_SLUGS_ASCII.get(cfg.get("city_display"), "krakow")
    url = f"https://www.morizon.pl/{transaction}/mieszkania/{city_slug}/"
    params = []
    if cfg.get("price_min"):
        params.append(f"ps%5Bprice_from%5D={int(cfg['price_min'])}")
    if cfg.get("price_max"):
        params.append(f"ps%5Bprice_to%5D={int(cfg['price_max'])}")
    if params:
        url += "?" + "&".join(params)
    return url


def fetch_morizon_items(cfg: dict) -> list:
    resp = requests.get(build_morizon_url(cfg), headers=HTTP_HEADERS, timeout=30)
    resp.raise_for_status()
    items = []
    for data in _parse_json_ld_blocks(resp.text):
        if data.get("@type") != "Product":
            continue
        offers = (data.get("offers") or {}).get("offers", [])
        for offer in offers:
            acc = offer.get("itemOffered", {}) or {}
            address = acc.get("address", {}) or {}
            floor_size = (acc.get("floorSize") or {}).get("value")
            items.append({
                "title": offer.get("name", ""),
                "totalPrice": {"value": float(offer["price"])} if offer.get("price") else {},
                "areaInSquareMeters": float(floor_size) if floor_size else None,
                "roomsNumber": rooms_int_to_enum(acc.get("numberOfRooms")),
                "shortDescription": acc.get("description", ""),
                "location": {"address": {"city": {"name": address.get("addressLocality")}}},
                "isPrivateOwner": None,
                "_source": "morizon",
                "_full_url": offer.get("url"),
            })
    return items


def _resolve_nuxt(raw_list: list, idx: int, cache: dict):
    if idx in cache:
        return cache[idx]
    val = raw_list[idx]
    if isinstance(val, dict):
        out = {}
        cache[idx] = out
        for k, v in val.items():
            out[k] = _resolve_nuxt(raw_list, v, cache) if isinstance(v, int) and 0 <= v < len(raw_list) else v
        return out
    if isinstance(val, list):
        out = []
        cache[idx] = out
        for v in val:
            out.append(_resolve_nuxt(raw_list, v, cache) if isinstance(v, int) and 0 <= v < len(raw_list) else v)
        return out
    cache[idx] = val
    return val


def _find_first(obj, key: str, depth: int = 0):
    if depth > 15:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_first(v, key, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_first(v, key, depth + 1)
            if found is not None:
                return found
    return None


def fetch_morizon_details(url: str):
    """Jedno zapytanie o stronę oferty -> (lat, lon) | None, is_private | None."""
    resp = requests.get(url, headers=HTTP_HEADERS, timeout=30)
    resp.raise_for_status()
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S)
    if not m:
        return None, None
    raw = json.loads(m.group(1))
    root = _resolve_nuxt(raw, 0, {})

    coords = None
    location = _find_first(root, "location")
    if isinstance(location, dict):
        center = (location.get("map") or {}).get("center") or {}
        if "latitude" in center and "longitude" in center:
            coords = (center["latitude"], center["longitude"])

    is_private = None
    contact = _find_first(root, "contact")
    if isinstance(contact, dict):
        person = contact.get("person")
        company = contact.get("company")
        if person and person.get("type") == "OWNER":
            is_private = True
        elif company:
            is_private = False

    return coords, is_private


SOURCE_DETAILS_FETCHERS = {
    "nieruchomosci-online": fetch_no_details,
    "morizon": fetch_morizon_details,
}


# ============================================================
# Spinanie: normalizacja + reużycie filtrów z otodom_core.
# ============================================================
def evaluate_multi_item(item: dict, cfg: dict) -> dict:
    description = item.get("shortDescription") or ""
    source = item.get("_source", "otodom")
    url = item.get("_full_url") or core.item_url(item)

    result = {
        "source": source,
        "transaction": item.get("_transaction"),
        "title": item.get("title", ""),
        "url": url,
        "price": (item.get("totalPrice") or {}).get("value"),
        "area": item.get("areaInSquareMeters"),
        "rooms": item.get("roomsNumber"),
        "city": ((item.get("location") or {}).get("address") or {}).get("city", {}).get("name"),
        "rooms_ok": core.meets_rooms_requirement(item, cfg),
        "area_ok": core.meets_area_requirement(item, cfg),
        "price_ok": meets_price_requirement(item, cfg),
        "required_mentions_ok": all(
            core.mentions_required(description, spec) for spec in cfg.get("required_mentions", [])
        ),
    }
    cheap_checks_ok = (
        result["rooms_ok"] and result["area_ok"] and result["price_ok"] and result["required_mentions_ok"]
    )

    # Współrzędne (a dla innych portali niż Otodom - przy okazji też typ
    # właściciela) pobieramy JEDNYM zapytaniem o stronę oferty, tylko gdy
    # tanie kryteria już przeszły i faktycznie czegoś z tej strony
    # potrzebujemy (bufory/avoid albo "tylko prywatni" dla portalu, który
    # nie ujawnia tego na liście wyników).
    result["amenities"] = {}
    result["avoid"] = {}
    coords = None
    need_owner_check = bool(cfg.get("private_owner_only")) and source != "otodom"
    need_details = cheap_checks_ok and (
        cfg.get("require_near_amenities") or cfg.get("avoid") or need_owner_check
    )

    if need_details:
        if source == "otodom":
            coords = core.fetch_coordinates(url)
        else:
            fetcher = SOURCE_DETAILS_FETCHERS.get(source)
            is_private = None
            if fetcher:
                try:
                    coords, is_private = fetcher(url)
                except requests.RequestException:
                    coords, is_private = None, None
            if is_private is not None:
                item["isPrivateOwner"] = is_private

    result["owner_ok"] = core.meets_owner_requirement(item, cfg)
    cheap_checks_ok = cheap_checks_ok and result["owner_ok"]

    if cheap_checks_ok and cfg.get("require_near_amenities"):
        if coords is None:
            result["amenity_ok"] = False
        else:
            amenities_cfg = cfg.get("amenities", {})
            result["amenities"] = core.meets_amenity_requirement(*coords, cfg)
            required_keys = [
                k for k, spec in amenities_cfg.items()
                if spec.get("required", True) and spec.get("buffer_m") is not None
            ]
            result["amenity_ok"] = all(result["amenities"].get(k, False) for k in required_keys)
    else:
        result["amenity_ok"] = True

    if cheap_checks_ok and result["amenity_ok"] and cfg.get("avoid"):
        if coords is None:
            result["avoid_ok"] = False
        else:
            avoid_cfg = cfg.get("avoid", {})
            avoid_probe_cfg = {"qgis_project_path": cfg.get("qgis_project_path"), "amenities": avoid_cfg}
            result["avoid"] = core.meets_amenity_requirement(*coords, avoid_probe_cfg)
            required_avoid_keys = [
                k for k, spec in avoid_cfg.items()
                if spec.get("required", True) and spec.get("buffer_m") is not None
            ]
            result["avoid_ok"] = not any(result["avoid"].get(k, False) for k in required_avoid_keys)
    else:
        result["avoid_ok"] = True

    result["passes"] = cheap_checks_ok and result["amenity_ok"] and result["avoid_ok"]
    return result


def search_all(cfg: dict) -> list:
    """Niezależne, bezstanowe wyszukiwanie łączone z 3 portali naraz
    (Otodom, nieruchomosci-online.pl, Morizon.pl - celowo bez OLX)."""
    raw_items = []

    otodom_url = core.build_search_url(cfg)
    otodom_html = core.fetch(otodom_url).text
    for it in core.extract_items(otodom_html):
        it["_source"] = "otodom"
        raw_items.append(it)

    raw_items.extend(fetch_no_items(cfg))
    raw_items.extend(fetch_morizon_items(cfg))

    return [evaluate_multi_item(it, cfg) for it in raw_items]


if __name__ == "__main__":
    CONFIG_FILE = Path(__file__).parent / "search_config.json"
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    print("🔎 Pobieram surowe listy z 3 portali...")
    raw = []
    otodom_html = core.fetch(core.build_search_url(cfg)).text
    otodom_items = core.extract_items(otodom_html)
    for it in otodom_items:
        it["_source"] = "otodom"
    no_items = fetch_no_items(cfg)
    morizon_items = fetch_morizon_items(cfg)
    print(f"   Otodom: {len(otodom_items)} | nieruchomosci-online: {len(no_items)} | Morizon: {len(morizon_items)}")

    all_raw = otodom_items + no_items + morizon_items
    print(f"\n📋 Oceniam {len(all_raw)} ofert łącznie (to może chwilę potrwać - dociąganie współrzędnych)...\n")
    results = [evaluate_multi_item(it, cfg) for it in all_raw]
    matches = [r for r in results if r["passes"]]
    for r in results:
        mark = "✅" if r["passes"] else "—"
        print(f"{mark} [{r['source']:22s}] {r['title'][:45]:45s} | {r['price']} PLN | {r['rooms']}")

    print(f"\n{len(matches)} z {len(results)} pasuje.")
