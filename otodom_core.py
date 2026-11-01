#!/usr/bin/env python3
"""
Wspólna logika wyszukiwania/filtrowania ofert Otodom, współdzielona przez:
- otodom_agent.py    - agent mailowy z pamięcią (wysyła tylko NOWE oferty)
- otodom_search.py   - bezstanowa wersja "od zera za każdym razem" (pod
                        przyszłą stronę internetową, gdzie każde wyszukanie
                        jest osobnym, niezależnym zapytaniem)

Każda funkcja przyjmuje `cfg` (słownik zgodny z search_config.json) jako
argument zamiast czytać globalną konfigurację - dzięki temu jeden proces
może w przyszłości obsłużyć wiele niezależnych wyszukiwań (np. dla różnych
użytkowników strony) bez współdzielenia stanu między nimi.
"""

import re
import json
import math
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs

import requests
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

def _resolve_qgz_path(raw_path: str) -> Path:
    """Względna ścieżka w qgis_project_path jest liczona od folderu tego
    pliku (nie od CWD procesu) - odporne na to, skąd faktycznie odpalono
    streamlit (lokalnie czy na Streamlit Cloud)."""
    p = Path(raw_path)
    return p if p.is_absolute() else (Path(__file__).parent / p)


HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# kolejność rosnąca - do porównywania "min. tyle pokoi"
ROOMS_ORDER = ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN"]

AMENITY_CRS_METRIC = "EPSG:2180"  # układ metryczny (Polska), do liczenia buforów w metrach

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def geocode_address(query: str) -> list:
    """Geokoduje dowolny adres przez Nominatim (OpenStreetMap) - zwraca listę
    kandydatów {display_name, lat, lon}, bo adres bywa niejednoznaczny."""
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": query, "format": "jsonv2", "addressdetails": 1, "limit": 5, "accept-language": "pl"},
        headers={"User-Agent": "otodom-agent-personal/1.0"},
        timeout=10,
    )
    resp.raise_for_status()
    return [
        {"display_name": item.get("display_name", ""), "lat": float(item["lat"]), "lon": float(item["lon"])}
        for item in resp.json()
    ]


def fetch(url: str) -> requests.Response:
    resp = requests.get(url, headers=HTTP_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp


def build_search_url(cfg: dict) -> str:
    base = f"https://www.otodom.pl/pl/wyniki/{cfg['transaction']}/mieszkanie/{cfg['city_slug']}"
    params = [f"limit={cfg.get('max_results_per_search', 72)}"]
    if cfg.get("price_max"):
        params.append(f"priceMax={int(cfg['price_max'])}")
    if cfg.get("price_min"):
        params.append(f"priceMin={int(cfg['price_min'])}")
    return base + "?" + "&".join(params)


def extract_items(html: str) -> list:
    """Wyciąga listę ofert z bloku __NEXT_DATA__ (gotowy JSON wbudowany w stronę)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', html)
    if not m:
        return []
    data = json.loads(m.group(1))
    return _find_key(data, "items") or []


def _find_key(obj, key: str, depth: int = 0):
    if depth > 8:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key, depth + 1)
            if found is not None:
                return found
    return None


def item_url(item: dict) -> str:
    return f"https://www.otodom.pl/pl/oferta/{item['slug']}"


def meets_rooms_requirement(item: dict, cfg: dict) -> bool:
    rooms = item.get("roomsNumber")
    if rooms not in ROOMS_ORDER:
        return True  # nieznany format - nie odrzucamy z tego powodu
    idx = ROOMS_ORDER.index(rooms)
    min_idx = ROOMS_ORDER.index(cfg["rooms_min"]) if cfg.get("rooms_min") else 0
    max_idx = ROOMS_ORDER.index(cfg["rooms_max"]) if cfg.get("rooms_max") else len(ROOMS_ORDER) - 1
    return min_idx <= idx <= max_idx


def meets_area_requirement(item: dict, cfg: dict) -> bool:
    if not cfg.get("area_min_m2"):
        return True
    area = item.get("areaInSquareMeters")
    return area is not None and area >= cfg["area_min_m2"]


def meets_owner_requirement(item: dict, cfg: dict) -> bool:
    if not cfg.get("private_owner_only"):
        return True
    return bool(item.get("isPrivateOwner"))


def mentions_required(text: str, spec: dict) -> bool:
    low = text.lower()
    for neg in spec.get("negations", []):
        if neg.lower() in low:
            return False
    return any(kw.lower() in low for kw in spec["any_of"])


# ============================================================
# Warstwy udogodnień prosto z projektu QGIS (PRJ_mieszkania.qgz) -
# nie korzystamy z ręcznie wyeksportowanych plików *_buf.gpkg, tylko
# czytamy bazowe warstwy wskazane w cfg["amenities"][klucz]["qgis_layer"]
# i buforujemy je w metrach (EPSG:2180) na żądanie. Mieszkanie musi leżeć
# w zasięgu WSZYSTKICH warstw oznaczonych jako "required": true.
# ============================================================
_qgis_layer_sources_cache: dict = {}   # {str(qgz_path): {layername: (provider, datasource)}}
_amenity_buffers_cache: dict = {}      # {cache_key: {klucz_udogodnienia: geometria}}
_raw_layer_cache: dict = {}            # {(str(qgz_path), qgis_layer): GeoDataFrame w metrach}


def _load_qgis_layer_sources(qgz_path: Path) -> dict:
    """Zwraca {nazwa_warstwy_w_projekcie: (provider, datasource)} z pliku .qgz."""
    cache_key = str(qgz_path)
    if cache_key in _qgis_layer_sources_cache:
        return _qgis_layer_sources_cache[cache_key]
    with zipfile.ZipFile(qgz_path) as zf:
        qgs_name = next(n for n in zf.namelist() if n.endswith(".qgs"))
        with zf.open(qgs_name) as f:
            root = ET.parse(f).getroot()
    sources = {}
    for maplayer in root.iter("maplayer"):
        name_el = maplayer.find("layername")
        ds_el = maplayer.find("datasource")
        prov_el = maplayer.find("provider")
        if name_el is None or ds_el is None or prov_el is None:
            continue
        sources[name_el.text] = (prov_el.text, ds_el.text)
    _qgis_layer_sources_cache[cache_key] = sources
    return sources


def _read_qgis_layer(qgz_path: Path, layer_display_name: str) -> gpd.GeoDataFrame:
    """Wczytuje warstwę o danej nazwie bezpośrednio ze źródła zapisanego w projekcie
    QGIS - obsługuje zwykłe warstwy OGR (gpkg/shp) oraz warstwę tekstową CSV/GTFS
    (provider 'delimitedtext', tak jak warstwa przystanków)."""
    sources = _load_qgis_layer_sources(qgz_path)
    if layer_display_name not in sources:
        raise KeyError(f"Nie znaleziono warstwy '{layer_display_name}' w projekcie {qgz_path.name}")
    provider, datasource = sources[layer_display_name]
    project_dir = qgz_path.parent

    if provider == "delimitedtext":
        path_part, _, query = datasource[len("file:"):].partition("?")
        params = parse_qs(query)
        csv_path = Path(path_part)
        if not csv_path.is_absolute():
            csv_path = (project_dir / csv_path).resolve()
        df = pd.read_csv(csv_path)
        xfield, yfield = params["xField"][0], params["yField"][0]
        crs = params.get("crs", ["EPSG:4326"])[0]
        return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[xfield], df[yfield]), crs=crs)

    if provider == "ogr":
        path_part, _, layer_opt = datasource.partition("|")
        gpkg_layer = layer_opt.split("=", 1)[1] if layer_opt.startswith("layername=") else None
        gpkg_path = Path(path_part)
        if not gpkg_path.is_absolute():
            gpkg_path = (project_dir / gpkg_path).resolve()
        return gpd.read_file(gpkg_path, layer=gpkg_layer)

    raise ValueError(f"Nieobsługiwany typ warstwy QGIS: {provider} ({layer_display_name})")


def _amenities_cache_key(cfg: dict) -> tuple:
    amenities = cfg.get("amenities", {})
    signature = tuple(sorted(
        (key, spec.get("qgis_layer"), spec.get("buffer_m"))
        for key, spec in amenities.items()
    ))
    return (cfg.get("qgis_project_path"), signature)


def _get_amenity_buffers(cfg: dict) -> dict:
    """Buforuje (w metrach) każdą skonfigurowaną warstwę udogodnień i zwraca
    {klucz_udogodnienia: geometria_bufora}. Wynik cache'owany per (projekt,
    zestaw warstw+promieni), więc kolejne wyszukiwania z tą samą konfiguracją
    nie liczą buforów od nowa."""
    cache_key = _amenities_cache_key(cfg)
    if cache_key in _amenity_buffers_cache:
        return _amenity_buffers_cache[cache_key]
    qgz_path = _resolve_qgz_path(cfg["qgis_project_path"])
    buffers = {}
    for key, spec in cfg.get("amenities", {}).items():
        buffer_m = spec.get("buffer_m")
        if buffer_m is None:
            continue  # warstwa skonfigurowana bez promienia - pomijana (wyłączona)
        gdf = _read_qgis_layer(qgz_path, spec["qgis_layer"]).to_crs(AMENITY_CRS_METRIC)
        buffers[key] = gdf.geometry.buffer(buffer_m).union_all()
    _amenity_buffers_cache[cache_key] = buffers
    return buffers


def meets_amenity_requirement(lat: float, lon: float, cfg: dict) -> dict:
    """Sprawdza punkt (lat, lon) względem KAŻDEJ warstwy udogodnień osobno."""
    buffers = _get_amenity_buffers(cfg)
    point = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(AMENITY_CRS_METRIC).iloc[0]
    return {key: point.within(geom) for key, geom in buffers.items()}


def _get_amenity_raw(cfg: dict) -> dict:
    """Surowe (niebuforowane) geometrie każdej skonfigurowanej warstwy, w metrach -
    cache'owane per plik+warstwa, żeby liczyć obiekty bez powtórnego czytania gpkg."""
    qgz_path = _resolve_qgz_path(cfg["qgis_project_path"])
    result = {}
    for key, spec in cfg.get("amenities", {}).items():
        cache_key = (str(qgz_path), spec["qgis_layer"])
        if cache_key not in _raw_layer_cache:
            _raw_layer_cache[cache_key] = _read_qgis_layer(qgz_path, spec["qgis_layer"]).to_crs(AMENITY_CRS_METRIC)
        result[key] = _raw_layer_cache[cache_key]
    return result


def objects_within_radius(lat: float, lon: float, radius_m: float, cfg: dict) -> dict:
    """Zwraca {klucz_udogodnienia: [(lat, lon), ...]} - współrzędne (środki
    geometrii) obiektów każdej skonfigurowanej warstwy, które leżą w
    promieniu `radius_m` metrów od punktu. Do liczenia i do rysowania na mapie."""
    layers = _get_amenity_raw(cfg)
    point = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(AMENITY_CRS_METRIC).iloc[0]
    result = {}
    for key, gdf in layers.items():
        within = gdf[gdf.geometry.distance(point) <= radius_m]
        centroids = gpd.GeoSeries(within.geometry.centroid, crs=AMENITY_CRS_METRIC).to_crs("EPSG:4326")
        result[key] = [(pt.y, pt.x) for pt in centroids]
    return result


def count_within_radius(lat: float, lon: float, radius_m: float, cfg: dict) -> dict:
    """Liczy, ile obiektów każdej skonfigurowanej warstwy leży w promieniu
    `radius_m` metrów od punktu (lat, lon)."""
    return {key: len(pts) for key, pts in objects_within_radius(lat, lon, radius_m, cfg).items()}


# ============================================================
# Hałas drogowy - osobne źródło (GIS/Halas/DrogiALL.gpkg, warstwa
# "zlaczone", pole "Halas" 20-100 wg klasy drogi), nie zarejestrowane
# w PRJ_mieszkania.qgz, więc czytane bezpośrednio ze stałej ścieżki.
# Wskaźnik 0-100: suma Halas dróg w promieniu 300 m, z wagą liniowo
# malejącą z odległością, przepuszczona przez funkcję nasycającą - patrz
# GIS/Halas/noise_utils.py (ten sam wzór, żeby wyniki były spójne).
# ============================================================
NOISE_GPKG_PATH = Path(__file__).parent / "gis_data" / "DrogiALL.gpkg"
NOISE_LAYER = "zlaczone"
NOISE_RADIUS_M = 300
NOISE_K = 400.0

_noise_layer_cache = None


def _get_noise_layer() -> gpd.GeoDataFrame:
    global _noise_layer_cache
    if _noise_layer_cache is None:
        gdf = gpd.read_file(NOISE_GPKG_PATH, layer=NOISE_LAYER, columns=["Halas", "geometry"])
        _noise_layer_cache = gdf.to_crs(AMENITY_CRS_METRIC)
    return _noise_layer_cache


def noise_index_at_point(lat: float, lon: float) -> float:
    """Wskaźnik hałasu drogowego 0-100 w promieniu NOISE_RADIUS_M od punktu."""
    gdf = _get_noise_layer()
    point = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(AMENITY_CRS_METRIC).iloc[0]
    nearby = gdf[gdf.geometry.distance(point) <= NOISE_RADIUS_M].copy()
    if nearby.empty:
        return 0.0
    nearby["dystans"] = nearby.geometry.distance(point)
    waga = (1 - nearby["dystans"] / NOISE_RADIUS_M).clip(lower=0)
    suma = float((nearby["Halas"] * waga).sum())
    return round(100 * (1 - math.exp(-suma / NOISE_K)), 1)


def fetch_coordinates(url: str):
    """Współrzędne są dostępne tylko na stronie konkretnej oferty, nie w
    liście wyników wyszukiwania - stąd osobne zapytanie."""
    html = fetch(url).text
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', html)
    if not m:
        return None
    data = json.loads(m.group(1))
    coords = _find_key(data, "coordinates")
    if not coords or "latitude" not in coords:
        return None
    return coords["latitude"], coords["longitude"]


def evaluate_item(item: dict, cfg: dict) -> dict:
    description = item.get("shortDescription") or ""
    result = {
        "title": item.get("title", ""),
        "url": item_url(item),
        "price": (item.get("totalPrice") or {}).get("value"),
        "area": item.get("areaInSquareMeters"),
        "rooms": item.get("roomsNumber"),
        "city": ((item.get("location") or {}).get("address") or {}).get("city", {}).get("name"),
        "rooms_ok": meets_rooms_requirement(item, cfg),
        "area_ok": meets_area_requirement(item, cfg),
        "owner_ok": meets_owner_requirement(item, cfg),
        "required_mentions_ok": all(
            mentions_required(description, spec) for spec in cfg.get("required_mentions", [])
        ),
    }
    cheap_checks_ok = (
        result["rooms_ok"] and result["area_ok"] and result["owner_ok"] and result["required_mentions_ok"]
    )

    # sprawdzenie buforów wymaga dodatkowego zapytania o stronę oferty - robimy
    # to TYLKO gdy tańsze kryteria już przeszły. Współrzędne pobieramy raz i
    # używamy zarówno do "musi być blisko" (amenities), jak i "ma NIE być
    # blisko" (avoid) - to dwa niezależne, symetryczne zestawy warstw.
    result["amenities"] = {}
    result["avoid"] = {}
    result["noise_index"] = None
    coords = None
    need_coords = cheap_checks_ok and (
        cfg.get("require_near_amenities") or cfg.get("avoid") or cfg.get("max_noise_index") is not None
    )
    if need_coords:
        coords = fetch_coordinates(result["url"])

    if cheap_checks_ok and cfg.get("require_near_amenities"):
        if coords is None:
            result["amenity_ok"] = False
        else:
            amenities_cfg = cfg.get("amenities", {})
            result["amenities"] = meets_amenity_requirement(*coords, cfg)
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
            result["avoid"] = meets_amenity_requirement(*coords, avoid_probe_cfg)
            required_avoid_keys = [
                k for k, spec in avoid_cfg.items()
                if spec.get("required", True) and spec.get("buffer_m") is not None
            ]
            result["avoid_ok"] = not any(result["avoid"].get(k, False) for k in required_avoid_keys)
    else:
        result["avoid_ok"] = True

    if cheap_checks_ok and result["amenity_ok"] and result["avoid_ok"] and cfg.get("max_noise_index") is not None:
        if coords is None:
            result["noise_ok"] = False
        else:
            result["noise_index"] = noise_index_at_point(*coords)
            result["noise_ok"] = result["noise_index"] <= cfg["max_noise_index"]
    else:
        result["noise_ok"] = True

    result["passes"] = cheap_checks_ok and result["amenity_ok"] and result["avoid_ok"] and result["noise_ok"]
    return result


def search_now(cfg: dict) -> list:
    """Jedno, niezależne, bezstanowe wyszukiwanie: pobiera bieżące wyniki
    z Otodom i ocenia KAŻDĄ ofertę względem `cfg` - bez pamięci "co już
    widziałem". To jest budulec pod przyszłą stronę internetową, gdzie
    każde wyszukanie ma być osobnym zapytaniem."""
    url = build_search_url(cfg)
    html = fetch(url).text
    items = extract_items(html)
    return [evaluate_item(item, cfg) for item in items]
