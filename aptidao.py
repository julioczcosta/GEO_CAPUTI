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
from shapely import wkb

# --- 1. CONFIGURAÇÕES E CORES ---
COLOR_MAP_LEGENDA = {
    "1": "#AAFF00", "2": "#D7B09E", "3": "#FFAA00", 
    "4": "#FFFFBC", "5": "#FF74E5", "6": "#D1D1D1",
    "Terra indígena": "#611F05", "Unidade de conservação": "#234D27", 
    "Corpos d'água": "#61D6FF", "Área urbana": "#FF0000",
    "Sem Inf.": "#DDDDDD"
}

# --- 2. FUNÇÕES DE PARSER E CORREÇÃO ---

def corrigir_geometrias(gdf):
    """
    Função robusta para limpar geometrias inválidas, remover 3D e consertar topologia.
    Evita o erro GEOSException no unary_union.
    """
    if gdf is None or gdf.empty:
        return gdf

    try:
        # 1. Remove Z (Altitude) forçando 2D
        # KMLs do Google Earth muitas vezes vêm com Z=0 que quebra operações planares
        gdf.geometry = gdf.geometry.apply(
            lambda geom: wkb.loads(wkb.dumps(geom, output_dimension=2)) if geom.has_z else geom
        )
        
        # 2. Explode MultiGeometries (separa polígonos grudados)
        gdf = gdf.explode(index_parts=False).reset_index(drop=True)

        # 3. Corrige topologia (Buffer 0 resolve auto-interseções)
        gdf.geometry = gdf.geometry.buffer(0)

        # 4. Remove geometrias vazias ou nulas
        gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
        
        return gdf
    except Exception as e:
        st.warning(f"Aviso: Tentativa de correção geométrica encontrou um problema, mas seguiremos: {e}")
        return gdf

def obter_epsg_por_latlon(lon, lat):
    """Calcula UTM local."""
    zona = int((lon + 180) / 6) + 1
    return 32700 + zona if lat < 0 else 32600 + zona

def carregar_kmz_kml_bs4(uploaded_file):
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{uploaded_file.name.split('.')[-1]}") as tmp:
            tmp.write(uploaded_file.getvalue())
            tmp.flush()
            
            # Se for KMZ, extrai
            arquivo_ler = tmp.name
            if uploaded_file.name.lower().endswith('.kmz'):
                with zipfile.ZipFile(tmp.name, 'r') as z:
                    kml_filename = [n for n in z.namelist() if n.endswith('.kml')][0]
                    z.extract(kml_filename, os.path.dirname(tmp.name))
                    arquivo_ler = os.path.join(os.path.dirname(tmp.name), kml_filename)
            
            # Lê com BeautifulSoup para robustez
            with open(arquivo_ler, 'r', encoding='utf-8', errors='ignore') as f:
                soup = bs4.BeautifulSoup(f, 'xml')

            data = []
            for placemark in soup.find_all('Placemark'):
                nome = placemark.find('name').text if placemark.find('name') else "Sem Nome"
                coords_str = placemark.find('coordinates')
                
                if coords_str:
                    coords_text = coords_str.text.strip()
                    pontos = []
                    for p in coords_text.split():
                        parts = p.split(',')
                        if len(parts) >= 2:
                            pontos.append((float(parts[0]), float(parts[1])))
                    
                    if len(pontos) > 2:
                        geom = Polygon(pontos)
                        if not geom.is_valid:
                            geom = geom.buffer(0) # Tenta corrigir na leitura
                        data.append({'Name': nome, 'geometry': geom})
            
            if not data:
                return None
                
            gdf = gpd.GeoDataFrame(data, crs="EPSG:4326")
            return gdf

    except Exception as e:
        st.error(f"Erro ao ler KML/KMZ: {e}")
        return None

def processar_shapefile(uploaded_file):
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            with zipfile.ZipFile(uploaded_file, "r") as z:
                z.extractall(tmpdirname)
                
            shp_files = [f for f in os.listdir(tmpdirname) if f.endswith('.shp')]
            if not shp_files:
                return None
            
            gdf = gpd.read_file(os.path.join(tmpdirname, shp_files[0]))
            
            # Converte para WGS84 se necessário
            if gdf.crs != "EPSG:4326":
                gdf = gdf.to_crs("EPSG:4326")
            
            return gdf
    except Exception as e:
        st.error(f"Erro ao ler Shapefile: {e}")
        return None

# --- 3. CONSULTA API ---

@st.cache_data(show_spinner=False)
def consultar_aptidao_api(gdf_imovel):
    """
    Envia o perímetro para API de Aptidão.
    """
    url = "https://f010b91a-745a-4649-8c24-2c3558237e3d-00-2d640989354k.sisko.replit.dev/processar_aptidao"
    
    try:
        # Prepara GeoJSON
        geojson_dict = gdf_imovel.__geo_interface__
        
        response = requests.post(
            url, 
            json=geojson_dict,
            headers={"Content-Type": "application/json"},
            timeout=120
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            return {"erro": f"Status API: {response.status_code} - {response.text}"}
            
    except Exception as e:
        return {"erro": str(e)}

# --- 4. RENDERIZAÇÃO DA ABA ---

def render_tab():
    st.markdown("### 🌾 Consulta de Aptidão Agrícola (SP)")

    # 1. UPLOAD
    uploaded_file = st.file_uploader(
        "Carregue o perímetro do imóvel (KML, KMZ ou SHP Zipado)", 
        type=["kml", "kmz", "zip"]
    )

    if uploaded_file is not None:
        with st.spinner("Lendo arquivo e corrigindo topologia..."):
            if uploaded_file.name.endswith('.zip'):
                gdf_raw = processar_shapefile(uploaded_file)
            else:
                gdf_raw = carregar_kmz_kml_bs4(uploaded_file)
        
        if gdf_raw is not None and not gdf_raw.empty:
            
            # ==================================================
            # 🚑 AQUI ESTÁ A CORREÇÃO DO ERRO GEOSException
            # ==================================================
            gdf_raw = corrigir_geometrias(gdf_raw)
            
            # Cálculo seguro do centroide após a correção
            try:
                centroid_geral = gdf_raw.unary_union.centroid
                lat_centro = centroid_geral.y
                lon_centro = centroid_geral.x
            except Exception as e:
                # Fallback se unary_union ainda falhar (muito raro após a correção)
                # Pega o centroide da primeira geometria válida
                primeira_geom = gdf_raw.geometry.iloc[0]
                lat_centro = primeira_geom.centroid.y
                lon_centro = primeira_geom.centroid.x
                # st.warning("Usando centroide aproximado devido a complexidade geométrica.")

            # --- MAPA DE LOCALIZAÇÃO (Folium) ---
            m = folium.Map(
                location=[lat_centro, lon_centro], 
                zoom_start=12,
                tiles="Esri World Imagery"
            )
            
            # Desenha o perímetro do usuário
            folium.GeoJson(
                gdf_raw,
                style_function=lambda x: {'color': 'white', 'weight': 2, 'fillOpacity': 0.0},
                name="Perímetro Upload"
            ).add_to(m)

            # --- BOTÃO DE CONSULTA ---
            col_act, _ = st.columns([1, 2])
            with col_act:
                # Botão Principal (Verde)
                btn_consulta = st.button("🔍 Consultar Aptidão", type="primary", use_container_width=True)

            if btn_consulta:
                with st.spinner("Processando na API (isso pode levar alguns segundos)..."):
                    resultado = consultar_aptidao_api(gdf_raw)

                if "erro" in resultado:
                    st.error(f"Erro na consulta: {resultado['erro']}")
                else:
                    # --- PROCESSAMENTO DO RESULTADO ---
                    st.success("Consulta realizada com sucesso!")
                    
                    # Converte GeoJSON de resposta de volta para GeoDataFrame
                    features = resultado.get("features", [])
                    if not features:
                        st.warning("Nenhuma intersecção de aptidão encontrada.")
                        st_folium(m, height=400, use_container_width=True)
                        return

                    gdf_res = gpd.GeoDataFrame.from_features(features)
                    
                    # Adiciona cores baseadas na legenda
                    def get_color(feature):
                        legenda = str(feature['properties'].get('legenda_ap', 'Sem Inf.'))
                        return COLOR_MAP_LEGENDA.get(legenda, "#DDDDDD")

                    folium.GeoJson(
                        gdf_res,
                        style_function=lambda x: {
                            'fillColor': get_color(x),
                            'color': 'black',
                            'weight': 0.5,
                            'fillOpacity': 0.6
                        },
                        tooltip=folium.GeoJsonTooltip(
                            fields=['legenda_ap', 'simb_apt', 'area_ha'],
                            aliases=['Classe:', 'Símbolo:', 'Área (ha):'],
                            localize=True
                        ),
                        name="Aptidão Agrícola"
                    ).add_to(m)

                    folium.LayerControl().add_to(m)
                    
                    # Exibe o mapa final com as camadas
                    st_folium(m, height=500, use_container_width=True)

                    # --- ESTATÍSTICAS ---
                    st.markdown("### 📊 Estatísticas de Aptidão")
                    
                    # Agrupa dados
                    if 'area_ha' in gdf_res.columns:
                        stats = gdf_res.groupby(['legenda_ap', 'simb_apt'])['area_ha'].sum().reset_index()
                        stats['%'] = (stats['area_ha'] / stats['area_ha'].sum()) * 100
                        stats = stats.sort_values('area_ha', ascending=False)
                    else:
                        st.warning("Coluna de área não retornada pela API.")
                        return

                    c_chart, c_table = st.columns([1, 1])
                    
                    with c_chart:
                        fig = px.pie(
                            stats, 
                            values='area_ha', 
                            names='legenda_ap', 
                            title='Distribuição de Área',
                            color='legenda_ap',
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
            else:
                # Se não clicou em consultar, mostra só o mapa do perímetro
                st_folium(m, height=400, use_container_width=True)