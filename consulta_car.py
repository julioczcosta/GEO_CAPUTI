import streamlit as st
import requests
import folium
from streamlit_folium import st_folium
import ssl
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.poolmanager import PoolManager
import json
import geopandas as gpd
import pandas as pd
import io
import zipfile
import tempfile
import os
import xml.dom.minidom as minidom

# --- 1. CONFIGURAÇÃO DE REDE BLINDADA ---
class LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False):
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.poolmanager = PoolManager(num_pools=connections, maxsize=maxsize, block=block, ssl_context=ctx)

def get_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    adapter = LegacySSLAdapter(max_retries=retries)
    session.mount('https://', adapter)
    return session

# --- 2. FUNÇÕES INTELIGENTES DE DADOS ---

def limpar_numero(valor):
    """Converte strings com vírgula para float."""
    if not valor: return None
    if isinstance(valor, (int, float)): return float(valor)
    try:
        limpo = str(valor).replace('.', '').replace(',', '.')
        return float(limpo)
    except:
        return None

def buscar_propriedade(props, termos):
    """Busca valor em dicionário ignorando maiúsculas/minúsculas."""
    for t in termos:
        if t in props: return props[t]
    
    props_lower = {k.lower(): v for k, v in props.items()}
    for t in termos:
        if t.lower() in props_lower: return props_lower[t.lower()]
        
    for key, value in props_lower.items():
        if any(t in key for t in termos): return value
    return None

def calcular_area_geometria(gdf):
    """Calcula área em Hectares baseada no desenho."""
    try:
        gdf_utm = gdf.to_crs(gdf.estimate_utm_crs())
        area_m2 = gdf_utm.geometry.area.sum()
        return area_m2 / 10000 
    except:
        return None

# --- 3. GERADORES DE ARQUIVOS ---
def gerar_kml_perimetro(gdf, codigo_car, metadados):
    try:
        doc = minidom.Document()
        kml = doc.createElementNS('http://www.opengis.net/kml/2.2', 'kml')
        kml.setAttribute('xmlns', 'http://www.opengis.net/kml/2.2')
        doc.appendChild(kml)
        
        document = doc.createElement('Document')
        kml.appendChild(document)
        
        # Estilo Amarelo (Sem preenchimento)
        style = doc.createElement('Style')
        style.setAttribute('id', 'yellowBorder')
        lstyle = doc.createElement('LineStyle')
        lcolor = doc.createElement('color')
        lcolor.appendChild(doc.createTextNode('ff00ffff')) # Amarelo
        lwidth = doc.createElement('width')
        lwidth.appendChild(doc.createTextNode('4'))
        lstyle.appendChild(lcolor)
        lstyle.appendChild(lwidth)
        pstyle = doc.createElement('PolyStyle')
        pcolor = doc.createElement('color')
        pcolor.appendChild(doc.createTextNode('00ffffff')) # Transparente
        pstyle.appendChild(pcolor)
        style.appendChild(lstyle)
        style.appendChild(pstyle)
        document.appendChild(style)

        for _, row in gdf.iterrows():
            placemark = doc.createElement('Placemark')
            name = doc.createElement('name')
            name.appendChild(doc.createTextNode(str(codigo_car)))
            placemark.appendChild(name)
            
            style_url = doc.createElement('styleUrl')
            style_url.appendChild(doc.createTextNode('#yellowBorder'))
            placemark.appendChild(style_url)

            # Usa os metadados garantidos
            desc_txt = f"Código: {codigo_car}\nMunicípio: {metadados['municipio']}\nÁrea: {metadados['area']} ha\nStatus: {metadados['status']}"
            
            desc = doc.createElement('description')
            desc.appendChild(doc.createCDATASection(desc_txt))
            placemark.appendChild(desc)

            geom = row.geometry
            geoms = [geom] if geom.geom_type == 'Polygon' else (geom.geoms if geom.geom_type == 'MultiPolygon' else [])

            if len(geoms) > 1:
                container = doc.createElement('MultiGeometry')
                placemark.appendChild(container)
            else:
                container = placemark

            for poly in geoms:
                if poly.is_empty: continue
                polygon_xml = doc.createElement('Polygon')
                outer = doc.createElement('outerBoundaryIs')
                ring = doc.createElement('LinearRing')
                coords_elem = doc.createElement('coordinates')
                coords = list(poly.exterior.coords)
                coords_str = " ".join([f"{x},{y},0" for x, y in coords])
                coords_elem.appendChild(doc.createTextNode(coords_str))
                ring.appendChild(coords_elem)
                outer.appendChild(ring)
                polygon_xml.appendChild(outer)
                container.appendChild(polygon_xml)
            
            document.appendChild(placemark)
        return doc.toprettyxml(encoding='UTF-8')
    except Exception: return None

def gerar_shp_perimetro(gdf, codigo_car):
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            clean_gdf = gdf[['geometry']].copy()
            clean_gdf['codigo'] = str(codigo_car)
            safe_code = str(codigo_car).replace("/", "_").replace(".", "")
            clean_gdf.to_file(os.path.join(tmpdirname, f"CAR_{safe_code}.shp"), driver='ESRI Shapefile')

            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(tmpdirname):
                    for file in files:
                        file_path = os.path.join(root, file)
                        zf.write(file_path, arcname=file)
            return zip_buffer.getvalue()
    except Exception: return None

# --- 4. FUNÇÃO PRINCIPAL ---
def render_tab():
    st.markdown("### 🌳 Consulta Pública SICAR (Perímetro)")
    st.markdown("Busca oficial do perímetro do imóvel na base federal.")
    st.markdown("---")

    if 'car_data' not in st.session_state: st.session_state['car_data'] = None
    if 'car_meta_safe' not in st.session_state: st.session_state['car_meta_safe'] = {}

    # BUSCA
    col1, col2 = st.columns([1, 2])
    with col1:
        st.info("💡 **Dica:** O código deve estar no formato `UF-Município-Hash`.")
    with col2:
        codigo_car_raw = st.text_input("Código do Imóvel:", placeholder="Ex: MT-1234567-...")
        
        if st.button("🔍 Buscar Perímetro", type="primary"):
            if not codigo_car_raw:
                st.warning("Digite o código.")
            else:
                codigo_car = codigo_car_raw.strip().replace("\n", "").replace("\r", "")
                st.session_state['car_data'] = None
                st.session_state['car_meta_safe'] = {}
                
                try:
                    uf_sigla = codigo_car.split('-')[0].lower()
                    base_url = "https://geoserver.car.gov.br/geoserver/sicar/wfs"
                    layer_name = f"sicar:sicar_imoveis_{uf_sigla}"
                    
                    params = {
                        "service": "WFS", "version": "1.0.0", "request": "GetFeature",
                        "typeName": layer_name, "outputFormat": "application/json",
                        "cql_filter": f"cod_imovel='{codigo_car}'"
                    }
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
                    
                    with st.spinner(f"Consultando base de {uf_sigla.upper()}..."):
                        session = get_session()
                        resp = session.get(base_url, params=params, headers=headers, timeout=25)
                        
                        if resp.status_code == 200:
                            data = resp.json()
                            if "features" in data and len(data["features"]) > 0:
                                st.session_state['car_data'] = data
                                st.toast("Imóvel localizado!", icon="✅")
                            else:
                                st.error("Imóvel não encontrado.")
                        else:
                            st.error(f"Erro no servidor: {resp.status_code}")
                except Exception as e:
                    st.error(f"Erro de conexão: {e}")

    # RESULTADOS
    if st.session_state['car_data']:
        st.divider()
        data = st.session_state['car_data']
        
        try:
            gdf = gpd.GeoDataFrame.from_features(data["features"])
            gdf.crs = "EPSG:4674"
            
            # --- RECUPERAÇÃO DE DADOS ---
            props = data["features"][0]["properties"]
            
            # 1. Código
            cod_final = props.get('cod_imovel', codigo_car_raw if 'codigo_car_raw' in locals() else 'CAR_Dados')
            
            # 2. Município
            mun = buscar_propriedade(props, ['municipio', 'nom_municipio', 'cidade', 'nome_municipio']) or "Não Informado"
            
            # 3. Status
            status = buscar_propriedade(props, ['ind_status_imovel', 'des_condicao_aguardando_analise', 'situacao', 'status']) or "---"
            
            # 4. Área
            area_attr = buscar_propriedade(props, ['num_area_imovel', 'val_area_imovel', 'area_ir', 'area_imovel', 'area_ha', 'nu_area_imovel'])
            area_val = limpar_numero(area_attr)
            
            # Se não achou atributo, calcula
            if area_val is None:
                area_val = calcular_area_geometria(gdf)
                
            area_fmt = f"{area_val:.4f}" if area_val else "---"
            # REMOVIDO: o sufixo " (Calc.)" não é mais adicionado.

            meta = {
                "municipio": mun,
                "area": area_fmt,
                "status": status,
                "codigo": cod_final
            }

            # --- DISPLAY ---
            c1, c2, c3, c4 = st.columns([1.5, 1, 1, 1], vertical_alignment="center")
            c1.metric("Município", mun)
            c2.metric("Área (ha)", area_fmt)
            c3.metric("Status", status)
            
            with c4:
                with st.popover("📥 Baixar Arquivos", use_container_width=True):
                    # KML
                    kml_bytes = gerar_kml_perimetro(gdf, cod_final, meta)
                    if kml_bytes:
                        st.download_button("🌍 Baixar KML", data=kml_bytes, file_name=f"{cod_final}.kml", mime="application/vnd.google-earth.kml+xml", use_container_width=True)
                    
                    # SHP
                    shp_bytes = gerar_shp_perimetro(gdf, cod_final)
                    if shp_bytes:
                        st.download_button("🗺️ Baixar SHP (ZIP)", data=shp_bytes, file_name=f"{cod_final}_SHP.zip", mime="application/zip", use_container_width=True)

            # --- MAPA ---
            bounds = gdf.total_bounds
            centro = [(bounds[1] + bounds[3])/2, (bounds[0] + bounds[2])/2]

            m = folium.Map(location=centro, zoom_start=13, tiles="Esri World Imagery")
            
            folium.GeoJson(
                data,
                style_function=lambda x: {
                    'color': '#FFFF00',      # Amarelo
                    'weight': 3,
                    'fillColor': '#000000',
                    'fillOpacity': 0.0,      # Transparente
                    'opacity': 1.0
                },
                tooltip=folium.Tooltip(
                    f"""
                    <b>Município:</b> {mun}<br>
                    <b>Área:</b> {area_fmt} ha<br>
                    <b>Status:</b> {status}
                    """
                )
            ).add_to(m)
            
            st_folium(m, width="100%", height=600)
            
        except Exception as e:
            st.error(f"Erro ao processar dados: {e}")