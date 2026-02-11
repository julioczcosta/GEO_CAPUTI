import streamlit as st
import geopandas as gpd
import pandas as pd
import duckdb
import folium
from streamlit_folium import st_folium
from shapely import wkb
import io
import zipfile
import tempfile
import os

# --- 1. CONFIGURAÇÃO ---
URL_SIGEF = "https://huggingface.co/datasets/julioczcosta/base-incra/resolve/main/sigef_brasil.parquet?download=true"
URL_SNCI = "https://huggingface.co/datasets/julioczcosta/base-incra/resolve/main/snci_brasil.parquet?download=true"

# Mapa Visual
MAPA_VISUAL = {
    'area_display': 'Área (ha)',
    # SIGEF
    'parcela_co': 'Cód. Parcela',
    'codigo_imo': 'Cód. Imóvel',
    'nome_area': 'Nome da Área',
    'status': 'Situação',
    'municipio_': 'Cód. Mun.',
    'registro_m': 'Matrícula',
    'uf_id': 'UF',
    # SNCI
    'num_certif': 'Nº Certificação',
    'cod_imovel': 'Cód. Imóvel',
    'nome_imove': 'Nome do Imóvel',
    'uf_municip': 'Localização'
}

# --- 2. FUNÇÕES AUXILIARES ---

def calcular_area_hectares(gdf):
    """Calcula área em hectares baseada na geometria (apenas para SIGEF)."""
    try:
        if gdf.empty or gdf.geometry.iloc[0] is None: return 0.0
        utm_crs = gdf.estimate_utm_crs()
        gdf_utm = gdf.to_crs(utm_crs)
        area_m2 = gdf_utm.geometry.area.sum()
        return round(area_m2 / 10000, 4)
    except:
        return 0.0

# --- 3. GERADORES DE ARQUIVOS (TEXTO PURO & COORDENADAS SEGURAS) ---

def gerar_kml_perimetro(gdf, codigo):
    try:
        # Cabeçalho KML
        kml = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<kml xmlns="http://www.opengis.net/kml/2.2">',
            '<Document>',
            '<Style id="yellowBorder">',
            '<LineStyle><color>ff00ffff</color><width>4</width></LineStyle>',
            '<PolyStyle><color>00ffffff</color></PolyStyle>',
            '</Style>'
        ]

        for _, row in gdf.iterrows():
            kml.append('<Placemark>')
            kml.append(f'<name>{str(codigo)}</name>')
            kml.append('<styleUrl>#yellowBorder</styleUrl>')

            # --- DESCRIÇÃO ---
            desc_lines = []
            for col in gdf.columns:
                if col == 'geometry': continue
                
                val = row[col]
                val_str = str(val).strip()
                
                if val_str.lower() in ['nan', 'nat', 'none', '', '<na>']:
                    continue
                
                val_str = val_str.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                desc_lines.append(f"<b>{col}:</b> {val_str}")

            full_desc = "<br>".join(desc_lines)
            kml.append(f'<description><![CDATA[{full_desc}]]></description>')

            # --- GEOMETRIA ---
            geom = row.geometry
            if geom and not geom.is_empty:
                kml.append('<MultiGeometry>')
                
                if geom.geom_type == 'Polygon':
                    polys = [geom]
                elif geom.geom_type == 'MultiPolygon':
                    polys = geom.geoms
                else:
                    polys = []

                for poly in polys:
                    if poly.is_empty: continue
                    
                    coords_str_list = []
                    # CORREÇÃO DO ERRO AQUI:
                    # Em vez de "for x, y in...", pegamos a tupla inteira "point"
                    for point in poly.exterior.coords:
                        # Pegamos sempre os dois primeiros valores (X, Y)
                        x, y = point[0], point[1]
                        coords_str_list.append(f"{x},{y},0")
                    
                    coords_str = " ".join(coords_str_list)
                    
                    kml.append('<Polygon><outerBoundaryIs><LinearRing><coordinates>')
                    kml.append(coords_str)
                    kml.append('</coordinates></LinearRing></outerBoundaryIs></Polygon>')
                
                kml.append('</MultiGeometry>')

            kml.append('</Placemark>')

        kml.append('</Document></kml>')
        return "\n".join(kml).encode('utf-8')

    except Exception as e:
        return f"ERRO: {str(e)}"

def gerar_shp_perimetro(gdf, codigo):
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            clean_gdf = gdf.copy()
            for col in clean_gdf.columns:
                if col != 'geometry':
                    clean_gdf[col] = clean_gdf[col].astype(str).replace({'NaT': '', 'nan': '', 'None': ''})

            safe_code = str(codigo).replace("/", "_").replace(".", "").replace(" ", "_")[:15]
            clean_gdf.to_file(os.path.join(tmpdirname, f"INCRA_{safe_code}.shp"), driver='ESRI Shapefile')
            
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(tmpdirname):
                    for file in files:
                        file_path = os.path.join(root, file)
                        zf.write(file_path, arcname=file)
            return zip_buffer.getvalue()
    except Exception: return None

# --- 4. BUSCA ---
def buscar_imovel_especifico(filtros_sql, url_parquet, eh_sigef=True):
    try:
        con = duckdb.connect(database=':memory:')
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute("SET http_keep_alive=false;")
        
        where_clause = " OR ".join(filtros_sql)
        query = f"""
            WITH tabela_bruta AS (SELECT * FROM read_parquet('{url_parquet}'))
            SELECT * FROM tabela_bruta WHERE {where_clause} LIMIT 50
        """
        df = con.execute(query).df()
        
        if df.empty: return gpd.GeoDataFrame()
        
        def converter_geometria(blob):
            try: return wkb.loads(bytes(blob))
            except: return None

        if 'geometry' in df.columns:
            geometrias = df['geometry'].apply(converter_geometria)
            gdf = gpd.GeoDataFrame(df, geometry=geometrias, crs="EPSG:4674")
            
            if eh_sigef:
                gdf['area_display'] = calcular_area_hectares(gdf)
            else:
                if 'qtd_area_p' in gdf.columns:
                    gdf['area_display'] = gdf['qtd_area_p'].astype(str).str.replace(',', '.').astype(float)
                else:
                    gdf['area_display'] = 0.0

            return gdf
        else: return gpd.GeoDataFrame()

    except Exception: return gpd.GeoDataFrame()

# --- 5. INTERFACE ---
def render_tab():
    st.markdown("### 📡 Consulta Pública INCRA")
    
    c_base, c_in1, c_in2, c_btn = st.columns([1.2, 2, 2, 1], vertical_alignment="bottom")
    
    with c_base:
        tipo_base = st.radio("Base:", ["SIGEF", "SNCI"], horizontal=True, label_visibility="collapsed")
    
    filtros_gerados = []
    url_alvo = ""
    eh_sigef = False

    if tipo_base == "SIGEF":
        with c_in1: in_parcela = st.text_input("Cód. Parcela (UUID):", placeholder="Ex: 426af057-...")
        with c_in2: in_imovel = st.text_input("Cód. Imóvel:", placeholder="Ex: 950238...")
        
        if in_parcela: filtros_gerados.append(f"CAST(parcela_co AS VARCHAR) = '{in_parcela.strip()}'")
        if in_imovel: filtros_gerados.append(f"CAST(codigo_imo AS VARCHAR) = '{in_imovel.strip()}'")
        
        url_alvo = URL_SIGEF
        eh_sigef = True

    else: # SNCI
        with c_in1: in_certif = st.text_input("Nº Certificação:", placeholder="Ex: 16180300...")
        with c_in2: in_imovel_snci = st.text_input("Cód. Imóvel:", placeholder="Ex: 908037...")

        if in_certif: filtros_gerados.append(f"CAST(num_certif AS VARCHAR) = '{in_certif.strip()}'")
        if in_imovel_snci: filtros_gerados.append(f"CAST(cod_imovel AS VARCHAR) = '{in_imovel_snci.strip()}'")
        
        url_alvo = URL_SNCI
        eh_sigef = False

    with c_btn:
        btn_buscar = st.button("🔍 Buscar", use_container_width=True)

    if btn_buscar:
        if not filtros_gerados: st.toast("Digite um código.", icon="⚠️")
        else:
            st.session_state['resultado_incra'] = None
            st.session_state['tipo_incra'] = None
            with st.spinner("Buscando..."):
                gdf_res = buscar_imovel_especifico(filtros_gerados, url_alvo, eh_sigef)
            st.session_state['resultado_incra'] = gdf_res
            st.session_state['tipo_incra'] = tipo_base

    if st.session_state.get('resultado_incra') is not None:
        gdf = st.session_state['resultado_incra']
        tipo = st.session_state['tipo_incra']
        
        if gdf.empty:
            st.warning("Nenhum registro encontrado.")
            return

        st.markdown("---")
        
        if tipo == "SIGEF":
            preferencia = ['parcela_co', 'area_display', 'codigo_imo', 'nome_area', 'status', 'registro_m']
        else:
            preferencia = ['num_certif', 'area_display', 'cod_imovel', 'nome_imove', 'uf_municip']

        cols_final = [c for c in preferencia if c in gdf.columns]
        
        st.markdown(f"**Resultados em {tipo} ({len(gdf)}):**")
        
        df_display = gdf[cols_final].rename(columns=MAPA_VISUAL)
        altura_dinamica = min((len(gdf) * 35) + 38, 400)
        
        event = st.dataframe(
            df_display,
            use_container_width=True,
            selection_mode="single-row",
            on_select="rerun",
            height=altura_dinamica, 
            key="grid_incra_final_v7"
        )

        if len(event.selection.rows) > 0:
            idx = event.selection.rows[0]
            row = gdf.iloc[idx]
            
            with st.container(border=True):
                try:
                    area_val = f"{float(row.get('area_display', 0)):.4f} ha"
                except:
                    area_val = "---"

                if tipo == "SIGEF":
                    titulo = row.get('nome_area', 'Sem Nome')
                    cod_dl = row.get('parcela_co', '000')
                    infos = [
                        f"**Imóvel:** {row.get('codigo_imo', '-')}",
                        f"**Matrícula:** {row.get('registro_m', '-')}",
                        f"**Área Est.:** {area_val}",
                        f"**Situação:** {row.get('status', '-')}"
                    ]
                else:
                    titulo = row.get('nome_imove', 'Sem Nome')
                    cod_dl = row.get('num_certif', '000')
                    infos = [
                        f"**Imóvel:** {row.get('cod_imovel', '-')}",
                        f"**Certificação:** {cod_dl}",
                        f"**Local:** {row.get('uf_municip', '-')}",
                        f"**Área:** {area_val}"
                    ]

                c_tit, c_down = st.columns([3, 1])
                with c_tit:
                    st.markdown(f"##### 📍 {titulo}")
                with c_down:
                    with st.popover("📥 Baixar Arquivos", use_container_width=True):
                        gdf_sel = gdf.iloc[[idx]]
                        
                        kml_result = gerar_kml_perimetro(gdf_sel, cod_dl)
                        
                        if isinstance(kml_result, bytes):
                            st.download_button("🌍 KML", data=kml_result, file_name=f"{cod_dl}.kml", use_container_width=True)
                        else:
                            st.error(f"Erro KML: {kml_result}")
                        
                        shp_data = gerar_shp_perimetro(gdf_sel, cod_dl)
                        if shp_data: 
                            st.download_button("🗺️ SHP (ZIP)", data=shp_data, file_name=f"{cod_dl}.zip", use_container_width=True)

                cols_info = st.columns(len(infos))
                for i, info in enumerate(infos):
                    cols_info[i].markdown(info)

                st.write("") 
                m = folium.Map(location=[row.geometry.centroid.y, row.geometry.centroid.x], zoom_start=13, tiles="Esri World Imagery")
                folium.GeoJson(
                    row.geometry, 
                    style_function=lambda x: {'color': '#FFFF00', 'weight': 3, 'fillOpacity': 0.0}
                ).add_to(m)
                st_folium(m, height=450, use_container_width=True, key="map_incra_wide_v7")
        
        else:
            st.info("👆 Selecione um imóvel na lista para ver o mapa e baixar.")