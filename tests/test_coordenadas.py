# -*- coding: utf-8 -*-
"""Testes do leitor de coordenadas (rodar: python -m pytest tests)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import coordenadas as c  # noqa: E402

# Brasilia: -15,7942 / -47,8822  ~  15°47'39"S 47°52'56"W  ~  UTM 23L 191204 8251714
LON, LAT = -47.8822, -15.7942


@pytest.mark.parametrize("txt", [
    "-15.7942, -47.8822",
    "-15,7942 -47,8822",
    "-15,7942, -47,8822",
    "-15.7942,-47.8822",
    "-47.8822, -15.7942",                 # ordem invertida
    "15.7942 47.8822",                    # sem sinal -> Sul/Oeste
    "Lat: -15.7942 Long: -47.8822",
    "15°47'39\"S 47°52'56\"W",
    "15 47 39 S, 47 52 56 O",
    "S 15°47'39\" W 47°52'56\"",
    "47°52'56\"W 15°47'39\"S",
    "15°47,65'S 47°52,93'W",
    "15º 47' 39,1\" Sul 47º 52' 56\" Oeste",
    "23L 191204 8251714",
    "fuso 23 E 191204 N 8251714",
    "8251714 191204 23S",
    "23K 191203,53 8251713,57",
    "https://www.google.com/maps/@-15.7942,-47.8822,15z",
])
def test_brasilia_em_varios_formatos(txt):
    [(lon, lat)] = c.interpretar(txt)
    assert lon == pytest.approx(LON, abs=2e-4)
    assert lat == pytest.approx(LAT, abs=2e-4)


def test_google_maps_prefere_local_marcado():
    url = ("https://www.google.com/maps/place/X/@-15.7942,-47.8822,12z/"
           "data=!3d-15.7801!4d-47.9292")
    assert c.interpretar(url) == [(-47.9292, -15.7801)]


def test_hemisferio_norte_com_sinal_so_na_longitude():
    assert c.interpretar("2.8235, -60.6758") == [(-60.6758, 2.8235)]


def test_varias_linhas():
    pts = c.interpretar("-15.7942 -47.8822\n-16.68, -49.25; -3.119 -60.0217")
    assert pts == [(-47.8822, -15.7942), (-49.25, -16.68), (-60.0217, -3.119)]


@pytest.mark.parametrize("txt, trecho", [
    ("", "digite"),
    ("abc", "duas coordenadas"),
    ("-15.79", "duas coordenadas"),
    ("192345 8251234", "fuso"),
    ("https://maps.app.goo.gl/xyz", "encurtados"),
    ("15 75 00 S 47 00 00 W", "menores que 60"),
    ("23L 1 2", "fora do Brasil"),
    ("48.85 N, 2.35 E", "fora do Brasil"),   # Paris
])
def test_erros_claros(txt, trecho):
    with pytest.raises(ValueError, match=trecho):
        c.interpretar(txt)


def test_erro_aponta_a_linha():
    with pytest.raises(ValueError, match="linha 2"):
        c.interpretar("-15.79 -47.88\nabc")
