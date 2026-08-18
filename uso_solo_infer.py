# -*- coding: utf-8 -*-
"""
Inferencia da aba "Uso do Solo" — auto-contida, sem depender do pipeline de
coleta (piloto_cerrado_rf.py, que puxa Supabase/multiprocessing).

Reproduz EXATAMENTE o mosaico de 25 bandas com que o modelo v7 foi treinado e
validado (base biestacional seca+chuva, 10 bandas cada, + 5 features temporais
anuais), baixa os pixels do recorte do imovel como numpy e roda o modelo
hierarquico local. A ordem das bandas vem do proprio pacote (pacote["bandas"]),
que foi a ordem usada na validacao.
"""

import io

import ee
import numpy as np
import requests
import shapely

# Bandas base biestacionais (mesma lista/ordem da v4/v7). O sufixo _seca/_chuva
# e a selecao final pela ordem de pacote["bandas"] garantem o casamento exato.
BANDAS_BASE = ["B2", "B3", "B4", "B8", "B11", "B12", "NDVI", "NDBI", "NDWI", "MNDWI"]

SENTINELA = -9999.0  # marca pixel sem dado (nuvem/borda) apos unmask


def _colecao_s2(ini, fim, regiao):
    cs = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")
    return (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(regiao).filterDate(ini, fim)
            .linkCollection(cs, ["cs_cdf"])
            .map(lambda im: im.updateMask(im.select("cs_cdf").gte(0.6))))


def _composto_estacao(ini, fim, regiao):
    m = _colecao_s2(ini, fim, regiao).median()
    ndvi = m.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndbi = m.normalizedDifference(["B11", "B8"]).rename("NDBI")
    ndwi = m.normalizedDifference(["B3", "B8"]).rename("NDWI")
    mndwi = m.normalizedDifference(["B3", "B11"]).rename("MNDWI")
    return (m.select(["B2", "B3", "B4", "B8", "B11", "B12"])
             .addBands([ndvi, ndbi, ndwi, mndwi]))


def _temporais_ano(ano, regiao):
    """5 estatisticas temporais do ano inteiro que separam lavoura de pastagem."""
    col = _colecao_s2(ee.Date.fromYMD(ano, 1, 1), ee.Date.fromYMD(ano, 12, 31), regiao)
    ndvi = col.map(lambda im: im.normalizedDifference(["B8", "B4"]).rename("NDVI")).select("NDVI")
    ndre = col.map(lambda im: im.normalizedDifference(["B8", "B5"]).rename("NDRE")).select("NDRE")
    ndvi_min = ndvi.min().rename("ndvi_min")
    ndvi_max = ndvi.max().rename("ndvi_max")
    ndvi_std = ndvi.reduce(ee.Reducer.stdDev()).rename("ndvi_std")
    ndre_max = ndre.max().rename("ndre_max")
    n_total = ndvi.count().rename("n")
    n_baixo = ndvi.map(lambda i: i.lt(0.3)).sum().rename("nb")
    frac_solo = n_baixo.divide(n_total.max(1)).rename("frac_solo")
    return ee.Image.cat([ndvi_min, ndvi_max, ndvi_std, ndre_max, frac_solo])


def mosaico_s2(ano, regiao):
    """Mosaico de 25 bandas identico ao do treino v7."""
    seca = _composto_estacao(ee.Date.fromYMD(ano, 5, 1), ee.Date.fromYMD(ano, 9, 30), regiao)
    chuva = _composto_estacao(ee.Date.fromYMD(ano - 1, 11, 1), ee.Date.fromYMD(ano, 3, 31), regiao)
    seca = seca.rename([b + "_seca" for b in BANDAS_BASE])
    chuva = chuva.rename([b + "_chuva" for b in BANDAS_BASE])
    return seca.addBands(chuva).addBands(_temporais_ano(ano, regiao))


def _escala_efetiva(bounds, scale, limite_pixels):
    """Aumenta a escala (pixel maior) se o recorte for grande demais para o
    download NPY. As PORCENTAGENS por classe sao robustas a essa granulacao
    (a area total vem da geometria, nao da contagem de pixels)."""
    minx, miny, maxx, maxy = bounds
    lat = (miny + maxy) / 2.0
    larg_m = abs(maxx - minx) * 111320.0 * max(0.1, np.cos(np.radians(lat)))
    alt_m = abs(maxy - miny) * 110540.0
    npix = (larg_m / scale) * (alt_m / scale)
    if npix <= limite_pixels:
        return scale
    fator = (npix / limite_pixels) ** 0.5
    nova = int(np.ceil(scale * fator / 10.0) * 10)  # arredonda p/ multiplo de 10
    return min(nova, 100)


def limpar_ruido(classe_2d, keep_cods, iteracoes=12):
    """Filtro de maioria: reatribui os pixels de classes NAO mantidas ao vizinho
    majoritario entre as classes mantidas, iterativamente. Remove o 'sal e
    pimenta' das classes de erro (que o corte de significancia ja tirou da
    tabela) do MAPA e das contagens. Nao cria buraco: todo pixel com dado (>=0)
    termina numa classe mantida.
    """
    keep = np.array(sorted(int(c) for c in keep_cods), dtype=classe_2d.dtype)
    out = classe_2d.copy()
    if keep.size == 0:
        return out

    dado = out >= 0
    ruido = dado & ~np.isin(out, keep)
    for _ in range(iteracoes):
        if not ruido.any():
            break
        counts = []
        for c in keep:
            m = (out == c).astype(np.int32)
            p = np.pad(m, 1)
            s = (p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:]
                 + p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:]
                 + p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:])
            counts.append(s)
        counts = np.stack(counts)                      # (K, H, W)
        maioria = keep[counts.argmax(axis=0)]
        tem_vizinho = counts.max(axis=0) > 0
        alvo = ruido & tem_vizinho
        out[alvo] = maioria[alvo]
        ruido = dado & ~np.isin(out, keep)
    if ruido.any():  # bloco sem vizinho mantido -> classe mantida mais comum
        dom = int(keep[np.argmax([(out == c).sum() for c in keep])])
        out[ruido] = dom
    return out


def classificar_imovel(geom_ee, geom_shapely, ano, pacote,
                       scale=20, limite_pixels=400000):
    """Classifica o uso do solo dentro do imovel.

    Retorna dict com: classe_2d (HxW, -1 = fora/sem dado), bounds (4326),
    contagem {cod: n_pixels}, n_total, scale_efetiva.
    """
    bandas = pacote["bandas"]
    modelo = pacote["modelo"]

    bounds = geom_shapely.bounds
    scale_ef = _escala_efetiva(bounds, scale, limite_pixels)

    img = mosaico_s2(int(ano), geom_ee).select(bandas).unmask(SENTINELA)
    url = img.getDownloadURL({
        "bands": bandas, "region": geom_ee, "scale": scale_ef,
        "crs": "EPSG:4326", "format": "NPY",
    })
    resp = requests.get(url, timeout=180)
    resp.raise_for_status()
    arr = np.load(io.BytesIO(resp.content))  # structured, shape (H, W)
    H, W = arr.shape

    X = np.stack([arr[b].astype("float32").ravel() for b in bandas], axis=1)

    # pixel valido = tem dado em todas as bandas (nao ficou no sentinela)
    valido = ~(X <= SENTINELA + 1).any(axis=1)

    # mascara do poligono real (containment vetorizado, shapely 2)
    minx, miny, maxx, maxy = bounds
    lons = np.linspace(minx + (maxx - minx) / W / 2, maxx - (maxx - minx) / W / 2, W)
    lats = np.linspace(maxy - (maxy - miny) / H / 2, miny + (maxy - miny) / H / 2, H)
    lon2d, lat2d = np.meshgrid(lons, lats)
    dentro = shapely.contains_xy(geom_shapely, lon2d.ravel(), lat2d.ravel())

    usar = valido & dentro
    classe = np.full(H * W, -1, dtype="int16")
    if usar.any():
        classe[usar] = modelo.predict(X[usar]).astype("int16")
    classe_2d = classe.reshape(H, W)

    cods, cnts = np.unique(classe[usar], return_counts=True) if usar.any() else (np.array([]), np.array([]))
    contagem = {int(c): int(n) for c, n in zip(cods, cnts)}

    return {
        "classe_2d": classe_2d,
        "bounds": (minx, miny, maxx, maxy),
        "contagem": contagem,
        "n_total": int(usar.sum()),
        "n_dentro": int(dentro.sum()),
        "n_sem_dado": int((dentro & ~valido).sum()),
        "scale_efetiva": scale_ef,
    }
