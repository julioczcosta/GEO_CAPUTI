# -*- coding: utf-8 -*-
"""
Aba "Uso do Solo" — classificacao de uso do solo do imovel (Cerrado) por
Sentinel-2 + Random Forest (modelo v7, hierarquico).

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
from shapely.ops import unary_union
from shapely.geometry import mapping, shape
from PIL import Image

import joblib
import modelo_hierarquico  # noqa: F401 - necessario para o joblib desserializar
import uso_solo_infer as infer

MODELO_PATH = os.path.join(os.path.dirname(__file__), "modelos", "modelo_uso_cerrado_v7.joblib")

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
}
NOMES_EXIBE = {
    0: "Vegetação Nativa", 1: "Lavoura", 2: "Pastagem", 3: "Pastagem degradada",
    4: "Corpo d'água", 5: "Silvicultura", 6: "Área aberta", 7: "Área de várzea",
}
# 2019..ano corrente. O ano corrente so tem a estacao seca (mai-set) COMPLETA
# depois de 30/set; antes disso a classificacao daquele ano sai PRELIMINAR.
ANOS = list(range(2019, date.today().year + 1))

# Corte de significancia para a tabela (classe abaixo do corte nao aparece).
# Duas faixas: classes CONFIAVEIS somem so se forem residuais; classes FRACAS
# (pasto degradado, varzea, silvicultura, area aberta) precisam de um pedaco
# GRANDE para aparecer - o modelo as detecta mal e elas pingam como ruido por
# todo o imovel; num imovel grande 1% ja viram muitos hectares "apontados" a
# toa. Por isso o corte delas e bem mais alto.
LIMIAR_PCT = 0.5
LIMIAR_FRACA_PCT = 5.0


@st.cache_resource(show_spinner=False)
def _carregar_modelo():
    return joblib.load(MODELO_PATH)


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
        style_function=lambda _f: {"color": "#FF0000", "weight": 2, "fillOpacity": 0},
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
    st.markdown("### Uso do Solo")
    st.warning("🧪 **Versão de testes.** Resultados experimentais, apenas para apoio "
               "— não use em laudo. A técnica ainda está em ajuste.")
    st.caption("Classificação automática de uso do solo do imóvel (Sentinel-2 + "
               "Random Forest), recortada ao perímetro.")

    gdf_imovel = st.session_state.get("gdf_imovel")
    if gdf_imovel is None or gdf_imovel.empty:
        st.info("📍 Carregue um imóvel na aba **Início** primeiro. A classificação "
                "é feita sobre o perímetro selecionado lá.")
        return

    try:
        pacote = _carregar_modelo()
    except Exception as e:
        st.error(f"Não foi possível carregar o modelo de classificação: {e}")
        return

    classes = pacote["classes"]
    fracas_nomes = set(pacote.get("classes_fracas", []))
    fracas_cods = {c for c, n in classes.items() if n in fracas_nomes}

    # partes distantes -> deixa escolher qual bloco classificar (como no Satelite)
    geom_shp, bloco_tag = _selecionar_bloco(gdf_imovel)

    # aviso se a area escolhida estiver fora do Cerrado (area de treino do modelo)
    try:
        cerr = _cerrado_geom()
        if cerr is not None and not cerr.contains(geom_shp.centroid):
            st.warning("⚠️ Este imóvel parece estar **fora do bioma Cerrado**. O modelo "
                       "foi treinado apenas no Cerrado — o resultado pode não ser "
                       "confiável aqui.")
    except Exception:
        pass

    # --- controles ---
    c1, c2 = st.columns([0.35, 0.65], vertical_alignment="bottom")
    with c1:
        ano = st.selectbox("Ano da imagem", ANOS, index=len(ANOS) - 1)
    with c2:
        rodar = st.button("🛰️ Classificar uso do solo", type="primary", use_container_width=True)

    nome_imovel = st.session_state.get("last_code", "imóvel")
    chave = f"{nome_imovel}|{ano}|{bloco_tag}"

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

    # aviso de ano preliminar (estacao seca ainda aberta) — abaixo do mapa/legenda
    ano_result = resultado.get("ano", guardado["dados"]["ano"])
    hoje = date.today()
    if ano_result > hoje.year or (ano_result == hoje.year and hoje.month <= 9):
        st.warning(f"⚠️ {ano_result}: a estação seca (mai–set) ainda não terminou — "
                   "classificação **preliminar**, pode mudar quando o ano se completar.")

    # --- rodape / ressalvas ---
    margem = pacote.get("margem_area_pp", {})
    if margem:
        txt = ", ".join(f"{NOMES_EXIBE[[k for k,v in classes.items() if v==nome][0]]} ±{_br(pp,1)}pp"
                        for nome, pp in margem.items()
                        if any(v == nome for v in classes.values()))
        st.caption(f"Margem típica por classe (validação por área, 80 fazendas): {txt}.")

    detalhe = f"Resolução: {resultado['scale_efetiva']} m · {n_total:,} pixels classificados".replace(",", ".")
    if resultado.get("n_sem_dado"):
        detalhe += f" · {resultado['n_sem_dado']} pixels sem imagem (nuvem/borda) foram ignorados"
    st.caption(detalhe)
    st.caption("⚠️ Estimativa de apoio — as classes marcadas como *baixa confiança* "
               "(pastagem degradada, várzea, silvicultura, área aberta) ainda não são "
               "detectadas de forma confiável e não devem ser reportadas isoladamente.")
