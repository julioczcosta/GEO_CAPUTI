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


# Satellite Embedding (AlphaEarth): 64 dims/pixel de um foundation model
# multimodal. So o modelo da Mata Atlantica (v2emb) usa (bandas A00..A63); no
# Cerrado essas bandas NAO entram em pacote["bandas"] -> o EE nem as computa.
BANDAS_EMB = [f"A{i:02d}" for i in range(64)]


def _emb(ano, regiao):
    """Media anual das 64 dims do GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL. Anual
    (max 2024) -> usa o ano mais proximo <= 2024."""
    yr = min(int(ano), 2024)
    col = (ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")
           .filterBounds(regiao)
           .filterDate(ee.Date.fromYMD(yr, 1, 1), ee.Date.fromYMD(yr, 12, 31)))
    return col.mosaic().rename(BANDAS_EMB)


def mosaico_s2(ano, regiao):
    """Mosaico de 25 bandas (base) + 64 do Satellite Embedding. O download so
    puxa as bandas de pacote["bandas"], entao o embedding so e computado quando
    o modelo (MA v2emb) o pede."""
    seca = _composto_estacao(ee.Date.fromYMD(ano, 5, 1), ee.Date.fromYMD(ano, 9, 30), regiao)
    chuva = _composto_estacao(ee.Date.fromYMD(ano - 1, 11, 1), ee.Date.fromYMD(ano, 3, 31), regiao)
    seca = seca.rename([b + "_seca" for b in BANDAS_BASE])
    chuva = chuva.rename([b + "_chuva" for b in BANDAS_BASE])
    return (seca.addBands(chuva).addBands(_temporais_ano(ano, regiao))
            .addBands(_emb(ano, regiao)))


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


def peneira(classe_2d, scale_m, mmu_ha=0.2):
    """Filtro de area minima (sieve / MMU): dissolve cada MANCHA conexa menor
    que `mmu_ha` hectares na classe majoritaria da vizinhanca — INDEPENDENTE da
    classe. E o que remove o 'sal e pimenta' que sobra DENTRO das classes
    confiaveis (ex.: alguns pixels de lavoura soltos no meio de um pastagem),
    que o filtro de maioria (que so mexe nas classes de erro) nao pega. Formas
    grandes ficam intactas; so o respingo isolado some. Mesma logica do MapBiomas.

    A area minima e em HECTARES e vira nº de pixels pela escala efetiva do
    recorte (nunca < 2 px), pra o criterio fisico nao mudar quando o app
    aumenta o pixel em imoveis grandes. 8-conectividade.
    """
    try:
        from scipy import ndimage
    except Exception:
        return classe_2d  # sem scipy: degrada limpo, mantem o mapa como esta

    min_pixels = max(2, int(round(mmu_ha * 10000.0 / (scale_m * scale_m))))
    out = classe_2d.copy()
    estrutura = np.ones((3, 3), dtype=bool)  # vizinhanca-8

    for _ in range(20):  # itera ate estabilizar (manchas somem em cascata)
        classes = np.array([int(c) for c in np.unique(out) if c >= 0], dtype=out.dtype)
        if classes.size <= 1:
            break
        absorver = np.zeros(out.shape, dtype=bool)
        for c in classes:
            lbl, n = ndimage.label(out == c, structure=estrutura)
            if n == 0:
                continue
            tam = np.bincount(lbl.ravel())
            pequenos = np.where(tam[1:] < min_pixels)[0] + 1  # rotulos 1..n
            if pequenos.size:
                absorver |= np.isin(lbl, pequenos)
        if not absorver.any():
            break
        # reatribui cada pixel a absorver ao vizinho majoritario entre os pixels
        # que NAO serao absorvidos (dado >= 0). Contagem 3x3 por classe.
        fixos = (out >= 0) & ~absorver
        counts = []
        for c in classes:
            m = (fixos & (out == c)).astype(np.int32)
            p = np.pad(m, 1)
            s = (p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:]
                 + p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:]
                 + p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:])
            counts.append(s)
        counts = np.stack(counts)
        maioria = classes[counts.argmax(axis=0)]
        alvo = absorver & (counts.max(axis=0) > 0)
        if not alvo.any():
            break  # so sobram manchas sem vizinho fixo -> para
        out[alvo] = maioria[alvo]
    return out


def suavizar_contexto(classe_2d, scale_m, classes_confusas=(0, 1, 2),
                      max_ha=2.0, pureza=0.75, aspecto_max=4.0, iteracoes=3,
                      classes_lineares=(0,)):
    """Filtro CONTEXTUAL de ilha: dissolve MANCHAS pequenas de classe errada
    EMBUTIDAS em outra classe (ex.: uma ilha de pastagem no meio de um lavoura,
    quando o pastagem de fato esta concentrado noutro lugar). Isso o sieve/MMU
    nao pega — a mancha e maior que a area minima; e o filtro por-pixel tambem
    nao, pois o interior da mancha segura suas proprias bordas. Aqui a decisao e
    no COMPONENTE inteiro: se a mancha (a) e menor que `max_ha`, (b) e compacta
    (nao e faixa linear) e (c) tem a borda dominada (>= `pureza`) por UMA unica
    outra classe, a mancha toda vira essa classe. Baseia-se na suavidade
    espacial do uso do solo.

    Salvaguardas: so ENTRE as `classes_confusas` (nativa/lavoura/pastagem) —
    agua, silvicultura, solo e varzea nao sao vitima nem alvo, entao lago /
    reflorestamento reais nao somem. Borda entre dois blocos grandes: a mancha
    e grande (> max_ha) e nao entra.

    O teste de compacidade (`aspecto_max` / fill da bounding-box) protege
    feicoes LINEARES so quando a VITIMA e de `classes_lineares` (nativa, p/ nao
    apagar mata ciliar). Para vitima lavoura/pastagem NAO se aplica: uma ilha
    fina/esparramada de pastagem cercada de lavoura e erro, entao dissolve.
    Mata ciliar de verdade costuma cruzar o imovel (componente grande) e ja e
    protegida pelo teto de area.
    """
    try:
        from scipy import ndimage
    except Exception:
        return classe_2d

    conf = set(int(c) for c in classes_confusas)
    lineares = set(int(c) for c in classes_lineares)
    max_px = max(4, int(round(max_ha * 10000.0 / (scale_m * scale_m))))
    out = classe_2d.copy()
    estrutura = np.ones((3, 3), dtype=bool)

    for _ in range(iteracoes):
        mudou = False
        for c in list(conf):
            mask_c = out == c
            if not mask_c.any():
                continue
            lbl, n = ndimage.label(mask_c, structure=estrutura)
            if n == 0:
                continue
            tam = np.bincount(lbl.ravel())
            objs = ndimage.find_objects(lbl)
            for L in range(1, n + 1):
                area = tam[L]
                if area == 0 or area > max_px:
                    continue
                sl = objs[L - 1]
                if sl is None:
                    continue
                if c in lineares:  # so protege forma quando vitima e linear (mata ciliar)
                    h = sl[0].stop - sl[0].start
                    w = sl[1].stop - sl[1].start
                    aspecto = max(h, w) / max(1, min(h, w))
                    if aspecto > aspecto_max or area < 0.4 * h * w:
                        continue  # faixa linear / esparramada -> preserva
                sl2 = (slice(max(0, sl[0].start - 1), sl[0].stop + 1),
                       slice(max(0, sl[1].start - 1), sl[1].stop + 1))
                sub_lbl = lbl[sl2]
                sub_out = out[sl2]
                comp = sub_lbl == L
                borda = ndimage.binary_dilation(comp, estrutura) & ~comp
                vals = sub_out[borda]
                vals = vals[(vals >= 0) & (vals != c)]
                if vals.size == 0:
                    continue
                cc = np.bincount(vals)
                D = int(cc.argmax())
                if D in conf and cc[D] / vals.size >= pureza:
                    reg = out[sl2]
                    reg[comp] = D
                    mudou = True
        if not mudou:
            break
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
    # teto de pixels adaptado ao nº de bandas (o download NPY do EE tem limite
    # ~50MB): o modelo da MA (embedding) tem 57 bandas -> pixel maior em imovel
    # grande, senao estoura. As % por classe sao robustas (area vem da geometria).
    lim = min(limite_pixels, int(45e6 / (max(1, len(bandas)) * 4)))
    scale_ef = _escala_efetiva(bounds, scale, lim)

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


def ndvi_serie_mensal(pontos_lonlat, ano_ini, ano_fim):
    """Serie temporal de NDVI (media mensal) em cada ponto clicado.

    Usa Sentinel-2 com a MESMA mascara de nuvem da classificacao
    (CLOUD_SCORE_PLUS via `_colecao_s2`), garantindo consistencia. Faz todo o
    trabalho no servidor do Earth Engine e traz um unico getInfo.

    pontos_lonlat: lista de (lon, lat) em graus (EPSG:4326).
    Retorna:
        {"meses": ["2019-01", ...],
         "series": {i_ponto: [ndvi_ou_None por mes]}}
    Mes sem imagem limpa (nuvem/sem passagem) vira None (buraco no grafico).
    """
    from datetime import date

    pontos_lonlat = list(pontos_lonlat or [])
    if not pontos_lonlat:
        return {"meses": [], "series": {}}

    hoje = date.today()
    meses = []
    for y in range(int(ano_ini), int(ano_fim) + 1):
        for mth in range(1, 13):
            if y == hoje.year and mth > hoje.month:
                break
            meses.append((y, mth))
    if not meses:
        return {"meses": [], "series": {}}

    fc = ee.FeatureCollection([
        ee.Feature(ee.Geometry.Point([float(lon), float(lat)]), {"pid": i})
        for i, (lon, lat) in enumerate(pontos_lonlat)
    ])
    regiao = fc.geometry().bounds().buffer(500)

    datas = ee.List([ee.Date.fromYMD(int(y), int(m), 1) for (y, m) in meses])

    def por_mes_img(d):
        d = ee.Date(d)
        col = _colecao_s2(d, d.advance(1, "month"), regiao)
        # If do mes sem imagem: banda toda mascarada -> media nula (buraco).
        return ee.Image(ee.Algorithms.If(
            col.size().gt(0),
            col.median().normalizedDifference(["B8", "B4"]),
            ee.Image.constant(0).updateMask(ee.Image.constant(0)),
        ))

    # Empilha um NDVI por mes numa imagem multi-banda e faz UM unico
    # reduceRegions — evita o erro "Too many concurrent aggregations" que
    # ocorre ao mapear um reduceRegions por mes.
    bandas = [f"m{y:04d}_{m:02d}" for (y, m) in meses]
    stack = ee.ImageCollection(datas.map(por_mes_img)).toBands().rename(bandas)
    feats = stack.reduceRegions(
        collection=fc, reducer=ee.Reducer.mean(), scale=10,
    ).getInfo()["features"]

    rotulos = [f"{y:04d}-{m:02d}" for (y, m) in meses]
    series = {i: [None] * len(rotulos) for i in range(len(pontos_lonlat))}
    for ft in feats:
        p = ft.get("properties", {})
        pid = p.get("pid")
        if pid is None:
            continue
        pid = int(pid)
        if pid not in series:
            continue
        for k, banda in enumerate(bandas):
            val = p.get(banda)
            if val is not None:
                series[pid][k] = round(float(val), 3)

    return {"meses": rotulos, "series": series}


def precip_serie_mensal(geom_ee, ano_ini, ano_fim):
    """Série mensal de precipitação (CHIRPS) média na REGIÃO do imóvel.

    Uma única série (não por ponto): a chuva é regional, então basta a média
    da área — o que também é rápido (um único reduceRegion multi-banda).
    Alinha aos mesmos meses de `ndvi_serie_mensal` para sobrepor no gráfico.

    Retorna: {"meses": ["2019-01", ...], "precip": [mm_ou_None por mês]}.
    """
    from datetime import date

    hoje = date.today()
    meses = []
    for y in range(int(ano_ini), int(ano_fim) + 1):
        for m in range(1, 13):
            if y == hoje.year and m > hoje.month:
                break
            meses.append((y, m))
    rotulos = [f"{y:04d}-{m:02d}" for (y, m) in meses]
    if not meses:
        return {"meses": [], "precip": []}

    chirps = ee.ImageCollection("UCSB-CHG/CHIRPS/PENTAD").select("precipitation")
    datas = ee.List([ee.Date.fromYMD(int(y), int(m), 1) for (y, m) in meses])

    def total_mes(d):
        d = ee.Date(d)
        col = chirps.filterDate(d, d.advance(1, "month"))
        # If garante 1 banda por mês mesmo quando não há dado (mês atual, que o
        # CHIRPS ainda não publicou) — senão o mês vazio some e o toBands/rename
        # quebra por contagem de bandas. Mês sem dado vira None (buraco).
        return ee.Image(ee.Algorithms.If(
            col.size().gt(0),
            col.sum(),
            ee.Image.constant(0).updateMask(ee.Image.constant(0)),
        )).rename("precipitation")

    bandas = [f"m{y:04d}_{m:02d}" for (y, m) in meses]
    stack = ee.ImageCollection(datas.map(total_mes)).toBands().rename(bandas)
    # CHIRPS tem pixel ~5,5 km. Em imóveis pequenos (< pixel), um reduceRegion a
    # scale=5000 amostra o grid a cada 5 km e pode não cair NENHUM ponto dentro
    # do perímetro -> retorna None em todos os meses e a precip "some". Como a
    # chuva é regional, bufferizamos para cobrir ao menos um pixel (garante valor).
    regiao = geom_ee.buffer(3000)
    try:
        vals = stack.reduceRegion(
            reducer=ee.Reducer.mean(), geometry=regiao, scale=5000,
            bestEffort=True, maxPixels=1e13,
        ).getInfo()
    except Exception:
        return {"meses": rotulos, "precip": [None] * len(rotulos)}

    precip = []
    for b in bandas:
        v = vals.get(b)
        precip.append(round(float(v), 1) if v is not None else None)
    return {"meses": rotulos, "precip": precip}


# ==========================================================================
#  MapBiomas (uso e cobertura do solo — referência, anual, 30 m)
# ==========================================================================

MAPBIOMAS_ASSET = ("projects/mapbiomas-public/assets/brazil/lulc/"
                   "collection9/mapbiomas_collection90_integration_v1")


def mapbiomas_anos():
    """Anos disponíveis na coleção MapBiomas (lidos das bandas do asset)."""
    bandas = ee.Image(MAPBIOMAS_ASSET).bandNames().getInfo()
    return sorted(int(b.split("_")[-1]) for b in bandas
                  if b.startswith("classification_"))


def mapbiomas_areas(geom_ee, ano):
    """Contagem de pixels por classe MapBiomas no imóvel, para o ano dado.

    Server-side (frequencyHistogram) — rápido e sem download, escala p/ qualquer
    área. Retorna {codigo_classe(int): n_pixels(int)}. O % por classe sai da
    contagem; os hectares vêm da área real da geometria (como na classificação).
    """
    banda = f"classification_{int(ano)}"
    hist = ee.Image(MAPBIOMAS_ASSET).select(banda).reduceRegion(
        reducer=ee.Reducer.frequencyHistogram(), geometry=geom_ee, scale=30,
        maxPixels=1e13, bestEffort=True,
    ).getInfo().get(banda, {}) or {}

    out = {}
    for k, v in hist.items():
        try:
            out[int(k)] = int(round(float(v)))
        except Exception:
            continue
    return out


# ==========================================================================
#  Áreas difíceis — índice combinado de restrição (relevo + água + solo)
# ==========================================================================

# Códigos das classes de restrição (0 = sem restrição detectada).
REST_OK, REST_RELEVO, REST_AGUA, REST_SOLO = 0, 1, 2, 3


def _imagem_restricoes(geom_ee, limiar_declive_pct=20.0):
    """Imagem classificada de restrições no imóvel (tudo no servidor do GEE).

    Combina três fontes de "dificuldade" para o uso/valor do imóvel rural:
      1 = RELEVO   — declividade acima do limiar (SRTM). Difícil mecanizar; vira
                     APP/reserva em encostas fortes.
      2 = ÁGUA     — corpos d'água / solo encharcado (NDWI de Sentinel-2 > 0).
                     Candidato a APP de nascente/curso d'água; área alagável.
      3 = SOLO     — solo exposto / degradado (BSI > 0 e NDVI < 0,30).
    Quando um pixel se enquadra em mais de uma, vale a PRIORIDADE água > relevo
    > solo (a mais restritiva ao uso). O relevo é global (SRTM), então classifica
    mesmo sem imagem limpa; água/solo dependem do composto Sentinel-2 recente.
    """
    from datetime import date

    # Relevo: declividade em PORCENTO (o slope do GEE vem em graus).
    slope_deg = ee.Terrain.slope(ee.Image("USGS/SRTMGL1_003"))
    slope_pct = slope_deg.multiply(3.141592653589793 / 180.0).tan().multiply(100)
    relevo = slope_pct.gt(limiar_declive_pct)

    # Composto Sentinel-2 dos últimos 24 meses, nuvens mascaradas (CLOUD_SCORE+).
    hoje = date.today()
    d_fim = ee.Date.fromYMD(hoje.year, hoje.month, 1).advance(1, "month")
    d_ini = d_fim.advance(-24, "month")
    m = _colecao_s2(d_ini, d_fim, geom_ee).median()

    ndvi = m.normalizedDifference(["B8", "B4"])
    ndwi = m.normalizedDifference(["B3", "B8"])  # McFeeters: água > 0
    # BSI = ((SWIR+Red) - (NIR+Blue)) / ((SWIR+Red) + (NIR+Blue))
    swir_red = m.select("B11").add(m.select("B4"))
    nir_blue = m.select("B8").add(m.select("B2"))
    bsi = swir_red.subtract(nir_blue).divide(swir_red.add(nir_blue))

    agua = ndwi.gt(0.0)
    solo = bsi.gt(0.0).And(ndvi.lt(0.30))  # solo nu e não vegetado

    classe = (ee.Image(0)
              .where(solo, REST_SOLO)
              .where(relevo, REST_RELEVO)
              .where(agua, REST_AGUA))  # água por último = maior prioridade
    return classe.rename("restricao").toInt().clip(geom_ee)


def restricoes_areas(geom_ee, limiar_declive_pct=20.0):
    """Contagem de pixels por classe de restrição (frequencyHistogram, 10 m).

    Retorna {codigo(int): n_pixels(int)} incluindo 0 (sem restrição). O % sai da
    contagem; os hectares vêm da área real da geometria (como nas outras abas).
    """
    img = _imagem_restricoes(geom_ee, limiar_declive_pct)
    hist = img.reduceRegion(
        reducer=ee.Reducer.frequencyHistogram(), geometry=geom_ee, scale=10,
        maxPixels=1e13, bestEffort=True,
    ).getInfo().get("restricao", {}) or {}

    out = {}
    for k, v in hist.items():
        try:
            out[int(float(k))] = int(round(float(v)))
        except Exception:
            continue
    return out
