#!/usr/bin/env python3
"""
jakdaleko.py - samodzielny program, niezwiązany z otodom_agent.py /
otodom_core.py / otodom_search.py / otodom_web.py (nic z nich nie
importuje, niczego w nich nie zmienia).

Podajesz adres, a program go geokoduje i liczy dystans (w metrach) od tego
adresu do najbliższej apteki, szpitala, przedszkola, parku i przystanku -
na podstawie warstw z Twojego projektu QGIS (PRJ_mieszkania.qgz). Umie też
policzyć, ile obiektów każdej warstwy leży w zadanym promieniu
(counts_within_radius).

URUCHOM (terminal):
    python3 jakdaleko.py "Karmelicka 10, Kraków"
albo bez argumentu - zapyta o adres interaktywnie:
    python3 jakdaleko.py

Wersja z interfejsem w przeglądarce (importuje funkcje z tego pliku):
    streamlit run jakdaleko_web.py
"""

import sys
import math
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs

import requests
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from dotenv import load_dotenv
import anthropic

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

QGIS_PROJECT_PATH = Path(__file__).parent / "gis_data" / "PRJ_mieszkania.qgz"
METRIC_CRS = "EPSG:2180"  # układ metryczny (Polska) - do liczenia dystansu w metrach

# etykieta do wyświetlenia -> nazwa warstwy w projekcie QGIS
AMENITY_LAYERS = {
    "apteka": "amenity_pharmacy_krakow",
    "szpital": "SZPITALE",
    "przedszkole": "amenity_kindergarten_krakow",
    "park": "leisure_park_krakow",
    "przystanek": "stops",
    "las": "landuse_forest_krakow",
    "przemysl": "landuse_industrial_krakow",
    "zielenina": "natural_wood_krakow",
}

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Hałas drogowy - osobne źródło (nie jest warstwą w PRJ_mieszkania.qgz), patrz
# GIS/Halas/noise_utils.py po ten sam wzór (waga liniowo malejąca z odległością,
# nasycający wskaźnik 0-100).
NOISE_GPKG_PATH = Path(__file__).parent / "gis_data" / "DrogiALL.gpkg"
NOISE_LAYER = "zlaczone"
NOISE_RADIUS_M = 300
NOISE_K = 400.0

_noise_layer_cache = None


def _get_noise_layer() -> gpd.GeoDataFrame:
    global _noise_layer_cache
    if _noise_layer_cache is None:
        gdf = gpd.read_file(NOISE_GPKG_PATH, layer=NOISE_LAYER, columns=["Halas", "geometry"])
        _noise_layer_cache = gdf.to_crs(METRIC_CRS)
    return _noise_layer_cache


def noise_index_at_point(lat: float, lon: float) -> float:
    """Wskaźnik hałasu drogowego 0-100 w promieniu NOISE_RADIUS_M od punktu."""
    gdf = _get_noise_layer()
    point = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(METRIC_CRS).iloc[0]
    nearby = gdf[gdf.geometry.distance(point) <= NOISE_RADIUS_M].copy()
    if nearby.empty:
        return 0.0
    nearby["dystans"] = nearby.geometry.distance(point)
    waga = (1 - nearby["dystans"] / NOISE_RADIUS_M).clip(lower=0)
    suma = float((nearby["Halas"] * waga).sum())
    return round(100 * (1 - math.exp(-suma / NOISE_K)), 1)


# ============================================================
# Przeznaczenie terenu (MPZP) - lokalna kopia (GeoPackage) warstwy WFS
# Krakowa "BP_MPZP_POBIERANIE:Przeznaczenia_MPZP" (Miejski System Informacji
# Przestrzennej). To zrzut na dany dzień, nie żywe połączenie - jeśli miasto
# zaktualizuje plan, trzeba wyeksportować Przeznaczenie.gpkg ponownie z QGIS
# (Warstwa WFS -> prawy klik -> Eksportuj -> Zapisz obiekty jako... -> GeoPackage).
# ============================================================
MPZP_GPKG_PATH = Path(__file__).parent / "Przeznaczenie.gpkg"

# Bardzo uproszczone, orientacyjne grupowanie surowych kodów MPZP (np. "MN 4.1",
# "KD/D.2") na czytelne kategorie - do tekstu i do koloru na mapie, NIE jest to
# interpretacja prawna (różne plany z różnych lat używają nieco innych oznaczeń).
MPZP_CATEGORIES = {
    "mieszkaniowe": ("MN", "MW", "MU", "M/"),
    "usługowe": ("U",),
    "zieleń": ("Z",),
    "komunikacja / drogi": ("K",),
    "woda": ("W",),
    "rolnicze": ("R",),
    "infrastruktura techniczna": ("E",),
}
MPZP_CATEGORY_COLORS = {
    "mieszkaniowe": "#f4a940",
    "usługowe": "#e0483e",
    "zieleń": "#3fa34d",
    "komunikacja / drogi": "#7d7d7d",
    "woda": "#3f8ce0",
    "rolnicze": "#a97c50",
    "infrastruktura techniczna": "#8e44ad",
    "inne": "#b0b0b0",
}


def classify_mpzp(rodzaj_oznaczenia: str) -> str:
    """Przybliżone grupowanie surowego kodu MPZP na jedną z MPZP_CATEGORIES
    (albo "inne", jeśli nie rozpoznany). Patrz zastrzeżenie w komentarzu wyżej."""
    kod = (rodzaj_oznaczenia or "").upper()
    for kategoria, przedrostki in MPZP_CATEGORIES.items():
        if any(kod.startswith(p) for p in przedrostki):
            return kategoria
    return "inne"


_mpzp_layer_cache = None


def _get_mpzp_layer() -> gpd.GeoDataFrame:
    global _mpzp_layer_cache
    if _mpzp_layer_cache is None:
        gdf = gpd.read_file(MPZP_GPKG_PATH)
        _mpzp_layer_cache = gdf.to_crs(METRIC_CRS)
    return _mpzp_layer_cache


def mpzp_at_point(lat: float, lon: float) -> dict | None:
    """Przeznaczenie terenu dokładnie w tym punkcie (nazwa planu, oznaczenie,
    opis, link do uchwały), albo None jeśli nie ma tam obowiązującego MPZP
    (może być tylko studium, którego ta warstwa nie obejmuje)."""
    gdf = _get_mpzp_layer()
    point = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(METRIC_CRS).iloc[0]
    trafienia = gdf[gdf.geometry.contains(point)]
    if trafienia.empty:
        return None
    row = trafienia.iloc[0]
    return {
        "nazwa_mpzp": row.get("nazwa_mpzp"),
        "oznaczenie": row.get("oznaczenie"),
        "kategoria": classify_mpzp(row.get("rodzaj_oznaczenia")),
        "opis_oznaczenia": row.get("opis_oznaczenia"),
        "www": row.get("www"),
    }


def mpzp_within_radius(lat: float, lon: float, radius_m: float) -> list:
    """Wszystkie fragmenty MPZP w promieniu `radius_m` m od punktu, każdy z
    polem "geometry_local" (geometria w METRIC_CRS, przycięta do okręgu - do
    rysowania na mapie) i "powierzchnia_m2" (ile m2 tego fragmentu faktycznie
    mieści się w promieniu - do oceny co dominuje w okolicy)."""
    gdf = _get_mpzp_layer()
    point = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(METRIC_CRS).iloc[0]
    okrag = point.buffer(radius_m)
    trafienia = gdf[gdf.geometry.intersects(okrag)].copy()

    wyniki = []
    for _, row in trafienia.iterrows():
        czesc_wspolna = row.geometry.intersection(okrag)
        wyniki.append({
            "nazwa_mpzp": row.get("nazwa_mpzp"),
            "oznaczenie": row.get("oznaczenie"),
            "kategoria": classify_mpzp(row.get("rodzaj_oznaczenia")),
            "opis_oznaczenia": row.get("opis_oznaczenia"),
            "www": row.get("www"),
            "geometry_local": czesc_wspolna,
            "powierzchnia_m2": round(czesc_wspolna.area, 1),
        })
    wyniki.sort(key=lambda w: w["powierzchnia_m2"], reverse=True)
    return wyniki


def mpzp_summary_text(lat: float, lon: float, radius_m: float = 300) -> str:
    """Krótkie podsumowanie tekstowe - udział % każdej kategorii MPZP w
    promieniu `radius_m` m. Do promptu dla Claude albo do wyświetlenia wprost."""
    fragmenty = mpzp_within_radius(lat, lon, radius_m)
    if not fragmenty:
        return "Brak danych o MPZP w tej okolicy (poza zasięgiem danych albo brak planu)."
    suma_kategorii: dict = {}
    for f in fragmenty:
        suma_kategorii[f["kategoria"]] = suma_kategorii.get(f["kategoria"], 0) + f["powierzchnia_m2"]
    total = sum(suma_kategorii.values())
    wiersze = sorted(suma_kategorii.items(), key=lambda kv: -kv[1])
    return "\n".join(f"- {kat}: ~{100 * pow_ / total:.0f}%" for kat, pow_ in wiersze)


# ============================================================
# Analiza AI (Claude) - krótki opisowy komentarz do lokalizacji na
# podstawie już policzonych danych GIS (dystanse + hałas). Osobna warstwa
# nad danymi liczbowymi - nie zastępuje ich, tylko je komentuje.
# ============================================================
ANALYSIS_SYSTEM_PROMPT = (
    "Jesteś asystentem oceniającym lokalizacje mieszkań w Krakowie na podstawie danych GIS. "
    "Dostajesz adres oraz zmierzone dane (odległości w metrach do najbliższej apteki, szpitala, "
    "przedszkola, parku, przystanku, lasu, terenu przemysłowego i zieleni, plus wskaźnik hałasu "
    "drogowego 0-100). Napisz krótką analizę tej lokalizacji, po jednym-dwóch zdaniach na każdy "
    "z pięciu aspektów: kulturalny, rekreacyjny, przyrodniczy, usługowy, biznesowy. Dla aspektu "
    "usługowego, rekreacyjnego i przyrodniczego opieraj się przede wszystkim na podanych danych "
    "liczbowych. Dla aspektu kulturalnego i biznesowego nie masz twardych danych z GIS - skorzystaj "
    "z ogólnej wiedzy o konkretnej dzielnicy Krakowa widocznej w adresie, ale zaznacz to wprost jako "
    "ocenę orientacyjną, a nie pomiar. "
    "Jeśli dostaniesz też dane o przeznaczeniu terenu (MPZP) w okolicy, dodaj szósty aspekt: "
    "planistyczny - co przeważa w promieniu (mieszkaniowe/usługowe/zieleń/przemysłowe/rolnicze itd.) "
    "i co to sugeruje dla przyszłego charakteru okolicy (np. dużo terenów rolniczych/przemysłowych "
    "w pobliżu osiedla mieszkaniowego może zapowiadać przyszłą zabudowę). Jeśli nie dostaniesz takich "
    "danych, pomiń ten aspekt (nie zgaduj przeznaczenia terenu z niczego). "
    "Pisz zwięźle i rzeczowo, bez lania wody, po polsku."
)

_anthropic_client = None


def _get_anthropic_client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic()
    return _anthropic_client


def generate_ai_analysis(
    address_label: str, distances: dict, noise_index: float, mpzp_summary: str | None = None
) -> str:
    """Prosi Claude o krótką, opisową analizę lokalizacji pod kątami:
    kulturalnym/rekreacyjnym/przyrodniczym/usługowym/biznesowym, a jeśli podano
    mpzp_summary (patrz mpzp_summary_text) - też planistycznym, na podstawie
    już policzonych danych GIS + wskaźnika hałasu."""
    client = _get_anthropic_client()
    dane_txt = "\n".join(
        f"- {label}: {dist:.0f} m" for label, dist in sorted(distances.items(), key=lambda kv: kv[1])
    )
    aspekty = "kulturalnym, rekreacyjnym, przyrodniczym, usługowym i biznesowym"
    mpzp_blok = ""
    if mpzp_summary:
        mpzp_blok = f"\n\nPrzeznaczenie terenu (MPZP) w okolicy:\n{mpzp_summary}"
        aspekty += " oraz planistycznym"
    user_msg = (
        f"Adres: {address_label}\n\n"
        f"Odległości do najbliższych obiektów:\n{dane_txt}\n\n"
        f"Wskaźnik hałasu drogowego: {noise_index:.0f}/100"
        f"{mpzp_blok}\n\n"
        f"Napisz analizę tej lokalizacji pod kątem: {aspekty}."
    )
    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=2048,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        system=ANALYSIS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


def geocode_address(query: str) -> list:
    """Geokoduje adres przez Nominatim (OpenStreetMap) - zwraca listę
    kandydatów {display_name, lat, lon}, bo adres bywa niejednoznaczny."""
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": query, "format": "jsonv2", "addressdetails": 1, "limit": 5, "accept-language": "pl"},
        headers={"User-Agent": "jakdaleko-personal/1.0"},
        timeout=10,
    )
    resp.raise_for_status()
    return [
        {"display_name": item.get("display_name", ""), "lat": float(item["lat"]), "lon": float(item["lon"])}
        for item in resp.json()
    ]


def _load_qgis_layer_sources(qgz_path: Path) -> dict:
    """Zwraca {nazwa_warstwy_w_projekcie: (provider, datasource)} z pliku .qgz."""
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
    return sources


def _read_qgis_layer(qgz_path: Path, layer_display_name: str) -> gpd.GeoDataFrame:
    """Wczytuje warstwę bezpośrednio ze źródła zapisanego w projekcie QGIS -
    obsługuje warstwy OGR (gpkg) oraz warstwę tekstową CSV/GTFS (przystanki)."""
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


def distances_from_point(lat: float, lon: float) -> dict:
    """Liczy dystans (w metrach) od punktu do najbliższej geometrii każdej
    warstwy z AMENITY_LAYERS. Dla warstw powierzchniowych (park, przedszkole)
    dystans to 0, jeśli punkt leży w ich wnętrzu."""
    point = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(METRIC_CRS).iloc[0]
    result = {}
    for label, layer_name in AMENITY_LAYERS.items():
        gdf = _read_qgis_layer(QGIS_PROJECT_PATH, layer_name).to_crs(METRIC_CRS)
        result[label] = float(gdf.geometry.distance(point).min())
    return result


def objects_within_radius(lat: float, lon: float, radius_m: float) -> dict:
    """Zwraca {etykieta: [(lat, lon), ...]} - współrzędne (środki geometrii)
    obiektów każdej warstwy, które leżą w promieniu `radius_m` metrów od
    punktu. Do liczenia i do rysowania na mapie."""
    point = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(METRIC_CRS).iloc[0]
    result = {}
    for label, layer_name in AMENITY_LAYERS.items():
        gdf = _read_qgis_layer(QGIS_PROJECT_PATH, layer_name).to_crs(METRIC_CRS)
        within = gdf[gdf.geometry.distance(point) <= radius_m]
        centroids = gpd.GeoSeries(within.geometry.centroid, crs=METRIC_CRS).to_crs("EPSG:4326")
        result[label] = [(pt.y, pt.x) for pt in centroids]
    return result


def counts_within_radius(lat: float, lon: float, radius_m: float) -> dict:
    """Liczy, ile obiektów każdej warstwy z AMENITY_LAYERS leży w promieniu
    `radius_m` metrów od punktu."""
    return {label: len(pts) for label, pts in objects_within_radius(lat, lon, radius_m).items()}


def resolve_point(address: str):
    candidates = geocode_address(address)
    if not candidates:
        print(f"Nie znaleziono adresu: {address}")
        return None

    if len(candidates) == 1:
        chosen = candidates[0]
    else:
        print(f"\nZnaleziono {len(candidates)} pasujących adresów:")
        for i, c in enumerate(candidates, 1):
            print(f"  {i}. {c['display_name']}")
        try:
            choice = input(f"Wybierz numer [1-{len(candidates)}] (Enter = 1): ").strip()
        except EOFError:
            choice = ""
        idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(candidates) else 0
        chosen = candidates[idx]

    print(f"\n📍 {chosen['display_name']}  ({chosen['lat']:.5f}, {chosen['lon']:.5f})")
    return chosen


def main() -> int:
    address = " ".join(sys.argv[1:]).strip()
    if not address:
        try:
            address = input("Podaj adres: ").strip()
        except EOFError:
            address = ""
    if not address:
        print("Nie podano adresu.", file=sys.stderr)
        return 1

    point = resolve_point(address)
    if point is None:
        return 1

    distances = distances_from_point(point["lat"], point["lon"])

    print("\nOdległość do najbliższego/-ej:")
    for label, dist_m in sorted(distances.items(), key=lambda kv: kv[1]):
        print(f"  {label:12s} {dist_m:7.0f} m")

    tutaj = mpzp_at_point(point["lat"], point["lon"])
    print("\nPrzeznaczenie terenu w tym miejscu (MPZP):")
    if tutaj:
        print(f"  {tutaj['oznaczenie']} - {tutaj['opis_oznaczenia']} (plan: {tutaj['nazwa_mpzp']})")
        print(f"  {tutaj['www']}")
    else:
        print("  Brak obowiązującego MPZP w tym miejscu.")

    print("\nPrzeznaczenie terenu w promieniu 300 m:")
    print(mpzp_summary_text(point["lat"], point["lon"], radius_m=300))

    return 0


if __name__ == "__main__":
    sys.exit(main())
