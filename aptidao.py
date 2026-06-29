import streamlit as st
import geopandas as gpd
import folium
from folium.features import DivIcon
from streamlit_folium import st_folium
import plotly.express as px
from shapely.geometry import Polygon, Point
import pandas as pd
import tempfile
import zipfile
import os
import io
import time
import requests
import bs4


# ============================================================
# 1. CONFIGURAÇÕES E CORES
# ============================================================

COLOR_MAP_LEGENDA = {
    "1": "#AAFF00",
    "2": "#D7B09E",
    "3": "#FFAA00",
    "4": "#FFFFBC",
    "5": "#FF74E5",
    "6": "#D1D1D1",
    "Terra indígena": "#611F05",
    "Unidade de conservação": "#234D27",
    "Corpos d'água": "#61D6FF",
    "Área urbana": "#FF0000",
    "Sem Inf.": "#DDDDDD",
}

# Significado de cada grupo de aptidão (sistema Embrapa / Ramalho Filho & Beek).
# 1-3 = lavouras; 4 = pastagem plantada; 5 = silvicultura/pastagem natural;
# 6 = preservação (sem aptidão para uso agrícola).
NIVEL_APTIDAO = {
    "1": "Boa — lavouras",
    "2": "Regular — lavouras",
    "3": "Restrita — lavouras",
    "4": "Pastagem plantada",
    "5": "Silvicultura / pastagem natural",
    "6": "Preservação (sem aptidão agrícola)",
}

CLASSES_AGRICULTAVEIS = {"1", "2", "3"}          # terras de lavoura
CLASSES_APTIDAO = {"1", "2", "3", "4", "5", "6"}  # classes do sistema Embrapa
CLASSES_RESTRICAO = {
    "Terra indígena",
    "Unidade de conservação",
    "Corpos d'água",
    "Área urbana",
}


def nivel_aptidao(classe_norm):
    """Texto explicativo do grupo de aptidão a partir da classe normalizada."""
    return NIVEL_APTIDAO.get(str(classe_norm).strip(), str(classe_norm).strip())


# ============================================================
# 2. FUNÇÕES AUXILIARES
# ============================================================

def obter_epsg_por_latlon(lon, lat):
    """Calcula UTM local."""
    zona = int((lon + 180) / 6) + 1
    return 32700 + zona if lat < 0 else 32600 + zona


def uniao_geometrias(gdf):
    """
    Une geometrias usando union_all() quando disponível.
    Mantém compatibilidade com versões antigas.
    """
    try:
        return gdf.geometry.union_all()
    except Exception:
        return gdf.unary_union


def normalizar_classe_embrapa(txt):
    """
    Normaliza o texto da Embrapa para a chave curta de cor.
    Exemplo:
    '2(a)bc Aptidão REGULAR...' -> '2'
    """
    txt = str(txt).strip()
    txt_lower = txt.lower()

    for k in COLOR_MAP_LEGENDA.keys():
        if txt.startswith(k):
            return k

    if txt_lower in ["none", "nan", "", "null", "<na>"]:
        return "Sem Inf."

    if "terra indígena" in txt_lower or "terra indigena" in txt_lower:
        return "Terra indígena"

    if "unidade de conservação" in txt_lower or "unidade de conservacao" in txt_lower:
        return "Unidade de conservação"

    if "corpos d" in txt_lower or "água" in txt_lower or "agua" in txt_lower:
        return "Corpos d'água"

    if "área urbana" in txt_lower or "area urbana" in txt_lower:
        return "Área urbana"

    return txt


def get_color_embrapa(txt):
    key = normalizar_classe_embrapa(txt)
    return COLOR_MAP_LEGENDA.get(key, "#DDDDDD")


def corrigir_sigla_reforcada(row):
    """
    Corrige siglas vazias/nulas com base na classe normalizada.
    """
    s = str(row.get("simb_apt", "")).strip()
    c_norm = str(row.get("classe_norm", "")).strip().lower()

    invalidos = ["none", "nan", "", "null", "<na>"]

    if s.lower() in invalidos:
        if "unidade de conservação" in c_norm or "unidade de conservacao" in c_norm:
            return "UC"
        if "terra indígena" in c_norm or "terra indigena" in c_norm:
            return "TI"
        if "corpos d" in c_norm:
            return "Água"
        if "área urbana" in c_norm or "area urbana" in c_norm:
            return "Urb"
        if "sem inf" in c_norm:
            return "---"

    return s


def preparar_visual_embrapa(gdf_embrapa):
    """
    Mantém a lógica original:
    mostra TODO o entorno retornado pelo WFS da Embrapa.

    Não faz overlay com buffers.
    Não recorta pela geometria dos elementos.
    Apenas limpa campos e cria classe_norm para cor/tooltip.
    """
    if gdf_embrapa is None or gdf_embrapa.empty:
        return None

    gdf_visual = gdf_embrapa.copy()

    if gdf_visual.crs is None:
        gdf_visual = gdf_visual.set_crs(epsg=4326, allow_override=True)

    gdf_visual = gdf_visual.to_crs(epsg=4326)

    if "legenda_ap" in gdf_visual.columns:
        gdf_visual["legenda_ap"] = (
            gdf_visual["legenda_ap"]
            .astype(str)
            .str.replace(r"\.", "", regex=True)
            .str.strip()
        )
    else:
        gdf_visual["legenda_ap"] = "Sem Inf."

    if "simb_apt" in gdf_visual.columns:
        gdf_visual["simb_apt"] = (
            gdf_visual["simb_apt"]
            .astype(str)
            .str.replace(r"\.", "", regex=True)
            .str.strip()
        )
    else:
        gdf_visual["simb_apt"] = "---"

    gdf_visual["classe_norm"] = gdf_visual["legenda_ap"].apply(
        normalizar_classe_embrapa
    )

    gdf_visual["simb_apt"] = gdf_visual.apply(
        corrigir_sigla_reforcada,
        axis=1
    )

    gdf_visual["legenda_curta"] = (
        gdf_visual["legenda_ap"]
        .astype(str)
        .str.slice(0, 90)
    )

    gdf_visual = gdf_visual[
        gdf_visual.geometry.notna() & ~gdf_visual.geometry.is_empty
    ].copy()

    return gdf_visual


def cor_aptidao_por_feature(feature):
    """
    Define cor da camada de aptidão.
    """
    props = feature.get("properties", {})
    classe = props.get("classe_norm", "")
    legenda = props.get("legenda_ap", "")

    classe_str = str(classe).strip()

    if classe_str.lower() in [
        "sem inf.",
        "sem inf",
        "none",
        "nan",
        "",
        "<na>",
        "null"
    ]:
        classe_str = normalizar_classe_embrapa(legenda)

    return COLOR_MAP_LEGENDA.get(
        classe_str,
        get_color_embrapa(legenda)
    )


def opacidade_por_feature(feature):
    """
    Reduz a opacidade das classes muito dominantes para evitar o efeito de 'quadradão'.
    """
    props = feature.get("properties", {})
    classe = str(props.get("classe_norm", "")).strip()

    if classe in ["2", "6", "Sem Inf."]:
        return 0.32

    if classe in [
        "Terra indígena",
        "Unidade de conservação",
        "Corpos d'água",
        "Área urbana"
    ]:
        return 0.55

    return 0.48


@st.cache_data(show_spinner=False, ttl=86400)
def consultar_aptidao_embrapa(bbox_str):
    """Consulta o WFS de aptidão agrícola da Embrapa com retry/timeout.

    O geoserver da Embrapa é instável; aplica backoff em 429/502/503/504 e
    cacheia por bbox (TTL 24h) para não refazer a chamada a cada reprocessamento.
    Retorna um GeoDataFrame (possivelmente vazio) ou None em caso de falha.
    """
    url = "https://geoinfo.dados.embrapa.br/geoserver/ows"
    params = {
        "service": "WFS", "version": "1.0.0", "request": "GetFeature",
        "typeName": "geonode:aptagr_bra", "outputFormat": "application/json",
        "bbox": f"{bbox_str},EPSG:4326",
    }
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=40,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code in (429, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                return gpd.read_file(io.BytesIO(r.content))
            return None
        except Exception:
            time.sleep(2 ** attempt)
    return None


# ============================================================
# 3. PARSER KML/KMZ
# ============================================================

def carregar_kmz_kml_bs4(uploaded_file):
    try:
        suffix = os.path.splitext(uploaded_file.name)[1]

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded_file.getbuffer())
            tmp_path = tmp.name

        # finally garante a remoção do temporário mesmo se a leitura falhar
        # (ex.: KMZ sem .kml dentro dispara ValueError abaixo).
        try:
            kml_content = ""

            if zipfile.is_zipfile(tmp_path):
                with zipfile.ZipFile(tmp_path, "r") as kmz:
                    kmls = [f for f in kmz.namelist() if f.lower().endswith(".kml")]

                    if not kmls:
                        raise ValueError("Nenhum .kml encontrado no KMZ/ZIP.")

                    target = "doc.kml" if "doc.kml" in kmz.namelist() else kmls[0]

                    with kmz.open(target) as f:
                        kml_content = f.read().decode("utf-8", errors="ignore")

            else:
                with open(tmp_path, "r", encoding="utf-8", errors="ignore") as f:
                    kml_content = f.read()
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        soup = bs4.BeautifulSoup(kml_content, "xml")
        records = []
        placemarks = soup.find_all("Placemark")

        for placemark in placemarks:
            name_tag = placemark.find("name")
            name = name_tag.text.strip() if name_tag else ""

            if not name:
                if placemark.find("Polygon"):
                    name = "Avaliando"
                elif placemark.find("Point"):
                    name = "Ponto Sem Nome"
                else:
                    name = "Elemento"

            coord_tag = placemark.find("coordinates")

            if coord_tag:
                raw_coords = coord_tag.text.strip().split()
                points = []

                for c in raw_coords:
                    parts = c.split(",")

                    if len(parts) >= 2:
                        try:
                            points.append((float(parts[0]), float(parts[1])))
                        except Exception:
                            continue

                geom = None

                if len(points) == 1:
                    geom = Point(points[0])
                elif len(points) > 2:
                    geom = Polygon(points)

                if geom and not geom.is_empty:
                    records.append({
                        "name": name,
                        "geometry": geom
                    })

        if not records:
            return None, "Nenhuma geometria encontrada."

        gdf = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")
        return gdf, None

    except Exception as e:
        return None, f"Erro ao ler arquivo: {str(e)}"


# ============================================================
# 4. RENDERIZAÇÃO
# ============================================================

def render_tab():
    st.markdown("### 🌾 Análise de Aptidão Agrícola (Embrapa)")

    # ------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------
    c1, c2 = st.columns([0.8, 0.2], gap="small", vertical_alignment="bottom")

    with c1:
        uploaded_file = st.file_uploader(
            "Arquivo (KML/KMZ)",
            type=["kml", "kmz", "zip"],
            key="aptidao_upload"
        )

    if uploaded_file:
        file_id = getattr(uploaded_file, "file_id", uploaded_file.name)

        if (
            "last_aptidao_file" not in st.session_state
            or st.session_state["last_aptidao_file"] != file_id
        ):
            st.session_state["last_aptidao_file"] = file_id
            st.session_state["aptidao_concluida"] = False
            st.session_state["aptidao_data"] = None

    if st.session_state.get("aptidao_concluida"):
        with c2:
            if st.button("🔄 Novo", use_container_width=True):
                st.session_state["aptidao_concluida"] = False
                st.session_state["aptidao_data"] = None
                st.rerun()

    if not uploaded_file:
        st.info("Envie um arquivo KML/KMZ/ZIP para iniciar a análise.")
        return

    # ------------------------------------------------------------
    # Lê KML/KMZ
    # ------------------------------------------------------------
    gdf_raw, erro = carregar_kmz_kml_bs4(uploaded_file)

    if erro:
        st.error(erro)
        return

    if gdf_raw is None or gdf_raw.empty:
        st.warning("Arquivo sem geometria válida.")
        return

    centroid_geral = uniao_geometrias(gdf_raw).centroid
    epsg_metro = obter_epsg_por_latlon(centroid_geral.x, centroid_geral.y)

    mask_pontos = gdf_raw.geometry.type == "Point"

    gdf_pontos = gdf_raw[mask_pontos].copy()
    gdf_poligonos_raw = gdf_raw[~mask_pontos].copy()

    if not gdf_poligonos_raw.empty:
        gdf_poligonos_raw["name_lower"] = (
            gdf_poligonos_raw["name"]
            .astype(str)
            .str.lower()
        )

        mask_av = gdf_poligonos_raw["name_lower"].str.contains(
            "avaliando",
            na=False
        )

        if mask_av.any():
            gdf_perimetro = gdf_poligonos_raw[mask_av].copy()
            gdf_outros = gdf_poligonos_raw[~mask_av].copy()
        else:
            gdf_perimetro = gdf_poligonos_raw.copy()
            gdf_outros = gpd.GeoDataFrame(
                columns=gdf_poligonos_raw.columns,
                geometry="geometry",
                crs=gdf_raw.crs
            )
    else:
        gdf_perimetro = gpd.GeoDataFrame(
            columns=gdf_raw.columns,
            geometry="geometry",
            crs=gdf_raw.crs
        )
        gdf_outros = gpd.GeoDataFrame(
            columns=gdf_raw.columns,
            geometry="geometry",
            crs=gdf_raw.crs
        )

    # ------------------------------------------------------------
    # Mapa base
    # ------------------------------------------------------------
    m = folium.Map(
        location=[centroid_geral.y, centroid_geral.x],
        zoom_start=13,
        tiles=None
    )

    folium.TileLayer(
        "https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
        attr="Google",
        name="Satélite"
    ).add_to(m)

    # Enquadra o mapa na extensão real do imóvel (em vez de zoom fixo).
    try:
        minx, miny, maxx, maxy = gdf_raw.total_bounds
        m.fit_bounds([[miny, minx], [maxy, maxx]])
    except Exception:
        pass

    # ============================================================
    # PRIMEIRO: CAMADA DE APTIDÃO COMO FUNDO
    # ============================================================
    if st.session_state.get("aptidao_concluida") and st.session_state.get("aptidao_data"):
        data = st.session_state["aptidao_data"]

        if data.get("visual") is not None and not data["visual"].empty:
            gdf_visual = data["visual"].copy()

            fields_tooltip = [
                c for c in ["simb_apt", "classe_norm", "legenda_curta"]
                if c in gdf_visual.columns
            ]

            aliases_tooltip = {
                "simb_apt": "Aptidão:",
                "classe_norm": "Classe:",
                "legenda_curta": "Descrição:"
            }

            tooltip_apt = None
            if fields_tooltip:
                tooltip_apt = folium.GeoJsonTooltip(
                    fields=fields_tooltip,
                    aliases=[aliases_tooltip.get(c, c) for c in fields_tooltip],
                    sticky=True,
                    style="""
                        background-color: rgba(255, 255, 255, 0.94);
                        border: 1px solid #777;
                        border-radius: 4px;
                        padding: 6px;
                        font-size: 12px;
                        max-width: 260px;
                        white-space: normal;
                    """
                )

            folium.GeoJson(
                gdf_visual,
                name="Aptidão Agrícola",
                style_function=lambda x: {
                    "fillColor": cor_aptidao_por_feature(x),
                    "color": "transparent",
                    "weight": 0,
                    "fillOpacity": opacidade_por_feature(x),
                },
                highlight_function=lambda x: {
                    "weight": 2,
                    "color": "#222222",
                    "fillOpacity": min(opacidade_por_feature(x) + 0.18, 0.75),
                },
                tooltip=tooltip_apt
            ).add_to(m)

            # Legenda só com as classes que de fato aparecem no recorte.
            if "classe_norm" in gdf_visual.columns:
                classes_presentes = set(
                    gdf_visual["classe_norm"].astype(str).str.strip()
                )
            else:
                classes_presentes = set()

            legenda_html = """
            <div style='
                position: fixed;
                bottom: 30px;
                left: 30px;
                width: 210px;
                z-index: 9999;
                background-color: white;
                border: 2px solid gray;
                padding: 10px;
                font-size: 11px;
                box-shadow: 2px 2px 6px rgba(0,0,0,0.3);
                border-radius: 5px;
                opacity: 0.92;'>
                <b>Legenda - Aptidão</b><br>
            """

            for nome, cor in COLOR_MAP_LEGENDA.items():
                if classes_presentes and nome not in classes_presentes:
                    continue
                descricao = NIVEL_APTIDAO.get(nome, nome)
                legenda_html += f"""
                <div style='margin-bottom:3px; line-height:1.25;'>
                    <i style='
                        background:{cor};
                        width:12px;
                        height:12px;
                        display:inline-block;
                        margin-right:6px;
                        border:1px solid #ccc;
                        vertical-align:middle;'></i>{descricao}
                </div>
                """

            legenda_html += "</div>"
            m.get_root().html.add_child(folium.Element(legenda_html))

    # ============================================================
    # DEPOIS: ELEMENTOS DO KML/KMZ POR CIMA, MAS NÃO INTERATIVOS
    # ============================================================
    fg_user = folium.FeatureGroup(name="Elementos do KMZ", overlay=True, show=True)

    if not gdf_perimetro.empty:
        folium.GeoJson(
            gdf_perimetro,
            name="Perímetro",
            style_function=lambda x: {
                "color": "#000000",
                "weight": 4,
                "fillColor": "#FFD700",
                "fillOpacity": 0.10,
                "interactive": False,
            },
            tooltip=None,
        ).add_to(fg_user)

    if not gdf_outros.empty:
        folium.GeoJson(
            gdf_outros,
            name="Outras Áreas",
            style_function=lambda x: {
                "color": "#FF0000",
                "weight": 4,
                "fillColor": "#FF0000",
                "fillOpacity": 0.08,
                "interactive": False,
            },
            tooltip=None,
        ).add_to(fg_user)

    if not gdf_pontos.empty:
        for _, row in gdf_pontos.iterrows():
            nome = row["name"]
            lat, lon = row.geometry.y, row.geometry.x
            cor = "green" if "avaliando" in str(nome).lower() else "red"

            folium.Marker(
                [lat, lon],
                icon=folium.Icon(color=cor, icon="info-sign"),
                interactive=False,
            ).add_to(fg_user)

            folium.map.Marker(
                [lat, lon],
                interactive=False,
                icon=DivIcon(
                    icon_size=(170, 36),
                    icon_anchor=(0, 0),
                    html=f"""
                    <div style="
                        font-size: 11px;
                        font-weight: bold;
                        color: white;
                        text-shadow:
                            -1px -1px 0 #000,
                             1px -1px 0 #000,
                            -1px  1px 0 #000,
                             1px  1px 0 #000;
                        pointer-events: none;">
                        {nome}
                    </div>
                    """
                )
            ).add_to(fg_user)

    fg_user.add_to(m)

    # ------------------------------------------------------------
    # Botão Processar
    # ------------------------------------------------------------
    if not st.session_state.get("aptidao_concluida"):
        with c2:
            if st.button("🚀 Processar", use_container_width=True):
                with st.spinner("Analisando aptidão agrícola..."):
                    try:
                        lista_calc = []

                        if not gdf_perimetro.empty:
                            lista_calc.append(gdf_perimetro.to_crs(epsg=epsg_metro))

                        if not gdf_outros.empty:
                            lista_calc.append(gdf_outros.to_crs(epsg=epsg_metro))

                        if not gdf_pontos.empty:
                            temp = gdf_pontos.to_crs(epsg=epsg_metro)
                            temp["geometry"] = temp.geometry.buffer(40)
                            lista_calc.append(temp)

                        if not lista_calc:
                            st.warning("Arquivo vazio ou sem geometria válida para cálculo.")
                            st.stop()

                        gdf_calculo = gpd.GeoDataFrame(
                            pd.concat(lista_calc, ignore_index=True),
                            geometry="geometry",
                            crs=f"EPSG:{epsg_metro}"
                        )

                        geom_uniao = uniao_geometrias(gdf_calculo)

                        gdf_uniao = gpd.GeoDataFrame(
                            geometry=[geom_uniao],
                            crs=gdf_calculo.crs
                        )

                        # ------------------------------------------------
                        # BBOX IGUAL À LÓGICA ORIGINAL:
                        # usa união dos elementos/buffers + margem.
                        # A camada visual será todo o retorno do WFS.
                        # ------------------------------------------------
                        bounds = gdf_uniao.to_crs(epsg=4326).total_bounds

                        margem = 0.05

                        bbox_str = (
                            f"{bounds[0] - margem},"
                            f"{bounds[1] - margem},"
                            f"{bounds[2] + margem},"
                            f"{bounds[3] + margem}"
                        )

                        gdf_embrapa = consultar_aptidao_embrapa(bbox_str)

                        if gdf_embrapa is None:
                            st.error(
                                "Não foi possível consultar o WFS de aptidão da "
                                "Embrapa (servidor instável). Tente novamente em instantes."
                            )
                            st.stop()

                        area_uniao_ha = float(geom_uniao.area / 10000)

                        if gdf_embrapa.empty:
                            st.warning("Sem dados de aptidão agrícola para a área informada.")
                            st.session_state["aptidao_data"] = {
                                "visual": None,
                                "stats_full": None,
                                "area_uniao_ha": area_uniao_ha,
                            }
                            st.session_state["aptidao_concluida"] = True
                            st.rerun()

                        if gdf_embrapa.crs is None:
                            gdf_embrapa = gdf_embrapa.set_crs(
                                epsg=4326,
                                allow_override=True
                            )

                        # VISUAL: mantém o entorno completo retornado pela Embrapa.
                        gdf_visual = preparar_visual_embrapa(gdf_embrapa)

                        # ESTATÍSTICA: interseção com os elementos/buffers.
                        gdf_embrapa_utm = gdf_embrapa.to_crs(epsg=epsg_metro)

                        gdf_intersect = gpd.overlay(
                            gdf_embrapa_utm,
                            gdf_uniao,
                            how="intersection",
                            keep_geom_type=False
                        )

                        stats_full = None

                        if not gdf_intersect.empty:
                            gdf_intersect["area_ha"] = gdf_intersect.geometry.area / 10000

                            if "legenda_ap" in gdf_intersect.columns:
                                gdf_intersect["legenda_ap"] = (
                                    gdf_intersect["legenda_ap"]
                                    .astype(str)
                                    .str.replace(r"\.", "", regex=True)
                                    .str.strip()
                                )
                            else:
                                gdf_intersect["legenda_ap"] = "Sem Inf."

                            if "simb_apt" in gdf_intersect.columns:
                                gdf_intersect["simb_apt"] = (
                                    gdf_intersect["simb_apt"]
                                    .astype(str)
                                    .str.replace(r"\.", "", regex=True)
                                    .str.strip()
                                )
                            else:
                                gdf_intersect["simb_apt"] = "---"

                            gdf_intersect["classe_norm"] = (
                                gdf_intersect["legenda_ap"]
                                .apply(normalizar_classe_embrapa)
                            )

                            gdf_intersect["simb_apt"] = gdf_intersect.apply(
                                corrigir_sigla_reforcada,
                                axis=1
                            )

                            # Mantém TODAS as classes (inclusive restrições e
                            # 'Sem Inf.'); a separação acontece na exibição, para
                            # que o denominador de % seja a área do imóvel.
                            stats_full = (
                                gdf_intersect
                                .groupby(["legenda_ap", "classe_norm", "simb_apt"])["area_ha"]
                                .sum()
                                .reset_index()
                            )
                            stats_full = stats_full[stats_full["area_ha"] > 0.001].copy()
                            stats_full = stats_full.sort_values(
                                "area_ha", ascending=False
                            ).reset_index(drop=True)

                            if stats_full.empty:
                                stats_full = None
                        else:
                            st.warning("A área não apresentou interseção com a base de aptidão agrícola.")

                        st.session_state["aptidao_data"] = {
                            "visual": gdf_visual,
                            "stats_full": stats_full,
                            "area_uniao_ha": area_uniao_ha,
                        }

                        st.session_state["aptidao_concluida"] = True
                        st.rerun()

                    except Exception as e:
                        st.error(f"Erro ao processar aptidão agrícola: {e}")

    # ------------------------------------------------------------
    # Renderização do mapa
    # ------------------------------------------------------------
    folium.LayerControl().add_to(m)

    st_folium(
        m,
        height=550,
        use_container_width=True,
        returned_objects=[],
        key="mapa_aptidao"
    )

    # ------------------------------------------------------------
    # Resultados
    # ------------------------------------------------------------
    if st.session_state.get("aptidao_concluida") and st.session_state.get("aptidao_data"):
        data = st.session_state["aptidao_data"]
        stats_full = data.get("stats_full")
        area_uniao_ha = data.get("area_uniao_ha") or 0.0

        def _pct(valor):
            return (valor / area_uniao_ha * 100) if area_uniao_ha > 0 else 0.0

        if stats_full is not None and not stats_full.empty:
            sf = stats_full.copy()
            sf["classe_norm"] = sf["classe_norm"].astype(str).str.strip()

            # Separa em: classes de aptidão (1-6) e restrições/sobreposições.
            # O que sobra da área do imóvel é tratado como "sem informação".
            agri = sf[sf["classe_norm"].isin(CLASSES_APTIDAO)].copy()
            restr = sf[sf["classe_norm"].isin(CLASSES_RESTRICAO)].copy()

            area_classificada = float(sf["area_ha"].sum())
            area_sem_info = max(area_uniao_ha - area_classificada, 0.0)
            area_agricultavel = float(
                agri[agri["classe_norm"].isin(CLASSES_AGRICULTAVEIS)]["area_ha"].sum()
            )

            if not agri.empty:
                agri = agri.sort_values("area_ha", ascending=False).reset_index(drop=True)
                agri["nivel"] = agri["classe_norm"].apply(nivel_aptidao)
                agri["%"] = agri["area_ha"].apply(_pct)

            st.markdown("#### 📊 Resultados da Análise")

            # ---------------- KPIs ----------------
            nivel_pred = nivel_aptidao(agri.iloc[0]["classe_norm"]) if not agri.empty else "—"

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Área analisada", f"{area_uniao_ha:,.1f} ha".replace(",", "."))
            k2.metric(
                "Terras agricultáveis", f"{_pct(area_agricultavel):.0f}%",
                help="Classes 1, 2 e 3 — terras aptas a lavouras."
            )
            k3.metric("Aptidão predominante", nivel_pred)
            k4.metric(
                "Sem informação", f"{_pct(area_sem_info):.0f}%",
                help="Parte da área do imóvel sem cobertura na base de aptidão da Embrapa."
            )

            # ---------------- Restrições / sobreposições ----------------
            if not restr.empty:
                linhas = [
                    f"**{r['classe_norm']}** — {r['area_ha']:.2f} ha ({_pct(r['area_ha']):.1f}%)"
                    for _, r in restr.iterrows()
                ]
                st.warning("⚠️ **Sobreposições detectadas na área:**  \n" + "  \n".join(linhas))

            # ---------------- Gráfico + tabela (classes de aptidão) ----------------
            if not agri.empty:
                c_chart, c_table = st.columns([0.42, 0.58], gap="medium")

                with c_chart:
                    agri_plot = agri.sort_values("area_ha", ascending=True)
                    fig = px.bar(
                        agri_plot,
                        x="area_ha", y="nivel", orientation="h",
                        color="classe_norm",
                        color_discrete_map=COLOR_MAP_LEGENDA,
                    )
                    fig.update_traces(
                        text=[f"{v:.0f}%" for v in agri_plot["%"]],
                        textposition="outside",
                        cliponaxis=False,
                    )
                    fig.update_layout(
                        showlegend=False,
                        height=max(220, 58 * len(agri_plot)),
                        margin=dict(t=10, b=10, l=10, r=40),
                        xaxis_title="Área (ha)", yaxis_title=None,
                    )
                    st.plotly_chart(fig, use_container_width=True)

                with c_table:
                    df_disp = agri[["nivel", "simb_apt", "area_ha", "%", "classe_norm"]].rename(
                        columns={"nivel": "Aptidão", "simb_apt": "Sigla", "area_ha": "Área (ha)"}
                    )
                    total = pd.DataFrame([{
                        "Aptidão": "TOTAL (classes de aptidão)", "Sigla": "",
                        "Área (ha)": float(agri["area_ha"].sum()),
                        "%": float(agri["%"].sum()),
                        "classe_norm": "_total",
                    }])
                    df_disp = pd.concat([df_disp, total], ignore_index=True)

                    def _estilo(row):
                        if str(row["classe_norm"]) == "_total":
                            return ["font-weight:700; background-color:#f0f0f0;"] * len(row)
                        estilos = [""] * len(row)
                        cor = COLOR_MAP_LEGENDA.get(str(row["classe_norm"]))
                        if cor:
                            i = list(row.index).index("Sigla")
                            estilos[i] = (
                                f"background-color:{cor}; color:#000; "
                                "font-weight:600; text-align:center;"
                            )
                        return estilos

                    try:
                        sty = (
                            df_disp.style
                            .apply(_estilo, axis=1)
                            .format({"Área (ha)": "{:.2f}", "%": "{:.1f}%"})
                            .hide(axis="index")
                            .hide(axis="columns", subset=["classe_norm"])
                        )
                        st.dataframe(sty, use_container_width=True)
                    except Exception:
                        st.dataframe(
                            df_disp.drop(columns=["classe_norm"]),
                            hide_index=True, use_container_width=True,
                            column_config={
                                "Área (ha)": st.column_config.NumberColumn(format="%.2f"),
                                "%": st.column_config.NumberColumn(format="%.1f%%"),
                            },
                        )

            # ---------------- Redação para laudo ----------------
            partes = []
            if not agri.empty:
                cp = agri.iloc[0]
                partes.append(
                    f"A área analisada ({area_uniao_ha:.1f} ha) apresenta predominância de terras "
                    f"com aptidão \"{nivel_aptidao(cp['classe_norm']).lower()}\" "
                    f"({cp['%']:.0f}% da área)."
                )
                partes.append(
                    f"As terras agricultáveis (classes 1 a 3) somam {_pct(area_agricultavel):.0f}% da área."
                )
            if not restr.empty:
                nomes_r = ", ".join(sorted(set(restr["classe_norm"])))
                partes.append(f"Foram identificadas sobreposições com: {nomes_r}.")
            if _pct(area_sem_info) >= 1:
                partes.append(
                    f"Cerca de {_pct(area_sem_info):.0f}% da área não possui cobertura "
                    "na base de aptidão (sem informação)."
                )
            if partes:
                with st.expander("📝 Redação para laudo"):
                    st.write(" ".join(partes))

            # ---------------- Downloads ----------------
            if not agri.empty:
                export = agri[["classe_norm", "nivel", "simb_apt", "legenda_ap", "area_ha", "%"]].rename(
                    columns={
                        "classe_norm": "Classe", "nivel": "Aptidão", "simb_apt": "Sigla",
                        "legenda_ap": "Descrição", "area_ha": "Área (ha)",
                    }
                )
                cdl1, cdl2 = st.columns(2)
                with cdl1:
                    st.download_button(
                        "📥 Tabela (CSV)",
                        export.to_csv(index=False).encode("utf-8-sig"),
                        file_name="aptidao_agricola.csv", mime="text/csv",
                        use_container_width=True,
                    )
                with cdl2:
                    buf = io.BytesIO()
                    with pd.ExcelWriter(buf, engine="xlsxwriter") as wr:
                        export.to_excel(wr, index=False, sheet_name="Aptidao")
                    st.download_button(
                        "📥 Tabela (Excel)",
                        buf.getvalue(), file_name="aptidao_agricola.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )

        else:
            st.info("Nenhuma classe de aptidão agrícola significativa foi identificada dentro dos elementos analisados.")