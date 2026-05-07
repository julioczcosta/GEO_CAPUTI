import streamlit as st
import geopandas as gpd
import folium
from folium.features import DivIcon
from streamlit_folium import st_folium
import plotly.express as px
from shapely.geometry import Polygon, Point, box
import pandas as pd
import tempfile
import zipfile
import os
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


# ============================================================
# 2. FUNÇÕES AUXILIARES
# ============================================================

def obter_epsg_por_latlon(lon, lat):
    """
    Calcula o EPSG UTM local.
    Hemisfério Sul: EPSG 327XX
    Hemisfério Norte: EPSG 326XX
    """
    zona = int((lon + 180) / 6) + 1
    return 32700 + zona if lat < 0 else 32600 + zona


def uniao_geometrias(gdf):
    """
    Une geometrias usando union_all() quando disponível.
    Mantém compatibilidade com versões anteriores.
    """
    try:
        return gdf.geometry.union_all()
    except Exception:
        return gdf.unary_union


def normalizar_classe_embrapa(txt):
    """
    Normaliza a legenda da Embrapa para a classe usada na paleta.
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
    """
    Retorna a cor da classe da Embrapa.
    """
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


def aplicar_fit_bounds(mapa, bounds, margem=0.02):
    """
    Ajusta o mapa para enquadrar os limites informados.
    """
    try:
        minx, miny, maxx, maxy = bounds

        if minx == maxx:
            minx -= margem
            maxx += margem

        if miny == maxy:
            miny -= margem
            maxy += margem

        mapa.fit_bounds([[miny, minx], [maxy, maxx]])
    except Exception:
        pass


def preparar_visualizacao_entorno(gdf_embrapa, gdf_bbox_visual, epsg_metro):
    """
    Prepara a camada visual do ENTORNO:
    - usa toda a base Embrapa retornada pelo bbox visual;
    - recorta pelo retângulo do entorno;
    - NÃO recorta pelos buffers/pontos;
    - mantém colunas necessárias;
    - simplifica geometria;
    - retorna em EPSG:4326.
    """
    if gdf_embrapa is None or gdf_embrapa.empty:
        return None

    gdf_visual = gdf_embrapa.copy()

    if gdf_visual.crs is None:
        gdf_visual = gdf_visual.set_crs(epsg=4326, allow_override=True)

    try:
        gdf_visual_utm = gdf_visual.to_crs(epsg=epsg_metro)
        gdf_bbox_utm = gdf_bbox_visual.to_crs(epsg=epsg_metro)

        gdf_visual = gpd.overlay(
            gdf_visual_utm,
            gdf_bbox_utm,
            how="intersection",
            keep_geom_type=False
        )

    except Exception:
        gdf_visual = gdf_visual.to_crs(epsg=epsg_metro)

    if gdf_visual.empty:
        return None

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

    cols_visuais = [
        c for c in ["simb_apt", "legenda_ap", "classe_norm", "geometry"]
        if c in gdf_visual.columns
    ]

    gdf_visual = gdf_visual[cols_visuais].copy()

    try:
        gdf_visual["geometry"] = gdf_visual.geometry.simplify(
            tolerance=20,
            preserve_topology=True
        )
    except Exception:
        pass

    gdf_visual = gdf_visual[
        gdf_visual.geometry.notna() & ~gdf_visual.geometry.is_empty
    ].copy()

    if gdf_visual.empty:
        return None

    return gdf_visual.to_crs(epsg=4326)


# ============================================================
# 3. PARSER KML/KMZ
# ============================================================

def carregar_kmz_kml_bs4(uploaded_file):
    """
    Lê KML/KMZ/ZIP usando BeautifulSoup.
    Retorna GeoDataFrame em EPSG:4326.
    """
    try:
        suffix = os.path.splitext(uploaded_file.name)[1]

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded_file.getbuffer())
            tmp_path = tmp.name

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
# 4. RENDERIZAÇÃO DA ABA
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
    # Leitura do KML/KMZ
    # ------------------------------------------------------------
    gdf_raw, erro = carregar_kmz_kml_bs4(uploaded_file)

    if erro:
        st.error(erro)
        return

    if gdf_raw is None or gdf_raw.empty:
        st.warning("Arquivo sem geometria válida.")
        return

    # ------------------------------------------------------------
    # Preparação das geometrias do usuário
    # ------------------------------------------------------------
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

        mask_av = gdf_poligonos_raw["name_lower"].str.contains("avaliando", na=False)

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
        zoom_start=10,
        tiles=None
    )

    folium.TileLayer(
        "https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
        attr="Google",
        name="Satélite"
    ).add_to(m)

    bounds_mapa = gdf_raw.total_bounds

    fg_user = folium.FeatureGroup(name="Elementos do KMZ")

    if not gdf_perimetro.empty:
        folium.GeoJson(
            gdf_perimetro,
            name="Perímetro",
            style_function=lambda x: {
                "color": "black",
                "weight": 3,
                "fillColor": "#FFD700",
                "fillOpacity": 0.1
            },
            tooltip="Perímetro"
        ).add_to(fg_user)

    if not gdf_outros.empty:
        folium.GeoJson(
            gdf_outros,
            name="Outras Áreas",
            style_function=lambda x: {
                "color": "red",
                "weight": 2,
                "fillColor": "none"
            },
            tooltip="Elemento"
        ).add_to(fg_user)

    if not gdf_pontos.empty:
        for _, row in gdf_pontos.iterrows():
            nome = row["name"]
            lat, lon = row.geometry.y, row.geometry.x
            cor = "green" if "avaliando" in str(nome).lower() else "red"

            folium.Marker(
                [lat, lon],
                popup=nome,
                icon=folium.Icon(color=cor, icon="info-sign")
            ).add_to(fg_user)

            folium.map.Marker(
                [lat, lon],
                icon=DivIcon(
                    icon_size=(150, 36),
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
                             1px  1px 0 #000;">
                        {nome}
                    </div>
                    """
                )
            ).add_to(fg_user)

    fg_user.add_to(m)

    # ------------------------------------------------------------
    # Se já processou, adiciona camada da aptidão do entorno
    # ------------------------------------------------------------
    if st.session_state.get("aptidao_concluida") and st.session_state.get("aptidao_data"):
        data = st.session_state["aptidao_data"]

        if data.get("visual") is not None and not data["visual"].empty:
            gdf_visual = data["visual"].copy()
            bounds_mapa = gdf_visual.total_bounds

            if "classe_norm" not in gdf_visual.columns:
                if "legenda_ap" in gdf_visual.columns:
                    gdf_visual["classe_norm"] = gdf_visual["legenda_ap"].apply(
                        normalizar_classe_embrapa
                    )
                else:
                    gdf_visual["classe_norm"] = "Sem Inf."

            if "legenda_ap" in gdf_visual.columns:
                mask_sem_inf = (
                    gdf_visual["classe_norm"]
                    .astype(str)
                    .str.lower()
                    .isin(["sem inf.", "sem inf", "none", "nan", "", "<na>", "null"])
                )

                gdf_visual.loc[mask_sem_inf, "classe_norm"] = (
                    gdf_visual.loc[mask_sem_inf, "legenda_ap"]
                    .apply(normalizar_classe_embrapa)
                )

                gdf_visual["legenda_curta"] = (
                    gdf_visual["legenda_ap"]
                    .astype(str)
                    .str.slice(0, 70)
                )
            else:
                gdf_visual["legenda_curta"] = ""

            def cor_aptidao(feature):
                props = feature.get("properties", {})

                classe = props.get("classe_norm", "Sem Inf.")
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

            tooltip_apt = None

            if len(gdf_visual) <= 100:
                fields_tooltip = [
                    c for c in ["simb_apt", "classe_norm", "legenda_curta"]
                    if c in gdf_visual.columns
                ]

                aliases_tooltip = {
                    "simb_apt": "Aptidão:",
                    "classe_norm": "Classe:",
                    "legenda_curta": "Descrição:"
                }

                if fields_tooltip:
                    tooltip_apt = folium.GeoJsonTooltip(
                        fields=fields_tooltip,
                        aliases=[aliases_tooltip.get(c, c) for c in fields_tooltip],
                        sticky=False,
                        style="""
                            background-color: rgba(255, 255, 255, 0.92);
                            border: 1px solid #999;
                            border-radius: 4px;
                            padding: 6px;
                            font-size: 12px;
                            max-width: 260px;
                            white-space: normal;
                        """
                    )

            folium.GeoJson(
                gdf_visual,
                name="Aptidão Agrícola - Entorno",
                style_function=lambda x: {
                    "fillColor": cor_aptidao(x),
                    "color": "none",
                    "fillOpacity": 0.6
                },
                tooltip=tooltip_apt
            ).add_to(m)

            legenda_html = """
            <div style='
                position: fixed;
                bottom: 30px;
                left: 30px;
                width: 190px;
                z-index: 9999;
                background-color: white;
                border: 2px solid gray;
                padding: 10px;
                font-size: 12px;
                box-shadow: 2px 2px 6px rgba(0,0,0,0.3);
                border-radius: 5px;
                opacity: 0.9;'>
                <b>Legenda - Aptidão</b><br>
            """

            for nome, cor in COLOR_MAP_LEGENDA.items():
                legenda_html += f"""
                <div style='margin-bottom:3px;'>
                    <i style='
                        background:{cor};
                        width:12px;
                        height:12px;
                        display:inline-block;
                        margin-right:6px;
                        border:1px solid #ccc;'></i>{nome}
                </div>
                """

            legenda_html += "</div>"
            m.get_root().html.add_child(folium.Element(legenda_html))

    # ------------------------------------------------------------
    # Botão Processar
    # ------------------------------------------------------------
    else:
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

                        # ------------------------------------------------
                        # GEOMETRIA DE CÁLCULO:
                        # usada apenas para tabela e gráfico
                        # ------------------------------------------------
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
                        # GEOMETRIA VISUAL:
                        # usa a extensão bruta do KML/KMZ inteiro
                        # NÃO usa os buffers
                        # ------------------------------------------------
                        bounds_visual = gdf_raw.total_bounds

                        # Margem visual em graus.
                        # 0.15 equivale aproximadamente a 15 km.
                        # Aumente para 0.20 ou 0.30 se quiser mais entorno.
                        margem_visual = 0.15

                        bbox_visual_geom = box(
                            bounds_visual[0] - margem_visual,
                            bounds_visual[1] - margem_visual,
                            bounds_visual[2] + margem_visual,
                            bounds_visual[3] + margem_visual
                        )

                        gdf_bbox_visual = gpd.GeoDataFrame(
                            geometry=[bbox_visual_geom],
                            crs="EPSG:4326"
                        )

                        bbox_bounds = gdf_bbox_visual.total_bounds

                        bbox_str = (
                            f"{bbox_bounds[0]},"
                            f"{bbox_bounds[1]},"
                            f"{bbox_bounds[2]},"
                            f"{bbox_bounds[3]}"
                        )

                        wfs_url = (
                            "https://geoinfo.dados.embrapa.br/geoserver/ows?"
                            "service=WFS&version=1.0.0&request=GetFeature"
                            "&typeName=geonode:aptagr_bra"
                            f"&bbox={bbox_str},EPSG:4326"
                            "&outputFormat=application/json"
                        )

                        gdf_embrapa = gpd.read_file(wfs_url)

                        if gdf_embrapa.empty:
                            st.warning("Sem dados de aptidão agrícola para a área informada.")
                            st.session_state["aptidao_data"] = {
                                "visual": None,
                                "stats": None
                            }
                            st.session_state["aptidao_concluida"] = True
                            st.rerun()

                        if gdf_embrapa.crs is None:
                            gdf_embrapa = gdf_embrapa.set_crs(
                                epsg=4326,
                                allow_override=True
                            )

                        # ------------------------------------------------
                        # VISUAL: ENTORNO COMPLETO DO BBOX
                        # ------------------------------------------------
                        gdf_visual = preparar_visualizacao_entorno(
                            gdf_embrapa,
                            gdf_bbox_visual,
                            epsg_metro
                        )

                        # ------------------------------------------------
                        # ESTATÍSTICA: APENAS INTERSEÇÃO COM ELEMENTOS
                        # ------------------------------------------------
                        gdf_embrapa_utm = gdf_embrapa.to_crs(epsg=epsg_metro)

                        gdf_intersect = gpd.overlay(
                            gdf_embrapa_utm,
                            gdf_uniao,
                            how="intersection",
                            keep_geom_type=False
                        )

                        stats = None

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

                            stats = (
                                gdf_intersect
                                .groupby(["legenda_ap", "classe_norm", "simb_apt"])["area_ha"]
                                .sum()
                                .reset_index()
                            )

                            mask_validos = ~stats["classe_norm"].astype(str).str.lower().isin(
                                ["none", "nan", "", "sem inf.", "sem inf", "null", "<na>"]
                            )

                            stats = stats[mask_validos].copy()
                            stats = stats[stats["area_ha"] > 0.001].copy()

                            if not stats.empty:
                                stats["%"] = (
                                    stats["area_ha"] / stats["area_ha"].sum()
                                ) * 100

                                stats = stats.sort_values(
                                    "area_ha",
                                    ascending=False
                                ).reset_index(drop=True)
                            else:
                                stats = None

                        else:
                            st.warning("A área não apresentou interseção com a base de aptidão agrícola.")

                        st.session_state["aptidao_data"] = {
                            "visual": gdf_visual,
                            "stats": stats
                        }

                        st.session_state["aptidao_concluida"] = True
                        st.rerun()

                    except Exception as e:
                        st.error(f"Erro ao processar aptidão agrícola: {e}")

    # ------------------------------------------------------------
    # Controle de camadas e renderização do mapa
    # ------------------------------------------------------------
    aplicar_fit_bounds(m, bounds_mapa)

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
        stats = st.session_state["aptidao_data"].get("stats")

        if stats is not None and not stats.empty:
            if "classe_norm" not in stats.columns:
                if "legenda_ap" in stats.columns:
                    stats["classe_norm"] = stats["legenda_ap"].apply(
                        normalizar_classe_embrapa
                    )
                else:
                    col_0 = stats.columns[0]
                    stats["classe_norm"] = stats[col_0].apply(
                        normalizar_classe_embrapa
                    )
                    stats["legenda_ap"] = stats[col_0]

            st.markdown("#### 📊 Resultados da Interseção")

            c_chart, c_table = st.columns([0.4, 0.6], gap="medium")

            with c_chart:
                fig = px.pie(
                    stats,
                    values="area_ha",
                    names="simb_apt",
                    color="classe_norm",
                    color_discrete_map=COLOR_MAP_LEGENDA,
                    hole=0.4
                )

                fig.update_traces(
                    textposition="outside",
                    textinfo="label+percent",
                    pull=[0.05] * len(stats)
                )

                fig.update_layout(
                    showlegend=False,
                    margin=dict(t=20, b=20, l=20, r=20),
                    height=300
                )

                st.plotly_chart(fig, use_container_width=True)

            with c_table:
                df_show = stats.rename(
                    columns={
                        "legenda_ap": "Classe",
                        "simb_apt": "Sigla",
                        "area_ha": "Área Intersectada (ha)"
                    }
                )

                cols_to_show = ["Classe", "Sigla", "Área Intersectada (ha)", "%"]
                cols_final = [c for c in cols_to_show if c in df_show.columns]
                df_show = df_show[cols_final]

                st.dataframe(
                    df_show,
                    column_config={
                        "Classe": st.column_config.TextColumn("Classe", width="medium"),
                        "Sigla": st.column_config.TextColumn("Sigla", width="small"),
                        "Área Intersectada (ha)": st.column_config.NumberColumn(
                            "Área Intersectada (ha)",
                            format="%.4f"
                        ),
                        "%": st.column_config.NumberColumn("%", format="%.2f%%")
                    },
                    use_container_width=True,
                    hide_index=True
                )

        else:
            st.info("Nenhuma classe de aptidão agrícola significativa foi identificada dentro dos elementos analisados.")