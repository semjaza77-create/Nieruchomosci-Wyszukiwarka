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
    "pl": {
        "ONE": "1 pokój", "TWO": "2 pokoje", "THREE": "3 pokoje", "FOUR": "4 pokoje",
        "FIVE": "5 pokoi", "SIX": "6 pokoi", "SEVEN": "7 pokoi", "EIGHT": "8 pokoi",
        "NINE": "9 pokoi", "TEN": "10 pokoi",
    },
    "en": {
        "ONE": "1 room", "TWO": "2 rooms", "THREE": "3 rooms", "FOUR": "4 rooms",
        "FIVE": "5 rooms", "SIX": "6 rooms", "SEVEN": "7 rooms", "EIGHT": "8 rooms",
        "NINE": "9 rooms", "TEN": "10 rooms",
    },
}

TRANSACTION_LABELS = {
    "pl": {"wynajem": "wynajem", "sprzedaz": "sprzedaż"},
    "en": {"wynajem": "rent", "sprzedaz": "sale"},
}

NONE_LABELS = {"pl": "brak", "en": "any"}

TXT = {
    "pl": {
        "title": "🏠 Wyszukiwarka mieszkań — Otodom + nieruchomosci-online + Morizon + QGIS",
        "intro": (
            "Prototyp: każde kliknięcie „Szukaj” to osobne, niezależne wyszukiwanie "
            "(bez pamięci), łączone naraz z 3 portali — filtruje też wg odległości od "
            "udogodnień z Twojego projektu QGIS."
        ),
        "criteria": "Kryteria",
        "transaction_type": "Typ transakcji",
        "city": "Miasto",
        "price_min": "Cena min (PLN)",
        "price_max": "Cena maks (PLN)",
        "rooms_min": "Min. liczba pokoi",
        "rooms_max": "Maks. liczba pokoi",
        "area_min": "Min. metraż (m²)",
        "private_owner_only": "Tylko prywatni właściciele",
        "mentions": "Wymagane słowa w opisie (oddziel przecinkami)",
        "amenities_header": "Udogodnienia (bufory z QGIS)",
        "amenities_caption": "Odznacz warstwę, żeby w ogóle jej nie sprawdzać. Odznacz „wymagane”, żeby tylko pokazywać dystans bez blokowania oferty.",
        "radius": "promień (m)",
        "required": "wymagane",
        "avoid_header": "🚫 Unikaj w pobliżu",
        "avoid_caption": "Odwrotność powyższego — zaznacz warstwę, żeby WYKLUCZYĆ oferty leżące w podanym promieniu od niej.",
        "avoid_prefix": "unikaj: ",
        "noise_caption": (
            "ℹ️ Wskaźnik hałasu drogowego nie jest tu dostępny — to funkcja "
            "specyficzna dla wersji jednoportalowej (otodom_web.py)."
        ),
        "search_button": "🔎 Szukaj",
        "spinner": "Szukam na Otodom, nieruchomosci-online.pl i Morizon.pl, i sprawdzam odległości od udogodnień...",
        "set_criteria": "Ustaw kryteria w panelu bocznym i kliknij **Szukaj**.",
        "results_header": "Wyniki: {n} z {m} ofert pasuje",
        "show_all": "Pokaż też niepasujące oferty",
        "no_matches": "Brak ofert spełniających kryteria. Spróbuj poluzować filtry.",
        "in_range": "✅ W zasięgu: ",
        "out_range": "❌ Poza zasięgiem: ",
        "far_enough": "🚫✅ Z dala od: ",
        "too_close": "🚫❌ Zbyt blisko: ",
        "fails": "⚠️ Nie spełnia: ",
        "reason_rooms": "liczba pokoi",
        "reason_area": "metraż",
        "reason_price": "cena",
        "reason_owner": "właściciel",
        "reason_mentions": "słowa w opisie",
        "reason_amenity": "udogodnienia",
        "reason_avoid": "zbyt blisko unikanych miejsc",
    },
    "en": {
        "title": "🏠 Apartment search — Otodom + nieruchomosci-online + Morizon + QGIS",
        "intro": (
            "Prototype: every click on “Search” is a separate, stateless search "
            "combining all 3 portals at once — also filters by distance from "
            "amenities defined in your QGIS project."
        ),
        "criteria": "Criteria",
        "transaction_type": "Transaction type",
        "city": "City",
        "price_min": "Min price (PLN)",
        "price_max": "Max price (PLN)",
        "rooms_min": "Min. number of rooms",
        "rooms_max": "Max. number of rooms",
        "area_min": "Min. area (m²)",
        "private_owner_only": "Private owners only",
        "mentions": "Required words in description (comma-separated)",
        "amenities_header": "Amenities (QGIS buffers)",
        "amenities_caption": "Uncheck a layer to skip it entirely. Uncheck “required” to only show the distance without filtering out the listing.",
        "radius": "radius (m)",
        "required": "required",
        "avoid_header": "🚫 Avoid nearby",
        "avoid_caption": "The opposite of above — check a layer to EXCLUDE listings within the given radius of it.",
        "avoid_prefix": "avoid: ",
        "noise_caption": (
            "ℹ️ Road noise indicator isn't available here — it's a feature "
            "specific to the single-portal version (otodom_web.py)."
        ),
        "search_button": "🔎 Search",
        "spinner": "Searching Otodom, nieruchomosci-online.pl and Morizon.pl, and checking distances to amenities...",
        "set_criteria": "Set your criteria in the sidebar and click **Search**.",
        "results_header": "Results: {n} of {m} listings match",
        "show_all": "Also show non-matching listings",
        "no_matches": "No listings match your criteria. Try loosening the filters.",
        "in_range": "✅ In range: ",
        "out_range": "❌ Out of range: ",
        "far_enough": "🚫✅ Far enough from: ",
        "too_close": "🚫❌ Too close to: ",
        "fails": "⚠️ Doesn't meet: ",
        "reason_rooms": "number of rooms",
        "reason_area": "area",
        "reason_price": "price",
        "reason_owner": "owner type",
        "reason_mentions": "words in description",
        "reason_amenity": "amenities",
        "reason_avoid": "too close to avoided places",
    },
}


@st.cache_data(ttl=180, show_spinner=False)
def run_search(cfg_json: str):
    return multi_core.search_all(json.loads(cfg_json))


def render():
    with st.sidebar:
        lang = st.selectbox("Język / Language", ["pl", "en"], format_func=lambda c: {"pl": "🇵🇱 Polski", "en": "🇬🇧 English"}[c], key="multi_lang")
    t = TXT[lang]

    st.title(t["title"])
    st.caption(t["intro"])

    with st.sidebar:
        st.header(t["criteria"])

        transaction = st.radio(
            t["transaction_type"], ["wynajem", "sprzedaz"],
            index=["wynajem", "sprzedaz"].index(DEFAULT_CFG.get("transaction", "wynajem")),
            format_func=lambda v: TRANSACTION_LABELS[lang][v],
            horizontal=True,
        )
        city_names = list(CITY_SLUGS.keys())
        default_city = DEFAULT_CFG.get("city_display", "Kraków")
        city_name = st.selectbox(t["city"], city_names, index=city_names.index(default_city) if default_city in city_names else 0)

        c1, c2 = st.columns(2)
        price_min = c1.number_input(t["price_min"], min_value=0, value=int(DEFAULT_CFG.get("price_min") or 0), step=100)
        price_max = c2.number_input(t["price_max"], min_value=0, value=int(DEFAULT_CFG.get("price_max") or 5000), step=100)

        rooms_options = ["brak"] + core.ROOMS_ORDER
        rooms_format = lambda v: NONE_LABELS[lang] if v == "brak" else ROOMS_LABELS[lang].get(v, v)
        rooms_min = st.selectbox(t["rooms_min"], rooms_options, index=rooms_options.index(DEFAULT_CFG.get("rooms_min") or "brak"), format_func=rooms_format)
        rooms_max = st.selectbox(t["rooms_max"], rooms_options, index=rooms_options.index(DEFAULT_CFG.get("rooms_max") or "brak"), format_func=rooms_format)

        area_min = st.number_input(t["area_min"], min_value=0, value=int(DEFAULT_CFG.get("area_min_m2") or 0), step=5)
        private_owner_only = st.checkbox(t["private_owner_only"], value=DEFAULT_CFG.get("private_owner_only", False))
        mentions_raw = st.text_input(t["mentions"], value="")

        st.divider()
        st.subheader(t["amenities_header"])
        st.caption(t["amenities_caption"])

        amenities_cfg = {}
        for key, spec in DEFAULT_CFG.get("amenities", {}).items():
            enabled = st.checkbox(key, value=spec.get("buffer_m") is not None, key=f"en_{key}")
            if enabled:
                ac1, ac2 = st.columns([2, 1])
                buffer_m = ac1.number_input(
                    t["radius"], min_value=50, max_value=5000,
                    value=int(spec.get("buffer_m") or 300), step=50,
                    key=f"buf_{key}", label_visibility="collapsed",
                )
                required = ac2.checkbox(t["required"], value=spec.get("required", True), key=f"req_{key}")
                amenities_cfg[key] = {"qgis_layer": spec["qgis_layer"], "buffer_m": buffer_m, "required": required}

        st.divider()
        st.subheader(t["avoid_header"])
        st.caption(t["avoid_caption"])

        avoid_cfg = {}
        for key, spec in DEFAULT_CFG.get("amenities", {}).items():
            avoid_enabled = st.checkbox(f"{t['avoid_prefix']}{key}", value=False, key=f"avoid_en_{key}")
            if avoid_enabled:
                av1, av2 = st.columns([2, 1])
                avoid_buffer_m = av1.number_input(
                    t["radius"], min_value=50, max_value=5000,
                    value=300, step=50, key=f"avoid_buf_{key}", label_visibility="collapsed",
                )
                avoid_required = av2.checkbox(t["required"], value=True, key=f"avoid_req_{key}")
                avoid_cfg[key] = {"qgis_layer": spec["qgis_layer"], "buffer_m": avoid_buffer_m, "required": avoid_required}

        st.divider()
        st.caption(t["noise_caption"])

        search_clicked = st.button(t["search_button"], type="primary", width="stretch", key="multi_search_btn")

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
        with st.spinner(t["spinner"]):
            st.session_state["multi_results"] = run_search(json.dumps(cfg, sort_keys=True))

    results = st.session_state.get("multi_results")

    if results is None:
        st.info(t["set_criteria"])
    else:
        matches = [r for r in results if r["passes"]]
        st.subheader(t["results_header"].format(n=len(matches), m=len(results)))
        show_all = st.checkbox(t["show_all"], value=False, key="multi_show_all")
        to_show = results if show_all else matches

        if not to_show:
            st.warning(t["no_matches"])

        for r in to_show:
            with st.container(border=True):
                cols = st.columns([0.9, 3.1, 1, 1, 1.5])
                cols[0].caption(SOURCE_LABELS.get(r.get("source"), r.get("source", "—")))
                cols[1].markdown(f"**[{r['title']}]({r['url']})**")
                cols[2].write(f"{r['price']:.0f} PLN" if r["price"] else "—")
                cols[3].write(f"{r['area']:.0f} m²" if r["area"] else "—")
                cols[4].write(ROOMS_LABELS[lang].get(r["rooms"], r["rooms"] or "—"))

                if r.get("amenities"):
                    in_range = [k for k, ok in r["amenities"].items() if ok]
                    out_range = [k for k, ok in r["amenities"].items() if not ok]
                    if in_range:
                        st.caption(t["in_range"] + ", ".join(in_range))
                    if out_range:
                        st.caption(t["out_range"] + ", ".join(out_range))

                if r.get("avoid"):
                    too_close = [k for k, near in r["avoid"].items() if near]
                    far_enough = [k for k, near in r["avoid"].items() if not near]
                    if far_enough:
                        st.caption(t["far_enough"] + ", ".join(far_enough))
                    if too_close:
                        st.caption(t["too_close"] + ", ".join(too_close))

                if not r["passes"]:
                    reasons = []
                    if not r["rooms_ok"]:
                        reasons.append(t["reason_rooms"])
                    if not r["area_ok"]:
                        reasons.append(t["reason_area"])
                    if not r.get("price_ok", True):
                        reasons.append(t["reason_price"])
                    if not r["owner_ok"]:
                        reasons.append(t["reason_owner"])
                    if not r["required_mentions_ok"]:
                        reasons.append(t["reason_mentions"])
                    if not r["amenity_ok"]:
                        reasons.append(t["reason_amenity"])
                    if not r["avoid_ok"]:
                        reasons.append(t["reason_avoid"])
                    if reasons:
                        st.caption(t["fails"] + ", ".join(reasons))


if __name__ == "__main__":
    st.set_page_config(page_title="Szukaj mieszkań (multi-portal)", page_icon="🏠", layout="wide")
    render()
