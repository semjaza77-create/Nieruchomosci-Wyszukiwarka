#!/usr/bin/env python3
"""
Wyszukiwarka mieszkań (wersja wieloportalowa - Otodom + nieruchomosci-online.pl
+ Morizon.pl, patrz multi_core.py) - formularz kryteriów + wyniki na żywo z
3 portali naraz, filtrowane wg odległości od udogodnień z projektu QGIS.
Każde kliknięcie "Szukaj" to niezależne, bezstanowe wyszukiwanie
(multi_core.search_all).

Całość jest wystawiona przez render() - dzięki temu ten plik działa
samodzielnie (streamlit run multi_web.py) ORAZ jako jedna z zakładek w
MultiAgent.py (obok jakdaleko_web.py), bez duplikowania kodu.

URUCHOM samodzielnie: streamlit run multi_web.py
URUCHOM razem z jakdaleko: streamlit run MultiAgent.py
"""

import json
from pathlib import Path

import streamlit as st

import otodom_core as core
import multi_core

SOURCE_LABELS = {
    "otodom": "Otodom",
    "nieruchomosci-online": "nieruchomosci-online.pl",
    "morizon": "Morizon.pl",
}

CONFIG_FILE = Path(__file__).parent / "search_config.json"
DEFAULT_CFG = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

CITY_SLUGS = {
    "Kraków": "malopolskie/krakow/krakow/krakow",
    "Warszawa": "mazowieckie/warszawa/warszawa/warszawa",
    "Wrocław": "dolnoslaskie/wroclaw/wroclaw/wroclaw",
    "Poznań": "wielkopolskie/poznan/poznan/poznan",
    "Gdańsk": "pomorskie/gdansk/gdansk/gdansk",
}

ROOMS_LABELS = {
    "ONE": "1 pokój", "TWO": "2 pokoje", "THREE": "3 pokoje", "FOUR": "4 pokoje",
    "FIVE": "5 pokoi", "SIX": "6 pokoi", "SEVEN": "7 pokoi", "EIGHT": "8 pokoi",
    "NINE": "9 pokoi", "TEN": "10 pokoi",
}


@st.cache_data(ttl=180, show_spinner=False)
def run_search(cfg_json: str):
    return multi_core.search_all(json.loads(cfg_json))


def render():
    st.title("🏠 Wyszukiwarka mieszkań — Otodom + nieruchomosci-online + Morizon + QGIS")
    st.caption(
        "Prototyp: każde kliknięcie „Szukaj” to osobne, niezależne wyszukiwanie "
        "(bez pamięci), łączone naraz z 3 portali — filtruje też wg odległości od "
        "udogodnień z Twojego projektu QGIS."
    )

    with st.sidebar:
        st.header("Kryteria")

        transaction = st.radio(
            "Typ transakcji", ["wynajem", "sprzedaz"],
            index=["wynajem", "sprzedaz"].index(DEFAULT_CFG.get("transaction", "wynajem")),
            horizontal=True,
        )
        city_names = list(CITY_SLUGS.keys())
        default_city = DEFAULT_CFG.get("city_display", "Kraków")
        city_name = st.selectbox("Miasto", city_names, index=city_names.index(default_city) if default_city in city_names else 0)

        c1, c2 = st.columns(2)
        price_min = c1.number_input("Cena min (PLN)", min_value=0, value=int(DEFAULT_CFG.get("price_min") or 0), step=100)
        price_max = c2.number_input("Cena maks (PLN)", min_value=0, value=int(DEFAULT_CFG.get("price_max") or 5000), step=100)

        rooms_options = ["brak"] + core.ROOMS_ORDER
        rooms_min = st.selectbox("Min. liczba pokoi", rooms_options, index=rooms_options.index(DEFAULT_CFG.get("rooms_min") or "brak"))
        rooms_max = st.selectbox("Maks. liczba pokoi", rooms_options, index=rooms_options.index(DEFAULT_CFG.get("rooms_max") or "brak"))

        area_min = st.number_input("Min. metraż (m²)", min_value=0, value=int(DEFAULT_CFG.get("area_min_m2") or 0), step=5)
        private_owner_only = st.checkbox("Tylko prywatni właściciele", value=DEFAULT_CFG.get("private_owner_only", False))
        mentions_raw = st.text_input("Wymagane słowa w opisie (oddziel przecinkami)", value="")

        st.divider()
        st.subheader("Udogodnienia (bufory z QGIS)")
        st.caption("Odznacz warstwę, żeby w ogóle jej nie sprawdzać. Odznacz „wymagane”, żeby tylko pokazywać dystans bez blokowania oferty.")

        amenities_cfg = {}
        for key, spec in DEFAULT_CFG.get("amenities", {}).items():
            enabled = st.checkbox(key, value=spec.get("buffer_m") is not None, key=f"en_{key}")
            if enabled:
                ac1, ac2 = st.columns([2, 1])
                buffer_m = ac1.number_input(
                    "promień (m)", min_value=50, max_value=5000,
                    value=int(spec.get("buffer_m") or 300), step=50,
                    key=f"buf_{key}", label_visibility="collapsed",
                )
                required = ac2.checkbox("wymagane", value=spec.get("required", True), key=f"req_{key}")
                amenities_cfg[key] = {"qgis_layer": spec["qgis_layer"], "buffer_m": buffer_m, "required": required}

        st.divider()
        st.subheader("🚫 Unikaj w pobliżu")
        st.caption("Odwrotność powyższego — zaznacz warstwę, żeby WYKLUCZYĆ oferty leżące w podanym promieniu od niej.")

        avoid_cfg = {}
        for key, spec in DEFAULT_CFG.get("amenities", {}).items():
            avoid_enabled = st.checkbox(f"unikaj: {key}", value=False, key=f"avoid_en_{key}")
            if avoid_enabled:
                av1, av2 = st.columns([2, 1])
                avoid_buffer_m = av1.number_input(
                    "promień (m)", min_value=50, max_value=5000,
                    value=300, step=50, key=f"avoid_buf_{key}", label_visibility="collapsed",
                )
                avoid_required = av2.checkbox("wymagane", value=True, key=f"avoid_req_{key}")
                avoid_cfg[key] = {"qgis_layer": spec["qgis_layer"], "buffer_m": avoid_buffer_m, "required": avoid_required}

        st.divider()
        st.caption(
            "ℹ️ Wskaźnik hałasu drogowego nie jest tu dostępny — to funkcja "
            "specyficzna dla wersji jednoportalowej (otodom_web.py)."
        )

        search_clicked = st.button("🔎 Szukaj", type="primary", width="stretch", key="multi_search_btn")

    cfg = {
        "transaction": transaction,
        "city_slug": CITY_SLUGS[city_name],
        "city_display": city_name,
        "price_min": price_min or None,
        "price_max": price_max or None,
        "rooms_min": None if rooms_min == "brak" else rooms_min,
        "rooms_max": None if rooms_max == "brak" else rooms_max,
        "area_min_m2": area_min or None,
        "private_owner_only": private_owner_only,
        "required_mentions": [
            {"label": w.strip(), "any_of": [w.strip()], "negations": []}
            for w in mentions_raw.split(",") if w.strip()
        ],
        "max_results_per_search": DEFAULT_CFG.get("max_results_per_search", 72),
        "require_near_amenities": bool(amenities_cfg),
        "qgis_project_path": DEFAULT_CFG["qgis_project_path"],
        "amenities": amenities_cfg,
        "avoid": avoid_cfg,
    }

    if search_clicked:
        with st.spinner("Szukam na Otodom, nieruchomosci-online.pl i Morizon.pl, i sprawdzam odległości od udogodnień..."):
            st.session_state["multi_results"] = run_search(json.dumps(cfg, sort_keys=True))

    results = st.session_state.get("multi_results")

    if results is None:
        st.info("Ustaw kryteria w panelu bocznym i kliknij **Szukaj**.")
    else:
        matches = [r for r in results if r["passes"]]
        st.subheader(f"Wyniki: {len(matches)} z {len(results)} ofert pasuje")
        show_all = st.checkbox("Pokaż też niepasujące oferty", value=False, key="multi_show_all")
        to_show = results if show_all else matches

        if not to_show:
            st.warning("Brak ofert spełniających kryteria. Spróbuj poluzować filtry.")

        for r in to_show:
            with st.container(border=True):
                cols = st.columns([0.9, 3.1, 1, 1, 1.5])
                cols[0].caption(SOURCE_LABELS.get(r.get("source"), r.get("source", "—")))
                cols[1].markdown(f"**[{r['title']}]({r['url']})**")
                cols[2].write(f"{r['price']:.0f} PLN" if r["price"] else "—")
                cols[3].write(f"{r['area']:.0f} m²" if r["area"] else "—")
                cols[4].write(ROOMS_LABELS.get(r["rooms"], r["rooms"] or "—"))

                if r.get("amenities"):
                    in_range = [k for k, ok in r["amenities"].items() if ok]
                    out_range = [k for k, ok in r["amenities"].items() if not ok]
                    if in_range:
                        st.caption("✅ W zasięgu: " + ", ".join(in_range))
                    if out_range:
                        st.caption("❌ Poza zasięgiem: " + ", ".join(out_range))

                if r.get("avoid"):
                    too_close = [k for k, near in r["avoid"].items() if near]
                    far_enough = [k for k, near in r["avoid"].items() if not near]
                    if far_enough:
                        st.caption("🚫✅ Z dala od: " + ", ".join(far_enough))
                    if too_close:
                        st.caption("🚫❌ Zbyt blisko: " + ", ".join(too_close))

                if not r["passes"]:
                    reasons = []
                    if not r["rooms_ok"]:
                        reasons.append("liczba pokoi")
                    if not r["area_ok"]:
                        reasons.append("metraż")
                    if not r.get("price_ok", True):
                        reasons.append("cena")
                    if not r["owner_ok"]:
                        reasons.append("właściciel")
                    if not r["required_mentions_ok"]:
                        reasons.append("słowa w opisie")
                    if not r["amenity_ok"]:
                        reasons.append("udogodnienia")
                    if not r["avoid_ok"]:
                        reasons.append("zbyt blisko unikanych miejsc")
                    if reasons:
                        st.caption("⚠️ Nie spełnia: " + ", ".join(reasons))


if __name__ == "__main__":
    st.set_page_config(page_title="Szukaj mieszkań (multi-portal)", page_icon="🏠", layout="wide")
    render()
