#!/usr/bin/env python3
"""
Jeden streamlit, dwie zakładki - łączy w sobie:
1. multi_web.py    - wyszukiwarka mieszkań (Otodom + nieruchomosci-online +
                      Morizon) z filtrami QGIS.
2. jakdaleko_web.py - odległości/hałas/MPZP/mapa dla dowolnego adresu.

Oba pliki zostają w pełni samodzielne (da się je nadal uruchomić osobno:
streamlit run multi_web.py / streamlit run jakdaleko_web.py) - eksportują
tylko funkcję render(), którą ten plik woła w dwóch osobnych zakładkach,
bez duplikowania ani grzebania w ich logice.

URUCHOM: streamlit run MultiAgent.py

Jeśli ustawiony jest sekret/zmienna środowiskowa APP_PASSWORD, strona jest
zablokowana prostym hasłem (do pokazania appki znajomym bez otwierania jej
całemu internetowi).
"""

import os

import streamlit as st

import multi_web
import jakdaleko_web

st.set_page_config(page_title="Nieruchomości — Kraków", page_icon="🏠", layout="wide")

APP_PASSWORD = os.getenv("APP_PASSWORD")
if APP_PASSWORD and not st.session_state.get("authed"):
    pw = st.text_input("Hasło dostępu", type="password")
    if pw:
        if pw == APP_PASSWORD:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("Złe hasło.")
    st.stop()

tab_szukaj, tab_jakdaleko = st.tabs(["🏠 Szukaj mieszkań", "📍 jakdaleko"])

with tab_szukaj:
    multi_web.render()

with tab_jakdaleko:
    jakdaleko_web.render()
