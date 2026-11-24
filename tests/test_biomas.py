# -*- coding: utf-8 -*-
"""Testes do bioma por ponto (IBGE 1:250.000; rodar: python -m pytest tests)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import context  # noqa: E402


@pytest.mark.parametrize("lat, lon, bioma", [
    (-22.6205, -47.0660, "Mata Atlântica"),  # Holambra-SP: a 1:5.000.000 dava Cerrado
    (-15.7942, -47.8822, "Cerrado"),         # Brasília-DF
    (-3.1190, -60.0217, "Amazônia"),         # Manaus-AM
    (-9.3986, -40.5008, "Caatinga"),         # Petrolina-PE
    (-19.0094, -57.6533, "Pantanal"),        # Corumbá-MS
    (-31.3289, -54.1069, "Pampa"),           # Bagé-RS
    (-23.9608, -46.3336, "Mata Atlântica"),  # Santos-SP
    (-23.7781, -45.3581, "Mata Atlântica"),  # Ilhabela-SP (ilha)
])
def test_bioma_conhecido(lat, lon, bioma):
    assert context._bioma_no_ponto(lat, lon) == bioma


def test_logo_alem_da_costa_pega_o_mais_proximo():
    # ~1 km mar adentro em Santos: bioma mais próximo
    assert context._bioma_no_ponto(-23.9880, -46.3000) == "Mata Atlântica"


def test_alto_mar_nao_identificado():
    assert context._bioma_no_ponto(-25.0, -40.0) == "Não identificado"
