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
import bs4
import requests

# --- 1. CONFIGURAÇÕES E CORES ---
COLOR_MAP_LEGENDA = {
    "1": "#AAFF00", "2": "#D7B09E", "3": "#FFAA00", 
    "4": "#FFFFBC", "5": "#FF74E5", "6": "#D1D1D1",
    "Terra indígena": "#611F05", "Unidade de conservação": "#234D27", 
    "Corpos d'água": "#61D6FF", "Área urbana": "#FF0000",
    "Sem Inf.": "#DDDDDD"
}

# --- 2. FUNÇÕES DE PARSER ---

def obter_epsg_por_latlon(lon, lat):
    """Calcula UTM local."""
    zona = int((lon + 180) / 6) + 1
    return 32700 + zona if lat < 0 else 32600 + zona

def carregar_kmz_kml_bs4(uploaded_file):
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(uploaded_file.name)[1]) as tmp:
            tmp.write(uploaded_file.getbuffer())
            tmp_path = tmp.name

        kml_content = ""
        if zipfile.is_zipfile(tmp_path):
            with zipfile.ZipFile(tmp_path, "r") as kmz:
                kmls = [f for f in kmz.namelist() if f.endswith(".kml")]
                if not kmls: raise ValueError("Nenhum .kml encontrado no KMZ")
                target = "doc.kml" if "doc.kml" in kmz.namelist() else kmls[0]
                with kmz.open(target) as f:
                    kml_content = f.read().decode("utf-8")
        else:
            with open(tmp_path, "r", encoding="utf-8") as f:
                kml_content = f.read()

        soup = bs4.BeautifulSoup(kml_content, "xml")
        records = []
        placemarks = soup.find_all("Placemark")
        
        for placemark in placemarks:
            name_tag = placemark.find("name")
            name = name_tag.text.strip() if name_tag else ""
            
            if not name:
                if placemark.find("Polygon"): name = "Avaliando"
                elif placemark.find("Point"): name = "Ponto Sem Nome"
                else: name = "Elemento"

            coord_tag = placemark.find("coordinates")
            if coord_tag:
                raw_coords = coord_tag.text.strip().split()
                points = []
                for c in raw_coords:
                    parts = c.split(",")
                    if len(parts) >= 2:
                        points.append((float(parts[0]), float(parts[1])))
                
                geom = None
                if len(points) == 1:
                    geom = Point(points[0])
                elif len(points) > 2:
                    geom = Polygon(points)
                
                if geom:
                    records.append({"name": name, "geometry": geom})

        if not records: return None, "Nenhuma geometria encontrada."
        return gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326"), None

    except Exception as e:
        return None, f"Erro ao ler arquivo: {str(e)}"

def normalizar_classe_embrapa(txt):
    """Normaliza o texto longo da Embrapa para a chave curta de cor."""
    txt = str(txt).strip()
    for k in COLOR_MAP_LEGENDA.keys():
        if txt.startswith(k): return k
    if txt.lower() in ['none', 'nan', '']: return "Sem Inf."
    return txt

def get_color_embrapa(txt):
    key = normalizar_classe_embrapa(txt)
    return COLOR_MAP_LEGENDA.get(key, "#DDDDDD")

# --- 3. RENDERIZAÇÃO ---
def render_tab():
    st.markdown("### 🌾 Análise de Aptidão Agrícola (Embrapa)")

    # UPLOAD
    c1, c2 = st.columns([0.8, 0.2], gap="small", vertical_alignment="bottom")
    with c1:
        uploaded_file = st.file_uploader("Arquivo (KML/KMZ)", type=["kml", "kmz", "zip"], key="aptidao_upload")

    if uploaded_file:
        file_id = uploaded_file.file_id
        if 'last_aptidao_file' not in st.session_state or st.session_state['last_aptidao_file'] != file_id:
            st.session_state['last_aptidao_file'] = file_id
            st.session_state['aptidao_concluida'] = False
            st.session_state['aptidao_data'] = None

    if st.session_state.get('aptidao_concluida'):
        with c2:
            if st.button("🔄 Novo", use_container_width=True):
                st.session_state['aptidao_concluida'] = False
                st.session_state['aptidao_data'] = None
                st.rerun()

    if uploaded_file:
        gdf_raw, erro = carregar_kmz_kml_bs4(uploaded_file)
        if erro:
            st.error(erro)
            return

        if gdf_raw is not None and not gdf_raw.empty:
            
            centroid_geral = gdf_raw.unary_union.centroid
            epsg_metro = obter_epsg_por_latlon(centroid_geral.x, centroid_geral.y)

            mask_pontos = gdf_raw.geometry.type == 'Point'
            gdf_pontos = gdf_raw[mask_pontos].copy()
            gdf_poligonos_raw = gdf_raw[~mask_pontos].copy()
            
            if not gdf_poligonos_raw.empty:
                gdf_poligonos_raw['name_lower'] = gdf_poligonos_raw['name'].astype(str).str.lower()
                mask_av = gdf_poligonos_raw['name_lower'].str.contains("avaliando")
                if mask_av.any():
                    gdf_perimetro = gdf_poligonos_raw[mask_av].copy()
                    gdf_outros = gdf_poligonos_raw[~mask_av].copy()
                else:
                    gdf_perimetro = gdf_poligonos_raw.copy()
                    gdf_outros = gpd.GeoDataFrame()
            else:
                gdf_perimetro = gpd.GeoDataFrame()
                gdf_outros = gpd.GeoDataFrame()

            m = folium.Map(location=[centroid_geral.y, centroid_geral.x], zoom_start=13, tiles=None)
            folium.TileLayer("https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}", attr="Google", name="Satélite").add_to(m)
            fg_user = folium.FeatureGroup(name="Elementos do KMZ")
            
            if not gdf_perimetro.empty:
                folium.GeoJson(gdf_perimetro, name="Perímetro", style_function=lambda x: {'color': 'black', 'weight': 3, 'fillColor': '#FFD700', 'fillOpacity': 0.1}, tooltip="Perímetro").add_to(fg_user)

            if not gdf_outros.empty:
                folium.GeoJson(gdf_outros, name="Outras Áreas", style_function=lambda x: {'color': 'red', 'weight': 2, 'fillColor': 'none'}, tooltip="Elemento").add_to(fg_user)

            if not gdf_pontos.empty:
                for idx, row in gdf_pontos.iterrows():
                    nome = row['name']
                    lat, lon = row.geometry.y, row.geometry.x
                    cor = "green" if "avaliando" in str(nome).lower() else "red"
                    folium.Marker([lat, lon], popup=nome, icon=folium.Icon(color=cor, icon='info-sign')).add_to(fg_user)
                    folium.map.Marker([lat, lon], icon=DivIcon(icon_size=(150,36), icon_anchor=(0,0), html=f'<div style="font-size: 11px; font-weight: bold; color: white; text-shadow: -1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000, 1px 1px 0 #000;">{nome}</div>')).add_to(fg_user)

            fg_user.add_to(m)

            if st.session_state.get('aptidao_concluida') and st.session_state.get('aptidao_data'):
                data = st.session_state['aptidao_data']
                stats = data['stats']
                
                if data.get('visual') is not None:
                    folium.GeoJson(
                        data['visual'],
                        name="Aptidão Agrícola",
                        style_function=lambda x: {
                            'fillColor': get_color_embrapa(x['properties'].get('legenda_ap', '')),
                            'color': 'none', 'fillOpacity': 0.6
                        },
                        tooltip=folium.GeoJsonTooltip(fields=['simb_apt', 'legenda_ap'])
                    ).add_to(m, index=0)

                    legenda_html = "<div style='position: fixed; bottom: 30px; left: 30px; width: 180px; z-index: 9999; background-color: white; border:2px solid gray; padding: 10px; font-size: 12px; box-shadow: 2px 2px 6px rgba(0,0,0,0.3); border-radius: 5px; opacity: 0.9;'><b>Legenda - Aptidão</b><br>"
                    for nome, cor in COLOR_MAP_LEGENDA.items():
                        legenda_html += f"<div style='margin-bottom:3px;'><i style='background:{cor};width:12px;height:12px;display:inline-block;margin-right:6px;border:1px solid #ccc;'></i>{nome}</div>"
                    legenda_html += "</div>"
                    m.get_root().html.add_child(folium.Element(legenda_html))

            else:
                with c2:
                    if st.button("🚀 Processar", type="primary", use_container_width=True):
                        with st.spinner("Analisando aptidão..."):
                            try:
                                lista_calc = []
                                if not gdf_perimetro.empty: lista_calc.append(gdf_perimetro.to_crs(epsg=epsg_metro))
                                if not gdf_outros.empty: lista_calc.append(gdf_outros.to_crs(epsg=epsg_metro))
                                if not gdf_pontos.empty:
                                    temp = gdf_pontos.to_crs(epsg=epsg_metro)
                                    temp['geometry'] = temp.geometry.buffer(40)
                                    lista_calc.append(temp.to_crs(epsg=4326).to_crs(epsg=epsg_metro))

                                if not lista_calc:
                                    st.warning("Arquivo vazio.")
                                    st.stop()

                                gdf_calculo = pd.concat(lista_calc, ignore_index=True)
                                geom_uniao = gdf_calculo.unary_union
                                gdf_uniao = gpd.GeoDataFrame(geometry=[geom_uniao], crs=gdf_calculo.crs)
                                
                                bounds = gdf_uniao.to_crs(epsg=4326).total_bounds
                                margem = 0.05
                                bbox_str = f"{bounds[0]-margem},{bounds[1]-margem},{bounds[2]+margem},{bounds[3]+margem}"
                                wfs_url = f"https://geoinfo.dados.embrapa.br/geoserver/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=geonode:aptagr_bra&bbox={bbox_str},EPSG:4326&outputFormat=application/json"
                                
                                gdf_embrapa = gpd.read_file(wfs_url)
                                
                                if gdf_embrapa.empty:
                                    st.warning("Sem dados.")
                                else:
                                    gdf_embrapa_utm = gdf_embrapa.to_crs(epsg=epsg_metro)
                                    gdf_intersect = gpd.overlay(gdf_embrapa_utm, gdf_uniao, how='intersection')
                                    
                                    stats = None
                                    if not gdf_intersect.empty:
                                        gdf_intersect["area_ha"] = gdf_intersect.geometry.area / 10000
                                        
                                        # Limpa strings
                                        gdf_intersect["legenda_ap"] = gdf_intersect["legenda_ap"].astype(str).str.replace(r"\.", "", regex=True).str.strip()
                                        gdf_intersect["simb_apt"] = gdf_intersect["simb_apt"].astype(str).str.replace(r"\.", "", regex=True).str.strip()

                                        # 1. Cria Normalização (Para cor)
                                        gdf_intersect["classe_norm"] = gdf_intersect["legenda_ap"].apply(normalizar_classe_embrapa)
                                        
                                        # 2. Corrige Siglas (CORREÇÃO REFORÇADA)
                                        def corrigir_sigla_reforcada(row):
                                            s = str(row['simb_apt']).strip()
                                            c_norm = str(row['classe_norm']).strip().lower()
                                            
                                            # Lista de valores inválidos
                                            invalidos = ['none', 'nan', '', 'null', 'nan']
                                            
                                            # Se a sigla é inválida, força a sigla correta baseada na classe
                                            if s.lower() in invalidos:
                                                if 'unidade de conservação' in c_norm: return 'UC'
                                                if 'terra indígena' in c_norm: return 'TI'
                                                if 'corpos d' in c_norm: return 'Água'
                                                if 'área urbana' in c_norm or 'area urbana' in c_norm: return 'Urb'
                                                if 'sem inf' in c_norm: return '---'
                                            
                                            return s
                                        
                                        gdf_intersect["simb_apt"] = gdf_intersect.apply(corrigir_sigla_reforcada, axis=1)

                                        # 3. Agrupamento
                                        stats = gdf_intersect.groupby(["legenda_ap", "classe_norm", "simb_apt"])["area_ha"].sum().reset_index()
                                        
                                        # Filtro de Sujeira
                                        stats = stats[~stats["classe_norm"].isin(["None", "nan", "", "Sem Inf."])]
                                        stats = stats[stats["area_ha"] > 0.001]

                                        if not stats.empty:
                                            stats['%'] = (stats['area_ha'] / stats['area_ha'].sum()) * 100
                                            stats = stats.sort_values('area_ha', ascending=False)
                                        else:
                                            st.warning("Área intersectada é insignificante.")
                                            stats = None

                                    st.session_state['aptidao_data'] = {
                                        'visual': gdf_embrapa.to_crs(epsg=4326),
                                        'stats': stats
                                    }
                                    st.session_state['aptidao_concluida'] = True
                                    st.rerun()
                            except Exception as e:
                                st.error(f"Erro: {e}")

            folium.LayerControl().add_to(m)
            st_folium(m, height=550, use_container_width=True)

            if st.session_state.get('aptidao_concluida') and st.session_state.get('aptidao_data'):
                stats = st.session_state['aptidao_data']['stats']
                if stats is not None and not stats.empty:
                    
                    # PATCH PARA DADOS ANTIGOS
                    if 'classe_norm' not in stats.columns:
                        if 'legenda_ap' in stats.columns:
                            stats['classe_norm'] = stats['legenda_ap'].apply(normalizar_classe_embrapa)
                        else:
                            col_0 = stats.columns[0]
                            stats['classe_norm'] = stats[col_0].apply(normalizar_classe_embrapa)
                            stats['legenda_ap'] = stats[col_0]

                    st.markdown("#### 📊 Resultados")
                    c_chart, c_table = st.columns([0.4, 0.6], gap="medium")
                    
                    with c_chart:
                        # Gráfico usa simb_apt (agora corrigido para UC) como label
                        fig = px.pie(
                            stats, values='area_ha', names='simb_apt', 
                            color='classe_norm', 
                            color_discrete_map=COLOR_MAP_LEGENDA,
                            hole=0.4
                        )
                        fig.update_traces(textposition='outside', textinfo='label+percent', pull=[0.05]*len(stats))
                        fig.update_layout(showlegend=False, margin=dict(t=20, b=20, l=20, r=20), height=300)
                        st.plotly_chart(fig, use_container_width=True)
                    
                    with c_table:
                        # Renomeia para exibição
                        df_show = stats.rename(columns={"legenda_ap": "Classe", "simb_apt": "Sigla", "area_ha": "Área (ha)"})
                        
                        cols_to_show = ["Classe", "Sigla", "Área (ha)", "%"]
                        cols_final = [c for c in cols_to_show if c in df_show.columns]
                        df_show = df_show[cols_final]
                        
                        st.dataframe(
                            df_show,
                            column_config={
                                "Classe": st.column_config.TextColumn("Classe", width="medium"),
                                "Sigla": st.column_config.TextColumn("Sigla", width="small"),
                                "Área (ha)": st.column_config.NumberColumn("Área (ha)", format="%.4f"),
                                "%": st.column_config.NumberColumn("%", format="%.2f%%")
                            },
                            use_container_width=True, hide_index=True
                        )