# -*- coding: utf-8 -*-
"""Testes da tabela de sedes municipais (rodar: python -m pytest tests)."""

import glob
import os
import sys

import geopandas as gpd
import pandas as pd
import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import utils  # noqa: E402

SEDES = os.path.join(RAIZ, "dados", "sedes_municipios.csv")
# municipios criados depois de 2010: sede pelo OpenStreetMap (fora do IBGE 2010)
NOVOS = {"1504752", "4212650", "4220000", "4314548", "5006275", "5101837"}


@pytest.fixture(scope="module")
def sedes():
    return pd.read_csv(SEDES, dtype={"cd_mun": str})


@pytest.fixture(scope="module")
def malha():
    m = pd.concat(gpd.read_parquet(p) for p in
                  glob.glob(os.path.join(RAIZ, "dados", "malha_municipios", "*.parquet")))
    return m.assign(cd_mun=m.cd_mun.astype(str)).set_index("cd_mun")


def test_uma_sede_por_municipio(sedes):
    assert len(sedes) == 5571
    assert not sedes.cd_mun.duplicated().any()
    assert sedes[["lat", "lon", "alt"]].notna().all().all()
    assert NOVOS <= set(sedes.cd_mun)


def test_cobre_toda_a_malha(sedes, malha):
    assert set(malha.index) <= set(sedes.cd_mun)


def test_sede_dentro_do_proprio_municipio(sedes, malha):
    # Boa Esperanca do Norte-MT (2023) ainda nao existe na malha 2022
    s = sedes[sedes.cd_mun.isin(malha.index)]
    pts = gpd.GeoSeries(gpd.points_from_xy(s.lon, s.lat), crs=4674)
    poli = gpd.GeoSeries(malha.loc[s.cd_mun].geometry.values, crs=malha.crs)
    dist = pts.to_crs(5880).distance(poli.to_crs(5880))
    assert (dist == 0).mean() > 0.998          # 5565 de 5570 dentro
    assert dist.max() < 300                     # o resto na beira (rio/mar)


def test_get_sede():
    s = utils.get_sede_municipio("3519055")      # Holambra-SP
    assert s["lat"] == pytest.approx(-22.64175)
    assert s["lon"] == pytest.approx(-47.059287)
    assert s["alt"] == pytest.approx(605.0)
    assert utils.get_sede_municipio(3519055) == s       # aceita int
    assert utils.get_sede_municipio("4212650") is not None  # Pescaria Brava (2013)
    assert utils.get_sede_municipio("9999999") is None
