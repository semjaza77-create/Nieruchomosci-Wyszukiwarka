#!/usr/bin/env python3
"""
Interfejs w przeglądarce dla jakdaleko.py - importuje jego funkcje (nie
dubluje ich), więc pozostaje w tej samej, samodzielnej rodzinie plików,
niezwiązanej z otodom_agent.py / otodom_core.py / otodom_search.py /
otodom_web.py.

Dwie sekcje:
1. Odległość do najbliższego - dla każdej warstwy pokazuje dystans (m).
2. Licznik obiektów - ile obiektów każdej warstwy mieści się w zadanym
   promieniu, plus mapa (Leaflet) z zaznaczonym promieniem i obiektami.

Całość jest wystawiona przez render() - dzięki temu ten plik działa
samodzielnie (streamlit run jakdaleko_web.py) ORAZ jako jedna z zakładek
w app.py (obok multi_web.py), bez duplikowania kodu.

URUCHOM samodzielnie: streamlit run jakdaleko_web.py
URUCHOM razem z wyszukiwarką: streamlit run app.py
"""

import json

import streamlit as st
import streamlit.components.v1 as components
import geopandas as gpd

import jakdaleko as jd

MAP_COLORS = {
    "apteka": "#1f77ff",
    "szpital": "#ff2d55",
    "przedszkole": "#ff9500",
    "park": "#34a853",
    "przystanek": "#8e44ad",
    "las": "#0b6623",
    "przemysl": "#5c5c5c",
    "zielenina": "#7fbf3f",
}


def build_mpzp_shapes(fragments: list) -> list:
    """Zamienia fragmenty z jd.mpzp_within_radius (geometrie w jd.METRIC_CRS)
    na listę {"rings": [[[lat, lon], ...], ...], "kategoria", "oznaczenie", "opis"}
    gotową do narysowania w Leaflet (WGS84)."""
    if not fragments:
        return []
    geoms = gpd.GeoSeries([f["geometry_local"] for f in fragments], crs=jd.METRIC_CRS).to_crs("EPSG:4326")
    shapes = []
    for f, geom in zip(fragments, geoms):
        polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        rings = [[[y, x] for x, y in poly.exterior.coords] for poly in polys if not poly.is_empty]
        if rings:
            shapes.append({
                "rings": rings,
                "kategoria": f["kategoria"],
                "oznaczenie": f["oznaczenie"] or "",
                "opis": f["opis_oznaczenia"] or "",
            })
    return shapes


def build_map_html(point: dict, objects: dict, radius_m: float, mpzp_shapes: list | None = None) -> str:
    markers_js = "\n      ".join(
        f"L.circleMarker([{lat}, {lon}], {{radius: 7, color: '{MAP_COLORS.get(label, '#888888')}', "
        f"fillColor: '{MAP_COLORS.get(label, '#888888')}', fillOpacity: 0.85, weight: 1}})"
        f".addTo(map).bindPopup('{label}');"
        for label, pts in objects.items()
        for lat, lon in pts
    )
    legend_rows = "".join(
        f"<div><span style='display:inline-block;width:10px;height:10px;"
        f"background:{MAP_COLORS.get(label, '#888888')};border-radius:50%;margin-right:6px;'></span>"
        f"{label} ({len(pts)})</div>"
        for label, pts in objects.items()
    )

    mpzp_shapes = mpzp_shapes or []
    mpzp_polygons_js = "\n      ".join(
        f"L.polygon({json.dumps(shape['rings'])}, {{color: '{jd.MPZP_CATEGORY_COLORS.get(shape['kategoria'], '#b0b0b0')}', "
        f"fillColor: '{jd.MPZP_CATEGORY_COLORS.get(shape['kategoria'], '#b0b0b0')}', fillOpacity: 0.35, weight: 1}})"
        f".addTo(map).bindPopup({json.dumps(shape['oznaczenie'] + ' — ' + shape['opis'])});"
        for shape in mpzp_shapes
    )
    mpzp_kategorie_obecne = sorted({s["kategoria"] for s in mpzp_shapes})
    mpzp_legend_rows = "".join(
        f"<div><span style='display:inline-block;width:10px;height:10px;"
        f"background:{jd.MPZP_CATEGORY_COLORS.get(kat, '#b0b0b0')};margin-right:6px;'></span>{kat}</div>"
        for kat in mpzp_kategorie_obecne
    )
    mpzp_legend_html = (
        f"<div class='legend-box' style='margin-top:6px;'><b>MPZP</b>{mpzp_legend_rows}</div>"
        if mpzp_kategorie_obecne else ""
    )

    return f"""
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <style>
      html, body {{ margin:0; padding:0; height:100%; font-family: sans-serif; }}
      #map {{ height:480px; width:100%; border-radius:8px; }}
      .leaflet-attribution-flag {{ display:none !important; }}
      .legend-box {{
        background:rgba(255,255,255,0.92); padding:8px 12px; border-radius:6px;
        font-size:12px; color:#111; box-shadow:0 1px 4px rgba(0,0,0,0.3); line-height:1.6;
      }}
    </style>
    <div id="map"></div>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
      var map = L.map('map').setView([{point['lat']}, {point['lon']}], 15);
      L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}.png', {{
          attribution: '&copy; OpenStreetMap contributors &copy; CARTO'
      }}).addTo(map);

      {mpzp_polygons_js}

      L.circle([{point['lat']}, {point['lon']}], {{
        radius: {radius_m}, color: '#ff3b30', fillColor: '#ff3b30', fillOpacity: 0.08, weight: 2
      }}).addTo(map);

      L.marker([{point['lat']}, {point['lon']}]).addTo(map).bindPopup('Adres').openPopup();

      {markers_js}

      var legend = L.control({{position: 'topright'}});
      legend.onAdd = function() {{
        var div = L.DomUtil.create('div', 'legend-box');
        div.innerHTML = `{legend_rows}`;
        return div;
      }};
      legend.addTo(map);

      var mpzpLegend = L.control({{position: 'topright'}});
      mpzpLegend.onAdd = function() {{
        var div = L.DomUtil.create('div');
        div.innerHTML = `{mpzp_legend_html}`;
        return div;
      }};
      mpzpLegend.addTo(map);

      setTimeout(function() {{ map.invalidateSize(); }}, 200);
    </script>
    """

def render_metrics_grid(items, formatter, cols_per_row: int = 4):
    """st.columns(len(items)) robi się nieczytelne przy >4-5 warstwach -
    liczby się ucinają. Zamiast jednego rzędu, dzieli na kilka rzędów po
    `cols_per_row` metryk, żeby każda miała wystarczająco miejsca."""
    items = list(items)
    for i in range(0, len(items), cols_per_row):
        chunk = items[i:i + cols_per_row]
        row_cols = st.columns(cols_per_row)
        for col, (label, value) in zip(row_cols, chunk):
            col.metric(label, formatter(value))


@st.cache_data(ttl=600, show_spinner=False)
def run_geocode(query: str):
    return jd.geocode_address(query)


def render():
    st.title("📍 jakdaleko")
    st.caption(
        "Wklej adres - program policzy dystans do najbliższej apteki/szpitala/przedszkola/parku/"
        "przystanku oraz (opcjonalnie) ile takich obiektów jest w zadanym promieniu. "
        "Dane z Twojego projektu QGIS (Kraków)."
    )

    if "jd_matches" not in st.session_state:
        st.session_state.jd_matches = []
    if "jd_point" not in st.session_state:
        st.session_state.jd_point = None

    col1, col2 = st.columns([4, 1])
    address = col1.text_input(
        "Adres", value="", label_visibility="collapsed",
        placeholder="np. Karmelicka 10, Kraków", key="jd_address_input",
    )
    search_clicked = col2.button("Szukaj", width="stretch", key="jd_search_btn")

    if search_clicked and address.strip():
        with st.spinner("Geokoduję adres..."):
            st.session_state.jd_matches = run_geocode(address.strip())
        st.session_state.jd_point = None
        if not st.session_state.jd_matches:
            st.warning("Nie znaleziono takiego adresu. Spróbuj dopisać miasto albo podaj współrzędne ręcznie.")

    if st.session_state.jd_matches:
        options = {m["display_name"]: m for m in st.session_state.jd_matches}
        chosen_label = st.selectbox("Wybierz dokładny adres", list(options.keys()), key="jd_addr_choice")
        st.session_state.jd_point = options[chosen_label]

    with st.expander("Albo podaj współrzędne ręcznie"):
        m1, m2, m3 = st.columns([1, 1, 1])
        manual_lat = m1.number_input("Szerokość (lat)", value=50.061947, format="%.6f", key="jd_manual_lat")
        manual_lon = m2.number_input("Długość (lon)", value=19.936856, format="%.6f", key="jd_manual_lon")
        m3.write("")
        if m3.button("Ustaw współrzędne", width="stretch", key="jd_manual_set"):
            st.session_state.jd_point = {
                "display_name": f"({manual_lat:.5f}, {manual_lon:.5f})",
                "lat": manual_lat, "lon": manual_lon,
            }

    point = st.session_state.jd_point

    if not point:
        st.info("Znajdź adres albo ustaw współrzędne ręcznie.")
    else:
        st.markdown(f"**📍 {point['display_name']}**")
        st.divider()

        st.subheader("Odległość do najbliższego / -ej")
        with st.spinner("Liczę odległości..."):
            distances = jd.distances_from_point(point["lat"], point["lon"])
        render_metrics_grid(
            sorted(distances.items(), key=lambda kv: kv[1]),
            formatter=lambda dist_m: f"{dist_m:.0f} m",
        )

        with st.spinner("Liczę hałas..."):
            noise_index = jd.noise_index_at_point(point["lat"], point["lon"])
        st.progress(noise_index / 100, text=f"🔊 Hałas drogowy: {noise_index:.0f}/100")

        if min(distances.values()) > 20000:
            st.warning(
                "Wszystkie odległości są bardzo duże (>20 km) - projekt QGIS zawiera dane tylko dla Krakowa, "
                "więc ten adres prawdopodobnie leży poza ich zasięgiem."
            )

        st.divider()
        st.subheader("🏗️ Przeznaczenie terenu (MPZP)")
        tutaj = jd.mpzp_at_point(point["lat"], point["lon"])
        if tutaj:
            st.markdown(
                f"**{tutaj['oznaczenie']}** — {tutaj['opis_oznaczenia']}  \n"
                f"Plan: *{tutaj['nazwa_mpzp']}* &nbsp;·&nbsp; [treść uchwały]({tutaj['www']})"
            )
        else:
            st.caption(
                "Brak obowiązującego MPZP dokładnie w tym miejscu. To częste w Krakowie - miasto nie ma "
                "planu na 100% powierzchni, część terenów jest zabudowywana na podstawie indywidualnych "
                "decyzji o warunkach zabudowy (WZ), a nie planu miejscowego. Sprawdź niżej, czy jakiś plan "
                "obowiązuje choć w pobliżu (w promieniu)."
            )

        st.divider()
        st.subheader("🤖 Analiza lokalizacji (Claude)")
        st.caption(
            "Kilka zdań pod kątem kulturalnym, rekreacyjnym, przyrodniczym, usługowym, biznesowym "
            "i planistycznym (MPZP) — napisane przez Claude na podstawie danych powyżej."
        )
        if st.button("Wygeneruj analizę", key="jd_analysis_btn"):
            with st.spinner("Claude analizuje lokalizację..."):
                mpzp_summary = jd.mpzp_summary_text(point["lat"], point["lon"], radius_m=300)
                st.session_state.jd_analysis_text = jd.generate_ai_analysis(
                    point["display_name"], distances, noise_index, mpzp_summary=mpzp_summary
                )
                st.session_state.jd_analysis_point = point

        if st.session_state.get("jd_analysis_text") and st.session_state.get("jd_analysis_point") == point:
            st.markdown(st.session_state.jd_analysis_text)

        st.divider()
        st.subheader("Licznik obiektów w promieniu")
        st.caption("Obejmuje też mapę z kolorami przeznaczenia terenu (MPZP) w tym samym promieniu.")
        radius_m = st.number_input("Promień (metry)", min_value=50, max_value=20000, value=500, step=50, key="jd_radius")
        if st.button("🔢 Policz", type="primary", key="jd_count_btn"):
            with st.spinner("Liczę obiekty i przeznaczenie terenu w promieniu..."):
                st.session_state.jd_objects = jd.objects_within_radius(point["lat"], point["lon"], radius_m)
                st.session_state.jd_mpzp = jd.mpzp_within_radius(point["lat"], point["lon"], radius_m)
                st.session_state.jd_objects_radius = radius_m
                st.session_state.jd_objects_point = point

        objects = st.session_state.get("jd_objects")
        if objects and st.session_state.get("jd_objects_point") == point:
            counts = {label: len(pts) for label, pts in objects.items()}
            render_metrics_grid(
                sorted(counts.items(), key=lambda kv: -kv[1]),
                formatter=lambda n: n,
            )

            mpzp_fragments = st.session_state.get("jd_mpzp", [])
            if mpzp_fragments:
                suma_kat: dict = {}
                for f in mpzp_fragments:
                    suma_kat[f["kategoria"]] = suma_kat.get(f["kategoria"], 0) + f["powierzchnia_m2"]
                total = sum(suma_kat.values())
                st.caption("Udział przeznaczenia terenu w tym promieniu:")
                for kat, pow_ in sorted(suma_kat.items(), key=lambda kv: -kv[1]):
                    st.progress(pow_ / total, text=f"{kat}: {100 * pow_ / total:.0f}%")

                st.caption("Plany / fragmenty przeznaczenia złapane w tym promieniu:")
                # kilka poligonów tego samego oznaczenia z tego samego planu (sąsiednie
                # działki) łączymy w jeden wiersz, sumując powierzchnię - inaczej lista
                # robi się nieczytelna przy dziesiątkach małych fragmentów.
                zebrane: dict = {}
                for f in mpzp_fragments:
                    klucz = (f["nazwa_mpzp"], f["oznaczenie"])
                    if klucz not in zebrane:
                        zebrane[klucz] = {
                            "Oznaczenie": f["oznaczenie"],
                            "Opis": f["opis_oznaczenia"],
                            "Plan": f["nazwa_mpzp"],
                            "Powierzchnia (m²)": 0.0,
                            "Link": f["www"],
                        }
                    zebrane[klucz]["Powierzchnia (m²)"] += f["powierzchnia_m2"]
                wiersze = sorted(zebrane.values(), key=lambda w: -w["Powierzchnia (m²)"])
                for w in wiersze:
                    w["Powierzchnia (m²)"] = round(w["Powierzchnia (m²)"])
                try:
                    # LinkColumn wymaga nowszego streamlit - jeśli nie ma, po prostu
                    # pokazujemy tabelę bez klikalnych linków (zwykły tekst URL).
                    st.dataframe(
                        wiersze,
                        hide_index=True,
                        column_config={"Link": st.column_config.LinkColumn("Link", display_text="uchwała")},
                    )
                except AttributeError:
                    st.dataframe(wiersze, hide_index=True)
            else:
                st.caption("Brak danych MPZP w tym promieniu.")

            st.subheader("Mapa")
            map_html = build_map_html(point, objects, st.session_state.jd_objects_radius)
            components.html(map_html, height=500)
            st.caption("Podkład mapy: CARTO / OpenStreetMap.")


if __name__ == "__main__":
    st.set_page_config(page_title="jakdaleko", page_icon="📍", layout="centered")
    render()
