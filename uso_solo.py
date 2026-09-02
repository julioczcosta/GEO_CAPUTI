# -*- coding: utf-8 -*-
"""
Aba "Uso do Solo" — classificacao de uso do solo do imovel (Cerrado) por
Sentinel-2 + Random Forest hierarquico (modelo v9, base + cascata de especialistas).

FERRAMENTA DE APOIO (nao vai para laudo). Usa o imovel ja carregado na aba
Inicio (st.session_state['gdf_imovel']): recorta a classificacao ao perimetro,
mostra a area calculada, as porcentagens e os hectares por classe, um campo de
area de referencia (matricula/SIGEF) com hectares proporcionais e um mapa
colorido com a paleta dos manuais.
"""

import io
import os
import json
import base64
from datetime import date

import ee
import numpy as np
import streamlit as st
import geopandas as gpd
import geemap.foliumap as geemap
import folium
import plotly.graph_objects as go
from streamlit_folium import st_folium
from shapely.ops import unary_union
from shapely.geometry import mapping, shape, Point
from PIL import Image

import joblib
import ui
import classificador_consolidado  # noqa: F401 - necessario para o joblib desserializar
import uso_solo_infer as infer

_MODELOS = os.path.join(os.path.dirname(__file__), "modelos")
MODELO_PADRAO = os.path.join(_MODELOS, "modelo_uso_cerrado_v9.joblib")
MODELO_SILVIC = os.path.join(_MODELOS, "modelo_uso_cerrado_v9_silvic.joblib")
MODELO_MA = os.path.join(_MODELOS, "modelo_uso_ma_v2emb.joblib")  # piloto MA-SE (base + Satellite Embedding)
COD_SILVICULTURA = 5
COD_PERENE = 8  # lavoura perene / cafe (so no modelo da MA)

# Paleta dos manuais (PyQGIS). "solo_exposto" e exibido como "Area aberta".
CORES = {
    0: "#088708",  # Vegetacao Nativa
    1: "#cfee00",  # Lavoura
    2: "#ebd762",  # Pastagem
    3: "#b2df8a",  # Pastagem degradada
    4: "#37f6e0",  # Corpo d'agua
    5: "#ff9a01",  # Silvicultura
    6: "#3f9571",  # Area aberta (solo exposto)
    7: "#091d61",  # Area de varzea
    8: "#a05a2c",  # Lavoura perene / cafe (so na Mata Atlantica)
}
NOMES_EXIBE = {
    0: "Vegetação Nativa", 1: "Lavoura", 2: "Pastagem", 3: "Pastagem degradada",
    4: "Corpo d'água", 5: "Silvicultura", 6: "Área aberta", 7: "Área de várzea",
    8: "Lavoura perene (café)",
}

# Cores distintas para os pontos de NDVI coletados (sub-aba NDVI).
CORES_PONTOS = ["#e6194B", "#4363d8", "#3cb44b", "#f58231",
                "#911eb4", "#008080", "#f032e6", "#9A6324"]

# MapBiomas Coleção 9 — nomes e cores oficiais das classes comuns no Brasil.
NOMES_MB = {
    1: "Floresta", 3: "Formação Florestal", 4: "Formação Savânica", 5: "Mangue",
    6: "Floresta Alagável", 9: "Silvicultura", 10: "Formação Natural não Florestal",
    11: "Campo Alagado e Área Pantanosa", 12: "Formação Campestre",
    13: "Outra Formação não Florestal", 15: "Pastagem", 18: "Agricultura",
    19: "Lavoura Temporária", 20: "Cana", 21: "Mosaico de Usos",
    22: "Área não Vegetada", 23: "Praia, Duna e Areal", 24: "Área Urbanizada",
    25: "Outras Áreas não Vegetadas", 27: "Não Observado", 29: "Afloramento Rochoso",
    30: "Mineração", 31: "Aquicultura", 32: "Apicum", 33: "Rio, Lago e Oceano",
    35: "Dendê", 36: "Lavoura Perene", 39: "Soja", 40: "Arroz",
    41: "Outras Lavouras Temporárias", 46: "Café", 47: "Citrus",
    48: "Outras Lavouras Perenes", 62: "Algodão",
}
CORES_MB = {
    1: "#32a65e", 3: "#1f8d49", 4: "#7dc975", 5: "#04381d", 6: "#026975",
    9: "#7a5900", 10: "#d6bc74", 11: "#519799", 12: "#d6bc74", 13: "#d89f5c",
    15: "#edde8e", 18: "#e974ed", 19: "#c27ba0", 20: "#db7093", 21: "#ffefc3",
    22: "#d4271e", 23: "#ffa07a", 24: "#d4271e", 25: "#db4d4f", 27: "#ffffff",
    29: "#ffaa5f", 30: "#9c0027", 31: "#091077", 32: "#fc8114", 33: "#2532e4",
    35: "#9065d0", 36: "#f3b4f1", 39: "#f5b3c8", 40: "#c71585", 41: "#f54ca9",
    46: "#d68fe2", 47: "#9932cc", 48: "#e6ccff", 62: "#ff69b4",
}
# Paleta indexada por código (0..máx) para renderizar o MapBiomas no mapa (ee).
PALETA_MB_LISTA = [CORES_MB.get(c, "#bdbdbd") for c in range(max(CORES_MB) + 1)]
# 2019..ano corrente. O ano corrente so tem a estacao seca (mai-set) COMPLETA
# depois de 30/set; antes disso a classificacao daquele ano sai PRELIMINAR.
ANOS = list(range(2019, date.today().year + 1))


def _ano_preliminar(ano):
    """True se a estacao seca (mai-set) desse ano ainda nao terminou (corte 30/set)."""
    h = date.today()
    return ano > h.year or (ano == h.year and h.month <= 9)

# Corte de significancia para a tabela (classe abaixo do corte nao aparece).
# Duas faixas: classes CONFIAVEIS somem so se forem residuais; classes FRACAS
# (pasto degradado, varzea, silvicultura, area aberta) precisam de um pedaco
# GRANDE para aparecer - o modelo as detecta mal e elas pingam como ruido por
# todo o imovel; num imovel grande 1% ja viram muitos hectares "apontados" a
# toa. Por isso o corte delas e bem mais alto.
LIMIAR_PCT = 0.5
LIMIAR_FRACA_PCT = 5.0


@st.cache_resource(show_spinner=False)
def _carregar_modelo(path):
    return joblib.load(path)


@st.cache_resource(show_spinner=False)
def _cerrado_geom():
    """Poligono (simplificado) do bioma Cerrado, para avisar quando o imovel
    esta fora da area de treino do modelo. None se o arquivo faltar."""
    p = os.path.join(os.path.dirname(__file__), "dados", "cerrado.geojson")
    try:
        with open(p, encoding="utf-8") as f:
            gj = json.load(f)
        feats = gj.get("features")
        return shape(feats[0]["geometry"]) if feats else shape(gj)
    except Exception:
        return None


@st.cache_resource(show_spinner=False)
def _ma_geom():
    """Poligono (simplificado) da Mata Atlantica Sudeste (piloto MA). Define
    quando usar o modelo da MA em vez do Cerrado. None se o arquivo faltar."""
    p = os.path.join(os.path.dirname(__file__), "dados", "mata_atlantica_se.geojson")
    try:
        with open(p, encoding="utf-8") as f:
            gj = json.load(f)
        feats = gj.get("features")
        return shape(feats[0]["geometry"]) if feats else shape(gj)
    except Exception:
        return None


def _br(valor, dec):
    try:
        return f"{float(valor):,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(valor)


def _hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _png_overlay(classe_2d):
    """RGBA PNG (data URI) do raster classificado; fora do imovel = transparente."""
    H, W = classe_2d.shape
    rgba = np.zeros((H, W, 4), dtype=np.uint8)
    for cod, hexcor in CORES.items():
        m = classe_2d == cod
        if m.any():
            r, g, b = _hex_rgb(hexcor)
            rgba[m] = (r, g, b, 255)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _mapa_html(geom_outline, resultado):
    minx, miny, maxx, maxy = resultado["bounds"]
    m = geemap.Map(
        center=[(miny + maxy) / 2, (minx + maxx) / 2], zoom=13, height=460,
        draw_control=False, scale_control=False, fullscreen_control=False,
        attribution_control=False, toolbar_control=False, lite_mode=True,
    )
    m.add_basemap("HYBRID")

    folium.raster_layers.ImageOverlay(
        image=_png_overlay(resultado["classe_2d"]),
        bounds=[[miny, minx], [maxy, maxx]],
        opacity=0.78, name="Uso do solo",
    ).add_to(m)

    folium.GeoJson(
        mapping(geom_outline), name="Perímetro",
        style_function=lambda _f: {"color": ui.COR_PERIMETRO, "weight": 2, "fillOpacity": 0},
    ).add_to(m)

    m.fit_bounds([[miny, minx], [maxy, maxx]])
    with io.BytesIO() as buffer:
        m.save(buffer, close_file=False)
        return buffer.getvalue().decode("utf-8")


def _legenda_html(cods_presentes):
    itens = ""
    for cod in cods_presentes:
        itens += (
            f'<div style="display:flex;align-items:center;margin:2px 0;font-size:0.85rem;">'
            f'<span style="width:14px;height:14px;border-radius:3px;background:{CORES[cod]};'
            f'border:1px solid #999;display:inline-block;margin-right:8px;"></span>'
            f'{NOMES_EXIBE[cod]}</div>'
        )
    return f'<div style="line-height:1.3;">{itens}</div>'


def _opcoes_escopo():
    """Se o imóvel veio de um arquivo com VÁRIAS matrículas (gdf_features),
    devolve as opções de escopo: 'Tudo junto (geral)' + uma por matrícula.
    Retorna (labels, geoms_shapely, tags) ou None quando não há multi-feição."""
    feats = st.session_state.get("gdf_features")
    if feats is None or len(feats) <= 1:
        return None
    labels = ["🟩 Tudo junto (geral)"]
    geoms = [unary_union(feats.geometry.values)]
    tags = ["geral"]
    for i in range(len(feats)):
        try:
            rot = str(feats.iloc[i]["_rotulo"])
        except Exception:
            rot = f"Feição {i + 1}"
        labels.append(f"📄 {rot}")
        geoms.append(feats.geometry.iloc[i])
        tags.append(f"m{i}")
    return labels, geoms, tags


def _selecionar_bloco(gdf_imovel):
    """Se o imovel tem partes geograficamente distantes, mostra um seletor para
    escolher qual classificar (como no Satelite). Retorna (geometria_shapely,
    tag). Partes proximas (ate ~200m) sao fundidas num mesmo bloco."""
    geom = unary_union(gdf_imovel.geometry.values)
    if geom.geom_type == "MultiPolygon":
        partes = list(geom.geoms)
    elif geom.geom_type == "GeometryCollection":
        partes = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
    else:
        partes = [geom]
    if len(partes) <= 1:
        return geom, "tudo"

    inflado = unary_union([p.buffer(0.002) for p in partes])  # ~200 m
    infl = list(inflado.geoms) if inflado.geom_type == "MultiPolygon" else [inflado]
    if len(infl) <= 1:  # tudo proximo — um bloco so
        return geom, "tudo"

    blocos = [unary_union([p for p in partes if p.intersects(bi)]) for bi in infl]
    opcoes = ["🟩 Tudo junto"]
    for i, b in enumerate(blocos):
        a = float(gpd.GeoSeries([b], crs=gdf_imovel.crs).to_crs(5880).area.iloc[0] / 1e4)
        opcoes.append(f"📍 Bloco {i + 1} ({_br(a, 1)} ha)")

    st.info("🌍 Foram identificadas áreas geograficamente distantes. Escolha qual "
            "classificar (blocos separados dão resolução melhor que tudo junto):")
    sel = st.selectbox("Bloco", opcoes, label_visibility="collapsed")
    idx = opcoes.index(sel)
    if idx == 0:
        return geom, "tudo"
    return blocos[idx - 1], f"b{idx}"


def render_tab():
    st.warning("🧪 **Versão de testes.** Resultados experimentais, apenas para apoio "
               "— não use em laudo. A técnica ainda está em ajuste.")

    gdf_imovel = st.session_state.get("gdf_imovel")
    if gdf_imovel is None or gdf_imovel.empty:
        ui.vazio()
        return

    ui.barra_imovel()

    tab_classe, tab_mb, tab_ndvi = st.tabs(
        ["📊 Classificação", "🗂️ MapBiomas", "🌱 NDVI (vigor)"])
    with tab_classe:
        _render_classificacao(gdf_imovel)
    with tab_mb:
        _render_mapbiomas(gdf_imovel)
    with tab_ndvi:
        _render_ndvi(gdf_imovel)


def _render_classificacao(gdf_imovel):
    st.caption("Classificação automática de uso do solo do imóvel (Sentinel-2 + "
               "Random Forest), recortada ao perímetro.")

    # --- roteamento de BIOMA: escolhe o modelo pelo centroide do imovel ---
    # Cerrado -> v9 (+ modo silvicultura); Mata Atlantica Sudeste -> v8 (piloto,
    # inclui cafe/lavoura perene). Fora dos dois: usa Cerrado com aviso.
    try:
        g4326 = gdf_imovel.to_crs(4326) if gdf_imovel.crs else gdf_imovel
        cent = unary_union(list(g4326.geometry)).centroid
    except Exception:
        cent = gdf_imovel.geometry.iloc[0].centroid
    cerr, ma = _cerrado_geom(), _ma_geom()
    em_cerrado = cerr is not None and cerr.contains(cent)
    em_ma = (not em_cerrado) and ma is not None and ma.contains(cent)

    if em_ma:
        st.info("🌱 Imóvel na **Mata Atlântica (Sudeste)** — modelo **piloto** da MA "
                "(inclui **lavoura perene / café**). Erro de área ~5 pp; o café é classe "
                "de **baixa confiança** (limite do satélite gratuito para café × floresta).")
        modo_silvic = False
        modelo_path = MODELO_MA
    else:
        modo_silvic = st.checkbox(
            "🌲 Modo silvicultura — ative para imóveis com reflorestamento",
            value=False,
            help="Detecta melhor a silvicultura (eucalipto/pínus). Deixe DESLIGADO em "
                 "imóveis sem reflorestamento — ligado, pode marcar silvicultura a mais.")
        modelo_path = MODELO_SILVIC if modo_silvic else MODELO_PADRAO
        if not em_cerrado:
            st.warning("⚠️ Este imóvel parece estar **fora do Cerrado e da Mata Atlântica "
                       "(Sudeste)** — áreas de treino. Usando o Cerrado; o resultado pode "
                       "não ser confiável aqui.")

    try:
        pacote = _carregar_modelo(modelo_path)
    except Exception as e:
        st.error(f"Não foi possível carregar o modelo de classificação: {e}")
        return

    classes = pacote["classes"]
    fracas_nomes = set(pacote.get("classes_fracas", []))
    fracas_cods = {c for c, n in classes.items() if n in fracas_nomes}
    if modo_silvic:
        fracas_cods.discard(COD_SILVICULTURA)  # detectada com confianca -> nao e "fraca"

    # Arquivo com várias matrículas -> seleciona por matrícula (estatísticas por
    # matrícula por padrão; 'Tudo junto' = geral). Senão, o seletor antigo de
    # partes geograficamente distantes.
    esc = _opcoes_escopo()
    if esc:
        labels, geoms, tags = esc
        st.caption("Arquivo com várias matrículas — estatísticas **por matrícula** "
                   "(escolha *Tudo junto* para o geral).")
        sel = st.selectbox("Matrícula", labels, index=1, key="uso_classe_matricula")
        i = labels.index(sel)
        geom_shp, bloco_tag = geoms[i], tags[i]
    else:
        geom_shp, bloco_tag = _selecionar_bloco(gdf_imovel)

    # --- controles ---
    # default no ULTIMO ano COMPLETO (evita abrir ja num ano preliminar como 2026;
    # vira o ano corrente sozinho depois de 30/set).
    completos = [a for a in ANOS if not _ano_preliminar(a)]
    idx_default = ANOS.index(completos[-1]) if completos else len(ANOS) - 1
    c1, c2 = st.columns([0.35, 0.65], vertical_alignment="bottom")
    with c1:
        ano = st.selectbox("Ano da imagem", ANOS, index=idx_default)
    with c2:
        rodar = st.button("🛰️ Classificar uso do solo", type="primary", use_container_width=True)

    # alerta na HORA que escolher um ano incompleto (antes de rodar)
    if _ano_preliminar(ano):
        st.warning(f"⚠️ {ano}: a estação seca (mai–set) ainda não terminou — a "
                   "classificação sai **preliminar** e pode mudar quando o ano se completar.")

    nome_imovel = st.session_state.get("last_code", "imóvel")
    chave = (f"{nome_imovel}|{ano}|{bloco_tag}|{'silv' if modo_silvic else 'pad'}"
             f"|{pacote.get('versao', 'cerrado')}")

    if rodar:
        try:
            geom_ee = ee.Geometry(mapping(geom_shp))
            area_ha = float(gpd.GeoSeries([geom_shp], crs=gdf_imovel.crs)
                            .to_crs(5880).area.iloc[0] / 1e4)
            with st.spinner("Baixando imagens e classificando a área..."):
                resultado = infer.classificar_imovel(geom_ee, geom_shp, ano, pacote)
            resultado["area_ha"] = area_ha
            resultado["ano"] = ano
            st.session_state["uso_result"] = {"chave": chave, "dados": resultado}
        except Exception as e:
            st.error(f"Falha na classificação: {e}")
            return

    guardado = st.session_state.get("uso_result")
    if not guardado or guardado["chave"] != chave:
        if guardado:
            st.info("O imóvel ou o ano mudou. Clique em **Classificar uso do solo** "
                    "para atualizar.")
        else:
            st.info("Escolha o ano e clique em **Classificar uso do solo**.")
        return

    resultado = guardado["dados"]
    n_total = resultado["n_total"]
    area_ha = resultado["area_ha"]

    if n_total == 0:
        st.warning("Não foi possível classificar (sem imagens válidas no período "
                   "para este imóvel). Tente outro ano.")
        return

    # --- opcoes de exibicao ---
    o1, o2 = st.columns([0.5, 0.5], vertical_alignment="bottom")
    with o1:
        agrupar = st.checkbox("Agrupar pastagem degradada em pastagem", value=False)
    with o2:
        area_ref = st.number_input(
            "Área de referência (matrícula/SIGEF), em ha — opcional",
            min_value=0.0, value=0.0, step=0.0001, format="%.4f",
            help="Se preenchida, mostra os hectares proporcionais a essa área.",
        )

    # raster de trabalho (opcional: pasto degradado -> pastagem)
    classe_2d = resultado["classe_2d"].copy()
    if agrupar:
        classe_2d[classe_2d == 3] = 2

    # corte de significancia: classes de erro exigem corte bem maior (o modelo
    # as detecta mal; num imovel grande um % baixo ja vira muitos ha).
    def _limiar(cod):
        return LIMIAR_FRACA_PCT if cod in fracas_cods else LIMIAR_PCT
    cods0, cnts0 = np.unique(classe_2d[classe_2d >= 0], return_counts=True)
    bruto = {int(c): int(n) for c, n in zip(cods0, cnts0)}
    total = sum(bruto.values()) or 1
    keep = {c for c, n in bruto.items() if n / total * 100 >= _limiar(c)}
    if not keep:  # imovel minusculo — mantem tudo
        keep = set(bruto)

    # FILTRO DE MAIORIA: pixels de classe fora do corte (erro/ruido, ex.:
    # silvicultura pingando) viram o vizinho majoritario mantido — somem do
    # mapa E das contagens, sem deixar buraco. Mapa e tabela ficam coerentes.
    classe_limpo = infer.limpar_ruido(classe_2d, keep)
    # FILTRO DE AREA MINIMA (sieve/MMU): dissolve manchas conexas minusculas
    # que sobraram DENTRO das classes confiaveis (ex.: pixels de lavoura soltos
    # num pastagem) na classe majoritaria ao redor. Tira o 'sal e pimenta' que
    # o filtro de maioria nao pega. Escala vem do recorte (criterio em ha).
    classe_limpo = infer.peneira(classe_limpo, resultado["scale_efetiva"])
    # FILTRO CONTEXTUAL DE ILHA: dissolve MANCHAS pequenas de nativa/lavoura/
    # pastagem embutidas em outra dessas classes (ex.: ilha de pastagem no meio
    # de um lavoura, com o pastagem real concentrado noutro lugar) — mancha
    # maior que a MMU, que o sieve nao pega. So mexe entre essas 3 classes que
    # se confundem; agua/silvicultura/solo/varzea e feicoes lineares ficam a salvo.
    # café(8) confunde com nativa na MA -> incluir no filtro de ilha (no-op no
    # Cerrado, que nao tem a classe 8).
    classe_limpo = infer.suavizar_contexto(classe_limpo, resultado["scale_efetiva"],
                                           classes_confusas=(0, 1, 2, 8))

    cods1, cnts1 = np.unique(classe_limpo[classe_limpo >= 0], return_counts=True)
    contagem = {int(c): int(n) for c, n in zip(cods1, cnts1)}
    n_total = int(cnts1.sum())
    linhas = sorted(contagem.items(), key=lambda kv: -kv[1])

    # --- metricas de area ---
    m1, m2 = st.columns(2)
    m1.metric("Área calculada (perímetro)", f"{_br(area_ha, 4)} ha")
    if area_ref and area_ref > 0:
        m2.metric("Área de referência", f"{_br(area_ref, 4)} ha")

    # --- tabela ---
    usar_ref = bool(area_ref and area_ref > 0)

    thead = ("<tr>"
             "<th style='text-align:left;'>Classe</th>"
             "<th style='text-align:right;'>%</th>"
             "<th style='text-align:right;'>Hectares</th>"
             + ("<th style='text-align:right;'>Ha (referência)</th>" if usar_ref else "")
             + "</tr>")
    corpo = ""
    for cod, n in linhas:
        pct = n / n_total * 100.0
        ha = area_ha * pct / 100.0
        ha_ref = area_ref * pct / 100.0
        badge = ("<span style='background:#fff3cd;color:#856404;font-size:0.72rem;"
                 "padding:1px 6px;border-radius:8px;margin-left:6px;'>baixa confiança</span>"
                 if cod in fracas_cods else "")
        swatch = (f"<span style='width:13px;height:13px;border-radius:3px;background:{CORES[cod]};"
                  f"border:1px solid #999;display:inline-block;margin-right:8px;vertical-align:middle;'></span>")
        corpo += (
            "<tr>"
            f"<td style='text-align:left;'>{swatch}{NOMES_EXIBE[cod]}{badge}</td>"
            f"<td style='text-align:right;'>{_br(pct, 2)}</td>"
            f"<td style='text-align:right;'>{_br(ha, 4)}</td>"
            + (f"<td style='text-align:right;'>{_br(ha_ref, 4)}</td>" if usar_ref else "")
            + "</tr>"
        )
    tabela = (
        "<table style='width:100%;border-collapse:collapse;font-size:0.92rem;'>"
        f"<thead style='border-bottom:2px solid #2C3E50;'>{thead}</thead>"
        f"<tbody>{corpo}</tbody></table>"
    )

    col_tab, col_mapa = st.columns([0.42, 0.58], gap="medium")
    with col_tab:
        st.markdown(tabela, unsafe_allow_html=True)
        st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
        st.markdown(_legenda_html([c for c, _ in linhas]), unsafe_allow_html=True)
    with col_mapa:
        res_map = {**resultado, "classe_2d": classe_limpo}
        st.components.v1.html(_mapa_html(geom_shp, res_map), height=470)

    # --- rodape / ressalvas ---
    margem = pacote.get("margem_area_pp", {})
    conf = set(pacote.get("classes_confiaveis", []))
    if margem:
        txt = ", ".join(f"{NOMES_EXIBE[[k for k,v in classes.items() if v==nome][0]]} ±{_br(pp,1)}pp"
                        for nome, pp in margem.items()
                        if nome in conf)
        st.caption(f"Margem típica por classe confiável (validação por área, 80 fazendas): {txt}.")

    detalhe = f"Resolução: {resultado['scale_efetiva']} m · {n_total:,} pixels classificados".replace(",", ".")
    if resultado.get("n_sem_dado"):
        detalhe += f" · {resultado['n_sem_dado']} pixels sem imagem (nuvem/borda) foram ignorados"
    st.caption(detalhe)
    st.caption("⚠️ Estimativa de apoio — as classes marcadas como *baixa confiança* "
               "(pastagem degradada, várzea, silvicultura, área aberta) ainda não são "
               "detectadas de forma confiável e não devem ser reportadas isoladamente.")


# ==========================================================================
#  SUB-ABA: NDVI (vigor da vegetação) — pontos clicados no perímetro
# ==========================================================================

def _imovel_wgs(gdf_imovel):
    """Geometria única do imóvel em EPSG:4326 (lon/lat), para o mapa e os cliques."""
    g = gdf_imovel if gdf_imovel.crs is not None else gdf_imovel.set_crs(4326)
    return unary_union(g.to_crs(4326).geometry.values)


def _zoom_bounds(minx, miny, maxx, maxy):
    """Zoom inicial do folium adequado à extensão do imóvel (o st_folium ignora
    fit_bounds; então calculamos o zoom em vez de depender dele)."""
    import math
    larg = max(abs(maxx - minx), abs(maxy - miny), 1e-4)
    return int(max(4, min(16, round(math.log2(360.0 / larg) - 0.5))))


def _ndvi_rotulo(v):
    """Rótulo curto de interpretação para um valor de NDVI."""
    if v is None:
        return "sem dado"
    if v < 0.1:
        return "água / sombra"
    if v < 0.2:
        return "solo exposto"
    if v < 0.4:
        return "vegetação rala"
    if v < 0.6:
        return "vegetação moderada"
    if v < 0.8:
        return "vegetação densa"
    return "muito densa / pico"


def _card_ponto_ndvi(pid, v):
    cor = CORES_PONTOS[pid % len(CORES_PONTOS)]
    val = f"{v:.2f}" if v is not None else "—"
    return (
        "<div style='border:1px solid #e9ecef;border-radius:10px;padding:10px 12px;"
        "background:#fff;box-shadow:0 1px 3px rgba(0,0,0,0.04);'>"
        "<div style='display:flex;align-items:center;gap:6px;font-size:0.78rem;color:#6c757d;'>"
        f"<span style='width:11px;height:11px;border-radius:50%;background:{cor};"
        "display:inline-block;'></span>"
        f"Ponto {pid + 1}</div>"
        f"<div style='font-size:1.6rem;font-weight:700;color:#2C3E50;line-height:1.1;"
        f"margin-top:2px;'>{val}</div>"
        f"<div style='font-size:0.76rem;color:#6c757d;'>{_ndvi_rotulo(v)}</div>"
        "</div>"
    )


# Assinaturas de NDVI HIPOTÉTICAS (ilustrativas) de cada classe ao longo do ano,
# para o usuário reconhecer o padrão. Valores Jan..Dez; cores da classificação.
# O gráfico de exemplo repete estes 12 meses por alguns anos (o padrão sazonal
# se repete), pra casar com o gráfico real, que é multianual (2019..atual).
ANOS_EXEMPLO = 3
EXEMPLOS_NDVI = [
    ("Lavoura anual", CORES[1],
     [0.85, 0.80, 0.55, 0.30, 0.20, 0.18, 0.17, 0.18, 0.28, 0.50, 0.72, 0.85],
     "Pico forte na safra e queda na colheita — grande variação no ano "
     "(pode ter um 2º pico menor da safrinha)."),
    ("Pastagem", CORES[2],
     [0.62, 0.63, 0.60, 0.52, 0.45, 0.40, 0.35, 0.33, 0.35, 0.45, 0.55, 0.60],
     "Acompanha a chuva: sobe no verão, cai na seca — mas nunca fica exposta."),
    ("Vegetação nativa", CORES[0],
     [0.78, 0.80, 0.78, 0.72, 0.68, 0.64, 0.60, 0.58, 0.60, 0.66, 0.72, 0.76],
     "Alta e estável o ano todo, com leve queda na seca."),
    ("Silvicultura", CORES[5],
     [0.80, 0.81, 0.80, 0.79, 0.80, 0.80, 0.79, 0.80, 0.81, 0.80, 0.80, 0.81],
     "Alta e quase constante (perene); cai de vez quando é colhida."),
    ("Solo exposto / área aberta", CORES[6],
     [0.15, 0.16, 0.15, 0.14, 0.15, 0.15, 0.14, 0.15, 0.16, 0.15, 0.15, 0.16],
     "Baixa e plana o ano inteiro."),
    ("Corpo d'água", CORES[4],
     [-0.05, -0.04, -0.05, -0.05, -0.04, -0.05, -0.05, -0.04, -0.05, -0.05, -0.04, -0.05],
     "Próxima de zero ou negativa."),
]


def _mini_grafico_exemplo(nome, cor, valores):
    r, g, b = _hex_rgb(cor)
    n = len(valores)                      # 12 meses
    y = valores * ANOS_EXEMPLO            # repete o padrão sazonal por vários anos
    x = list(range(len(y)))
    fig = go.Figure(go.Scatter(
        x=x, y=y, mode="lines",
        line=dict(color=cor, width=2.5),
        fill="tozeroy", fillcolor=f"rgba({r},{g},{b},0.18)", hoverinfo="skip",
    ))
    # separadores pontilhados entre os anos, pra deixar claro que o padrão
    # se repete a cada ano (como no gráfico real, multianual)
    for k in range(1, ANOS_EXEMPLO):
        fig.add_vline(x=k * n - 0.5, line=dict(color="#ddd", width=1, dash="dot"))
    fig.update_layout(
        height=120, margin=dict(l=6, r=6, t=6, b=4),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", showlegend=False,
        yaxis=dict(range=[-0.15, 1.0], showticklabels=False, showgrid=False, zeroline=False),
        xaxis=dict(showgrid=False, tickmode="array",
                   tickvals=[k * n + n / 2 - 0.5 for k in range(ANOS_EXEMPLO)],
                   ticktext=[f"ano {k + 1}" for k in range(ANOS_EXEMPLO)],
                   tickfont=dict(size=9, color="#999")),
    )
    return fig


def _guia_ndvi():
    with st.expander("📖 Guia de interpretação do NDVI"):
        st.markdown(
            "**O que é.** O NDVI mede o vigor da vegetação a partir da luz refletida "
            "(infravermelho vs. vermelho). Vai de **-1 a 1**: quanto mais alto, mais "
            "vegetação verde e ativa.\n\n"
            "**Faixas de valor:**\n\n"
            "| NDVI | Normalmente indica |\n"
            "|---|---|\n"
            "| < 0,1 | Água, sombra, nuvem |\n"
            "| 0,1 – 0,2 | Solo exposto, área construída, rocha |\n"
            "| 0,2 – 0,4 | Vegetação rala / pastagem seca ou degradada |\n"
            "| 0,4 – 0,6 | Pastagem em bom estado / vegetação moderada |\n"
            "| 0,6 – 0,8 | Vegetação densa / cultura vigorosa / cerrado |\n"
            "| 0,8 – 1,0 | Floresta densa / cultura no pico |\n"
        )

        st.markdown("**Assinaturas típicas ao longo de vários anos** (curvas "
                    "ilustrativas — o padrão sazonal **se repete a cada ano**, como "
                    "no seu gráfico; compare o formato):")
        # Um exemplo por linha (gráfico à esquerda, texto à direita): legível
        # mesmo com a janela estreita/lateral, onde 3 colunas ficariam espremidas.
        for nome, cor, vals, desc in EXEMPLOS_NDVI:
            c_graf, c_txt = st.columns([1, 1.25], vertical_alignment="center")
            c_graf.plotly_chart(_mini_grafico_exemplo(nome, cor, vals),
                                 use_container_width=True,
                                 config={"displayModeBar": False},
                                 key=f"ex_ndvi_{nome}")
            c_txt.markdown(
                f"<div style='display:flex;align-items:center;gap:6px;font-weight:600;"
                f"font-size:0.92rem;color:#2C3E50;'>"
                f"<span style='width:12px;height:12px;border-radius:3px;background:{cor};"
                f"border:1px solid #999;display:inline-block;'></span>{nome}</div>",
                unsafe_allow_html=True)
            c_txt.caption(desc)

        st.caption("🌎 As curvas seguem o regime do **Cerrado / Centro-Oeste** "
                   "(seca mai–set). O **formato** vale em boa parte do Brasil agrícola, "
                   "mas o *timing* e a amplitude deslocam em outros biomas — na "
                   "**Caatinga**, por exemplo, a vegetação nativa é caducifólia e "
                   "**cai muito na seca** (não fica alta e estável como acima).")

        st.caption("⚠️ As curvas acima são **ilustrativas** (não são dados reais) — "
                   "servem só para reconhecer o padrão. Buracos na sua linha = mês sem "
                   "imagem limpa (nuvem ou sem passagem do satélite).")


def _render_ndvi(gdf_imovel):
    st.caption("Clique no mapa para coletar pontos e ver o vigor da vegetação "
               "(NDVI) de cada um ao longo do tempo. Pode coletar **fora** do "
               "perímetro também — útil para comparar com um vizinho.")

    geom = _imovel_wgs(gdf_imovel)
    minx, miny, maxx, maxy = geom.bounds
    pontos = st.session_state.setdefault("ndvi_pontos", [])

    col_mapa, col_pts = st.columns([0.6, 0.4], gap="medium")

    with col_mapa:
        fmap = folium.Map(location=[(miny + maxy) / 2, (minx + maxx) / 2],
                          zoom_start=_zoom_bounds(minx, miny, maxx, maxy),
                          tiles="Esri World Imagery", control_scale=True)
        folium.GeoJson(
            mapping(geom), name="Perímetro",
            style_function=lambda _f: {"color": ui.COR_PERIMETRO, "weight": 2, "fillOpacity": 0},
        ).add_to(fmap)
        for i, (lon, lat) in enumerate(pontos):
            cor = CORES_PONTOS[i % len(CORES_PONTOS)]
            dentro = geom.contains(Point(lon, lat))
            # Ponto fora do perímetro: borda âmbar (sinaliza "vizinho/comparação").
            borda = "#ffffff" if dentro else "#ffb300"
            tip = f"Ponto {i + 1}" + ("" if dentro else " (fora do perímetro)")
            folium.CircleMarker(
                [lat, lon], radius=9, color=borda, weight=3 if not dentro else 2,
                fill=True, fill_color=cor, fill_opacity=1.0, tooltip=tip,
            ).add_to(fmap)
            folium.Marker(
                [lat, lon],
                # icon_size/icon_anchor centram o número sobre a bolinha
                # (o DivIcon ancora no canto sup.-esq. por padrão, deslocando-o).
                icon=folium.DivIcon(
                    icon_size=(18, 18),
                    icon_anchor=(9, 9),
                    html=(
                        "<div style='font-size:11px;font-weight:700;color:#fff;"
                        "text-align:center;line-height:18px;width:18px;height:18px;'>"
                        f"{i + 1}</div>")),
            ).add_to(fmap)
        saida = st_folium(fmap, height=430, use_container_width=True,
                          key="ndvi_map", returned_objects=["last_clicked"])

    # trata o clique fora do bloco (para poder dar rerun com o novo marcador)
    clk = (saida or {}).get("last_clicked")
    if clk:
        novo = (round(clk["lng"], 6), round(clk["lat"], 6))
        if st.session_state.get("ndvi_last_click") != novo:
            st.session_state["ndvi_last_click"] = novo
            # Aceita pontos dentro OU fora do perímetro (comparação com vizinhos).
            pontos.append(novo)
            st.rerun()

    with col_pts:
        st.markdown("**Pontos coletados**")
        if not pontos:
            st.caption("Clique no mapa para adicionar pontos (dentro ou fora).")
        for i, (lon, lat) in enumerate(pontos):
            cor = CORES_PONTOS[i % len(CORES_PONTOS)]
            fora = not geom.contains(Point(lon, lat))
            tag_fora = (" · <span style='color:#b8860b;font-weight:600;'>fora</span>"
                        if fora else "")
            c1, c2 = st.columns([0.85, 0.15], vertical_alignment="center")
            c1.markdown(
                f"<span style='width:11px;height:11px;border-radius:50%;background:{cor};"
                f"display:inline-block;margin-right:7px;'></span>"
                f"<b>Ponto {i + 1}</b> · {lat:.4f}, {lon:.4f}{tag_fora}",
                unsafe_allow_html=True)
            if c2.button("✕", key=f"ndvi_rm_{i}", help="Remover ponto"):
                pontos.pop(i)
                st.session_state.pop("ndvi_result", None)
                st.rerun()
        if pontos:
            if st.button("🗑️ Limpar todos", key="ndvi_clear", use_container_width=True):
                pontos.clear()
                st.session_state.pop("ndvi_result", None)
                st.session_state.pop("ndvi_last_click", None)
                st.rerun()

        st.divider()
        anos = list(range(2019, date.today().year + 1))
        ca, cb = st.columns(2)
        ano_ini = ca.selectbox("De (ano)", anos, index=0, key="ndvi_ano_ini")
        ano_fim = cb.selectbox("Até (ano)", anos, index=len(anos) - 1, key="ndvi_ano_fim")
        gerar = st.button("📈 Gerar gráficos", type="primary", use_container_width=True,
                          disabled=not pontos, key="ndvi_gerar")

    if gerar and pontos:
        if ano_fim < ano_ini:
            st.warning("O ano final deve ser maior ou igual ao inicial.")
        else:
            with st.spinner("Calculando NDVI e precipitação no Earth Engine..."):
                dados = infer.ndvi_serie_mensal(pontos, ano_ini, ano_fim)
                try:
                    precip = infer.precip_serie_mensal(
                        ee.Geometry(mapping(geom)), ano_ini, ano_fim)
                except Exception:
                    precip = {"meses": [], "precip": []}
            st.session_state["ndvi_result"] = {
                "dados": dados, "precip": precip, "n": len(pontos),
                "chave": f"{[tuple(p) for p in pontos]}|{ano_ini}|{ano_fim}",
            }

    res = st.session_state.get("ndvi_result")
    if not res:
        st.info("Adicione pontos e clique em **Gerar gráficos**.")
        _guia_ndvi()
        return
    if res.get("n") != len(pontos):
        st.info("Os pontos mudaram. Clique em **Gerar gráficos** para atualizar.")
        _guia_ndvi()
        return

    dados = res["dados"]
    meses = dados["meses"]
    series = dados["series"]
    if not meses or not any(any(v is not None for v in s) for s in series.values()):
        st.warning("Sem dados de NDVI no período/pontos selecionados. Tente outro período.")
        _guia_ndvi()
        return

    st.markdown("##### Vigor no mês de referência")
    mes_ref = st.select_slider("Mês de referência", options=meses, value=meses[-1],
                               key="ndvi_mesref")
    idx_ref = meses.index(mes_ref)

    cols = st.columns(min(len(series), 4) or 1)
    for i, (pid, serie) in enumerate(series.items()):
        cols[i % len(cols)].markdown(_card_ponto_ndvi(pid, serie[idx_ref]),
                                     unsafe_allow_html=True)

    st.markdown("<div style='margin-top:14px;'></div>", unsafe_allow_html=True)

    precip_vals = (res.get("precip") or {}).get("precip")
    tem_precip = bool(precip_vals and len(precip_vals) == len(meses)
                      and any(v is not None for v in precip_vals))

    fig = go.Figure()
    if tem_precip:
        # Barras de chuva ao FUNDO (eixo direito), como pano de fundo para
        # comparar o vigor com a precipitação — média na região do imóvel.
        fig.add_trace(go.Bar(
            x=meses, y=precip_vals, name="Precipitação (mm)", yaxis="y2",
            marker_color="rgba(64,120,200,0.22)", marker_line_width=0,
            hovertemplate="%{y:.0f} mm",
        ))
    for pid, serie in series.items():
        cor = CORES_PONTOS[pid % len(CORES_PONTOS)]
        fig.add_trace(go.Scatter(
            x=meses, y=serie, mode="lines+markers", name=f"Ponto {pid + 1}",
            line=dict(color=cor, width=2), marker=dict(size=4),
            connectgaps=False, hovertemplate="%{y:.2f}",
        ))
    fig.add_vline(x=mes_ref, line=dict(color="#8899aa", dash="dot"))
    layout = dict(
        height=380, margin=dict(l=20, r=20, t=20, b=20),
        yaxis=dict(title="NDVI", range=[-0.05, 1.0]),
        hovermode="x unified", barmode="overlay",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    if tem_precip:
        maxp = max(v for v in precip_vals if v is not None) or 1
        # eixo da chuva "espremido" (2,2x o máximo) -> as barras ficam na metade
        # de baixo, como backdrop sutil sem competir com as linhas de NDVI.
        layout["yaxis2"] = dict(
            title="Precipitação (mm)", overlaying="y", side="right",
            showgrid=False, rangemode="tozero", range=[0, maxp * 2.2],
        )
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True)
    fonte = ("Fonte: Sentinel-2 (10 m), nuvens mascaradas (CLOUD_SCORE_PLUS). "
             "Média mensal de NDVI · buracos na linha = mês sem imagem limpa.")
    if tem_precip:
        fonte += " Barras: precipitação CHIRPS (média na região do imóvel)."
    else:
        fonte += " (Precipitação CHIRPS indisponível para esta área/período.)"
    st.caption(fonte)

    _guia_ndvi()


# ==========================================================================
#  SUB-ABA: MapBiomas (uso/cobertura de referência — anual, 30 m)
# ==========================================================================

def _mapa_mapbiomas_html(geom_shp, ano):
    """HTML do mapa MapBiomas recortado no imóvel (tiles do GEE, sem download)."""
    geom_ee = ee.Geometry(mapping(geom_shp))
    minx, miny, maxx, maxy = geom_shp.bounds
    m = geemap.Map(
        center=[(miny + maxy) / 2, (minx + maxx) / 2], zoom=13, height=460,
        draw_control=False, scale_control=False, fullscreen_control=False,
        attribution_control=False, toolbar_control=False, lite_mode=True,
    )
    m.add_basemap("HYBRID")
    img = (ee.Image(infer.MAPBIOMAS_ASSET)
           .select(f"classification_{int(ano)}").clip(geom_ee))
    m.add_layer(img, {"min": 0, "max": len(PALETA_MB_LISTA) - 1,
                      "palette": PALETA_MB_LISTA}, "MapBiomas")
    folium.GeoJson(
        mapping(geom_shp), name="Perímetro",
        style_function=lambda _f: {"color": ui.COR_PERIMETRO, "weight": 2, "fillOpacity": 0},
    ).add_to(m)
    m.fit_bounds([[miny, minx], [maxy, maxx]])
    with io.BytesIO() as buffer:
        m.save(buffer, close_file=False)
        return buffer.getvalue().decode("utf-8")


# Cache das chamadas ao GEE: o st.tabs renderiza TODAS as sub-abas a cada
# interação, então sem cache o MapBiomas bateria no GEE a cada rerun. Cacheado
# por (imóvel|ano), só recalcula ao trocar o ano.
@st.cache_data(show_spinner=False)
def _mb_anos():
    try:
        return infer.mapbiomas_anos()
    except Exception:
        return list(range(1985, 2024))


@st.cache_data(show_spinner=False)
def _mb_areas(_geom_ee, ano, cache_id):
    return infer.mapbiomas_areas(_geom_ee, ano)


@st.cache_data(show_spinner=False)
def _mb_mapa(_geom_shp, ano, cache_id):
    return _mapa_mapbiomas_html(_geom_shp, ano)


def _render_mapbiomas(gdf_imovel):
    st.caption("Uso e cobertura do solo pelo **MapBiomas** (referência anual, 30 m). "
               "Serve para comparar/validar a classificação do app.")

    # Escopo: geral por padrão; se houver várias matrículas, opção por matrícula.
    esc = _opcoes_escopo()
    if esc:
        labels, geoms, tags = esc
        sel = st.selectbox("Escopo", labels, index=0, key="mb_escopo")
        i = labels.index(sel)
        geom_shp, escopo_tag = geoms[i], tags[i]
    else:
        geom_shp, escopo_tag = unary_union(gdf_imovel.geometry.values), "geral"
    area_ha = float(gpd.GeoSeries([geom_shp], crs=gdf_imovel.crs)
                    .to_crs(5880).area.iloc[0] / 1e4)
    geom_ee = ee.Geometry(mapping(geom_shp))
    nome_imovel = st.session_state.get("last_code", "imovel")

    anos = _mb_anos()
    c1, c2 = st.columns([0.35, 0.65], vertical_alignment="bottom")
    with c1:
        ano = st.selectbox("Ano MapBiomas", anos, index=len(anos) - 1, key="mb_ano")
    with c2:
        area_ref = st.number_input(
            "Área de referência (matrícula/SIGEF), em ha — opcional",
            min_value=0.0, value=0.0, step=0.0001, format="%.4f", key="mb_area_ref",
            help="Se preenchida, mostra os hectares proporcionais a essa área.")

    try:
        contagem = _mb_areas(geom_ee, ano, f"{nome_imovel}|{escopo_tag}|{ano}")
    except Exception as e:
        st.error(f"Não foi possível consultar o MapBiomas: {e}")
        return

    if not contagem:
        st.warning("Sem dados do MapBiomas para este imóvel/ano.")
        return

    total = sum(contagem.values()) or 1
    linhas = sorted(contagem.items(), key=lambda kv: -kv[1])

    m1, m2 = st.columns(2)
    m1.metric("Área calculada (perímetro)", f"{_br(area_ha, 4)} ha")
    if area_ref and area_ref > 0:
        m2.metric("Área de referência", f"{_br(area_ref, 4)} ha")

    usar_ref = bool(area_ref and area_ref > 0)
    thead = ("<tr><th style='text-align:left;'>Classe</th>"
             "<th style='text-align:right;'>%</th>"
             "<th style='text-align:right;'>Hectares</th>"
             + ("<th style='text-align:right;'>Ha (referência)</th>" if usar_ref else "")
             + "</tr>")
    corpo = ""
    for cod, n in linhas:
        pct = n / total * 100.0
        ha = area_ha * pct / 100.0
        ha_ref = area_ref * pct / 100.0
        cor = CORES_MB.get(cod, "#bdbdbd")
        nome = NOMES_MB.get(cod, f"Classe {cod}")
        swatch = (f"<span style='width:13px;height:13px;border-radius:3px;background:{cor};"
                  "border:1px solid #999;display:inline-block;margin-right:8px;vertical-align:middle;'></span>")
        corpo += ("<tr>"
                  f"<td style='text-align:left;'>{swatch}{nome}</td>"
                  f"<td style='text-align:right;'>{_br(pct, 2)}</td>"
                  f"<td style='text-align:right;'>{_br(ha, 4)}</td>"
                  + (f"<td style='text-align:right;'>{_br(ha_ref, 4)}</td>" if usar_ref else "")
                  + "</tr>")
    tabela = ("<table style='width:100%;border-collapse:collapse;font-size:0.92rem;'>"
              f"<thead style='border-bottom:2px solid #2C3E50;'>{thead}</thead>"
              f"<tbody>{corpo}</tbody></table>")

    col_tab, col_mapa = st.columns([0.42, 0.58], gap="medium")
    with col_tab:
        st.markdown(tabela, unsafe_allow_html=True)
    with col_mapa:
        try:
            st.components.v1.html(_mb_mapa(geom_shp, ano, f"{nome_imovel}|{escopo_tag}|{ano}"), height=470)
        except Exception:
            st.caption("Mapa indisponível no momento.")

    st.caption(f"Fonte: MapBiomas Coleção 9 ({ano}) · 30 m. Referência independente — "
               "as classes e a resolução diferem da classificação do app.")
