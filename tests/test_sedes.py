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
# municipios criados depois de 2010 (fora do IBGE Localidades 2010)
SEM_SEDE = {"1504752", "4212650", "4220000", "4314548", "5006275", "5101837"}


@pytest.fixture(scope="module")
def sedes():
    return pd.read_csv(SEDES, dtype={"cd_mun": str})


def test_uma_sede_por_municipio(sedes):
    assert len(sedes) == 5565
    assert not sedes.cd_mun.duplicated().any()
    assert sedes[["lat", "lon", "alt"]].notna().all().all()


def test_cobre_a_malha_menos_os_novos(sedes):
    malha = pd.concat(gpd.read_parquet(p) for p in
                      glob.glob(os.path.join(RAIZ, "dados", "malha_municipios", "*.parquet")))
    # so faltam municipios novos (a malha 2022 ainda nao tem Boa Esperanca do Norte-MT)
    assert set(malha.cd_mun.astype(str)) - set(sedes.cd_mun) <= SEM_SEDE


def test_sede_dentro_do_proprio_municipio(sedes):
    malha = pd.concat(gpd.read_parquet(p) for p in
                      glob.glob(os.path.join(RAIZ, "dados", "malha_municipios", "*.parquet")))
    malha = malha.assign(cd_mun=malha.cd_mun.astype(str)).set_index("cd_mun")
    pts = gpd.GeoSeries(gpd.points_from_xy(sedes.lon, sedes.lat), crs=4674)
    poli = gpd.GeoSeries(malha.loc[sedes.cd_mun].geometry.values, crs=malha.crs)
    dist = pts.to_crs(5880).distance(poli.to_crs(5880))
    assert (dist == 0).mean() > 0.998          # 5560 de 5565 dentro
    assert dist.max() < 300                     # o resto na beira (rio/mar)


def test_get_sede_holambra():
    s = utils.get_sede_municipio("3519055")
    assert s["lat"] == pytest.approx(-22.64175)
    assert s["lon"] == pytest.approx(-47.059287)
    assert s["alt"] == pytest.approx(605.0)
    assert utils.get_sede_municipio(3519055) == s       # aceita int
    assert utils.get_sede_municipio("4212650") is None  # Pescaria Brava (2013)
