import streamlit as st
import ee
import xml.etree.ElementTree as ET
import requests
import ssl
import json
import os
import time
import io
import zipfile
import shutil
import tempfile
import pandas as pd
from shapely.geometry import shape, Point, mapping
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from shapely.ops import transform

# Tenta importar Geopandas e Fiona
try:
    import geopandas as gpd
    import fiona
    # Habilita drivers KML para leitura/escrita
    fiona.drvsupport.supported_drivers['KML'] = 'rw'
    fiona.drvsupport.supported_drivers['LIBKML'] = 'rw'
except ImportError:
    gpd = None
    fiona = None

# ==========================================
# 1. INICIALIZAÇÃO E STATE
# ==========================================

def init_gee():
    """Cria o arquivo de credenciais e conecta ao GEE"""
    try:
        # 1. Verifica se estamos na nuvem (tem secrets)
        if "earth_engine" in st.secrets:
            credentials_path = os.path.expanduser("~/.config/earthengine/")
            credentials_file = os.path.join(credentials_path, "credentials")

            if not os.path.exists(credentials_path):
                os.makedirs(credentials_path)

            token_content = st.secrets["earth_engine"]["token"]
            
            with open(credentials_file, "w") as f:
                f.write(token_content)
            
        # 2. Inicializa
        try:
            ee.Initialize()
        except Exception:
            ee.Initialize(project='ee-julioczcosta')
            
    except Exception as e:
        st.error(f"⚠️ Falha na conexão GEE: {e}")
        st.stop()

# Chama a função
init_gee()

def init_session_state():
    """Inicializa variáveis básicas da sessão."""
    if 'camadas_fixas' not in st.session_state: st.session_state['camadas_fixas'] = []
    if 'camada_preview' not in st.session_state: st.session_state['camada_preview'] = None
    if 'ndvi_stats' not in st.session_state: st.session_state['ndvi_stats'] = None
    if 'ndvi_colorbar' not in st.session_state: st.session_state['ndvi_colorbar'] = None
    if 'current_geometry' not in st.session_state: st.session_state['current_geometry'] = None
    if 'source_name' not in st.session_state: st.session_state['source_name'] = "Nenhuma seleção"
    if 'preview_geometry' not in st.session_state: st.session_state['preview_geometry'] = None
    if 'preview_data' not in st.session_state: st.session_state['preview_data'] = None
    if 'last_car_searched' not in st.session_state: st.session_state['last_car_searched'] = None
    if 'car_consultado' not in st.session_state: st.session_state['car_consultado'] = None

def limpar_analises():
    """FAXINA GERAL: Apaga todos os dados calculados."""
    keys_to_delete = [
        'clim_temp', 'clim_rain', 'erro_clima_temp', 'erro_clima_rain', 'last_clim_source',
        'camada_preview', 'camadas_fixas', 'ndvi_stats', 'ndvi_colorbar', 'ctx_dados'
    ]
    for k in keys_to_delete:
        if k in st.session_state:
            del st.session_state[k]
            
    st.session_state['camadas_fixas'] = []
    st.session_state['camada_preview'] = None

def reset_preview():
    st.session_state['camada_preview'] = None
    st.session_state['ndvi_stats'] = None
    st.session_state['ndvi_colorbar'] = None

# ==========================================
# 2. CONEXÃO SEGURA (CAR/SSL)
# ==========================================

class LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False):
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.poolmanager = PoolManager(num_pools=connections, maxsize=maxsize, block=block, ssl_context=ctx)

def get_legacy_session():
    session = requests.Session()
    session.mount('https://', LegacySSLAdapter())
    return session

def get_car_geometry(codigo_car):
    try:
        if '-' not in codigo_car:
            return None, None, "Formato inválido. Use Ex: UF-CODIGO..."
            
        uf_sigla = codigo_car.split('-')[0].lower()
        layer_name = f"sicar:sicar_imoveis_{uf_sigla}"
        wfs_url = "https://geoserver.car.gov.br/geoserver/sicar/wfs"
        
        params = {
            "service": "WFS", "version": "1.0.0", "request": "GetFeature",
            "typeName": layer_name, "outputFormat": "application/json",
            "cql_filter": f"cod_imovel='{codigo_car}'"
        }
        
        session = get_legacy_session()
        response = session.get(wfs_url, params=params, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            if "features" in data and len(data["features"]) > 0:
                feat = data["features"][0]
                gee_geometry = ee.Geometry(feat["geometry"])
                props = feat.get("properties", {})
                return gee_geometry, props, None
            else:
                return None, None, "Código CAR não encontrado."
        else:
            return None, None, f"Erro CAR: {response.status_code}"
            
    except Exception as e:
        return None, None, f"Erro: {e}"

# ==========================================
# 3. PROCESSAMENTO KML (GEE Direto)
# ==========================================

@st.cache_data
def processar_kml_conteudo(kml_content):
    try:
        kml_str = kml_content.decode('utf-8', errors='ignore')
        tree = ET.fromstring(kml_str)
        poligonos_gee = []
        for elem in tree.iter():
            if 'coordinates' in elem.tag and elem.text:
                try:
                    coords_list = []
                    for coord in elem.text.strip().split():
                        parts = coord.split(',')
                        if len(parts) >= 2:
                            coords_list.append([float(parts[0]), float(parts[1])])
                    if len(coords_list) > 2:
                        poligonos_gee.append(ee.Geometry.Polygon([coords_list]))
                except: continue
        
        if not poligonos_gee: return None, "Sem coordenadas."
        if len(poligonos_gee) > 1: return ee.Geometry.MultiPolygon(poligonos_gee), None
        return poligonos_gee[0], None
    except Exception as e:
        return None, str(e)

# ==========================================
# 4. FUNÇÕES DE SUPORTE GEOPANDAS
# ==========================================

def _force_2d(geometry):
    if geometry.has_z:
        return transform(lambda x, y, z: (x, y), geometry)
    return geometry

def carregar_kml_geopandas(uploaded_file):
    if gpd is None: return None, "Biblioteca Geopandas não instalada."
    try:
        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, uploaded_file.name)
        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        target_file = file_path
        if uploaded_file.name.lower().endswith(('.kmz', '.zip')):
            with zipfile.ZipFile(file_path, 'r') as z:
                kml_filename = [n for n in z.namelist() if n.lower().endswith('.kml')]
                if not kml_filename:
                    return None, "Nenhum arquivo .kml encontrado."
                z.extract(kml_filename[0], temp_dir)
                target_file = os.path.join(temp_dir, kml_filename[0])

        gdfs = []
        try: layers = fiona.listlayers(target_file)
        except: layers = [0] 

        for layer in layers:
            try:
                gdf = gpd.read_file(target_file, layer=layer)
                if not gdf.empty:
                    gdf.geometry = gdf.geometry.apply(_force_2d)
                    if gdf.crs is None:
                        gdf.set_crs(epsg=4326, inplace=True)
                    else:
                        gdf = gdf.to_crs(epsg=4326)
                    gdfs.append(gdf)
            except Exception: continue

        if not gdfs: return None, "Nenhuma geometria válida encontrada."
        gdf_final = pd.concat(gdfs, ignore_index=True)
        try: shutil.rmtree(temp_dir)
        except: pass

        return gdf_final, None
    except Exception as e:
        return None, f"Erro ao processar arquivo: {str(e)}"

# ==========================================
# 5. DADOS DE CONTEXTO E INTERSECÇÃO GEE
# ==========================================

@st.cache_data(show_spinner=False)
def obter_municipios_interseccao(_geom_gee, cache_id):
    """
    Cruza o imóvel com a malha municipal do IBGE via GEE e calcula porcentagem de área.
    Retorna uma lista de municípios que tocam a propriedade.
    """
    try:
        # Malha oficial do IBGE 2020 no MapBiomas
        mun_col = ee.FeatureCollection("projects/mapbiomas-workspace/AUXILIAR/municipios-2020")
        interseccao = mun_col.filterBounds(_geom_gee)

        def calc_area(feat):
            inter = feat.geometry().intersection(_geom_gee, 10)
            area_ha = inter.area(10).divide(10000)
            return feat.set('area_imovel_ha', area_ha)

        muns_area = interseccao.map(calc_area)
        features = muns_area.getInfo()['features']

        resultados = []
        area_total = 0

        for f in features:
            props = f['properties']
            nome = props.get('NM_MUN', 'Desconhecido')
            uf = props.get('SIGLA_UF', '')
            area = props.get('area_imovel_ha', 0)
            cod_ibge = props.get('CD_MUN')

            # Considera apenas intersecções > 0.05 ha para evitar erros de desenho
            if area > 0.05:
                resultados.append({
                    'municipio': f"{nome} - {uf}".strip(" -"),
                    'nome_puro': nome,
                    'uf': uf,
                    'area_ha': area,
                    'cod_ibge': str(cod_ibge)
                })
                area_total += area

        # Calcula porcentagem e ordena
        for r in resultados:
            r['porcentagem'] = (r['area_ha'] / area_total) * 100 if area_total > 0 else 0
        
        resultados = sorted(resultados, key=lambda x: x['area_ha'], reverse=True)
        return resultados

    except Exception as e:
        return [{"erro": str(e)}]

@st.cache_data
def get_koppen_class(lat, lon):
    caminho_arquivo = os.path.join("dados", "koppen_brasil.geojson")
    try:
        with open(caminho_arquivo, 'r', encoding='utf-8') as f:
            data = json.load(f)
        ponto = Point(lon, lat)
        for feature in data['features']:
            if shape(feature['geometry']).contains(ponto):
                return feature['properties']
    except Exception: pass
        
    if lat > -10: code, desc = "Am", "Tropical de Monção"
    elif lat > -20: code, desc = "Aw", "Tropical de Savana"
    elif lat > -25: code, desc = "Cwa", "Subtropical Úmido (Inverno Seco)"
    else: code, desc = "Cfa", "Subtropical Úmido"
    return {"Classificacao": code, "Descricao": desc}

@st.cache_data
def get_ibge_context_by_code(cod_ibge, nome_oficial, uf, lat, lon):
    """Busca dados populacionais e territoriais do IBGE usando o código."""
    try:
        session = requests.Session()
        
        # População e Área
        populacao, area_km2 = None, None
        try:
            r_pop = session.get(f"https://apisidra.ibge.gov.br/values/t/4714/n6/{cod_ibge}/v/93/p/last%201", timeout=5).json()
            if len(r_pop) > 1: populacao = float(r_pop[1].get("V"))
        except: pass
        
        try:
            r_area = session.get(f"https://servicodados.ibge.gov.br/api/v3/malhas/municipios/{cod_ibge}/metadados", timeout=5).json()
            if r_area and len(r_area) > 0: area_km2 = float(r_area[0].get("area", {}).get("dimensao"))
        except: pass

        densidade = (populacao / area_km2) if (populacao and area_km2) else None

        # Regiões (Intermediária e Imediata via WFS do IBGE usando o centroide)
        reg_int, reg_ime = "---", "---"
        try:
            bbox = f"{lon-0.001},{lat-0.001},{lon+0.001},{lat+0.001}"
            url_wfs = "https://geoservicos.ibge.gov.br/geoserver/ows"
            p_int = {"service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": "CGEO:RG2017_rgint", "outputFormat": "application/json", "bbox": bbox}
            r_int = session.get(url_wfs, params=p_int, timeout=5).json()
            if r_int.get("features"): reg_int = r_int["features"][0]["properties"].get("first_nome", "---")

            p_ime = {"service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": "CGMAT:qg_2024_110_reggeogimed_agreg", "outputFormat": "application/json", "bbox": bbox}
            r_ime = session.get(url_wfs, params=p_ime, timeout=5).json()
            if r_ime.get("features"): reg_ime = r_ime["features"][0]["properties"].get("nm_rgi", "---")
        except: pass

        return {
            "municipio": nome_oficial, "uf": uf, "area_km2": area_km2, "populacao": populacao, 
            "densidade": densidade, "codigo_ibge": cod_ibge,
            "regiao_intermediaria": reg_int, "regiao_imediata": reg_ime
        }
    except Exception as e: return {"erro": str(e)}

@st.cache_data
def get_bacia_info(lat, lon):
    try:
        session = requests.Session()
        url = "https://geoservicos.ibge.gov.br/geoserver/ows"
        bbox = f"{lon-0.01},{lat-0.01},{lon+0.01},{lat+0.01}"
        
        try:
            r = session.get(url, params={"service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": "CREN:bacias_nivel_6", "outputFormat": "application/json", "bbox": bbox}, timeout=6).json()
            props = r["features"][0]["properties"] if r.get("features") else {}
        except: props = {}

        if not props:
            try:
                r = session.get(url, params={"service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": "CREN:bacias_nivel_4", "outputFormat": "application/json", "bbox": bbox}, timeout=6).json()
                props = r["features"][0]["properties"] if r.get("features") else {}
            except: pass

        if not props: return {"erro": "Bacia não identificada."}

        return {
            "suprabacia": props.get("suprabacia", "---"),
            "nome_bacia": props.get("nome_bacia", "---"),
            "curso_prin": props.get("curso_prin", "---"),
            "princ_aflu": props.get("princ_aflu", "---")
        }
    except Exception as e: return {"erro": str(e)}

# ==========================================
# 6. FUNÇÕES DE EXPORTAÇÃO (VETORIAL)
# ==========================================

def convert_gee_to_gdf(gee_geometry, properties):
    if gpd is None: return None
    try:
        geojson = gee_geometry.getInfo()
        shapely_geom = shape(geojson)
        if not properties or not isinstance(properties, dict): properties = {}
        gdf = gpd.GeoDataFrame([properties], geometry=[shapely_geom])
        gdf.set_crs(epsg=4326, inplace=True)
        return gdf
    except Exception as e:
        print(f"Erro ao converter GDF: {e}")
        return None

def gerar_kml_bytes(gdf, nome_arquivo):
    if gpd is None: return None
    try:
        with tempfile.NamedTemporaryFile(suffix='.kml', delete=False) as tmp:
            gdf.to_file(tmp.name, driver='KML')
            tmp.seek(0)
            return tmp.read()
    except:
        return gdf.to_json().encode('utf-8')

def gerar_shapefile_zip(gdf):
    if gpd is None: return None
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            nome_base = "imovel_car"
            caminho_completo = os.path.join(temp_dir, nome_base + ".shp")
            gdf.to_file(caminho_completo, encoding='utf-8')
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for ext in ['.shp', '.shx', '.dbf', '.prj', '.cpg']:
                    arquivo = nome_base + ext
                    caminho_arq = os.path.join(temp_dir, arquivo)
                    if os.path.exists(caminho_arq):
                        zip_file.write(caminho_arq, arcname=arquivo)
            return zip_buffer.getvalue()
    except Exception as e:
        print(f"Erro SHP: {e}")
        return None

def gerar_geopackage_bytes(gdf):
    if gpd is None: return None
    try:
        with tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False) as tmp:
            gdf.to_file(tmp.name, driver="GPKG")
            tmp.seek(0)
            return tmp.read()
    except: return None
    
@st.cache_data(show_spinner=False)
def get_altimetria_municipio(cod_ibge):
    """
    Calcula a altitude média de todo o território do município usando SRTM e a malha do IBGE no GEE.
    """
    try:
        # Puxa a malha de municípios e filtra pelo código IBGE exato
        mun_col = ee.FeatureCollection("projects/mapbiomas-workspace/AUXILIAR/municipios-2020")
        municipio = mun_col.filter(ee.Filter.eq('CD_MUN', str(cod_ibge))).first()
        geom_mun = municipio.geometry()

        # Puxa o SRTM (Modelo de Elevação)
        srtm = ee.Image('USGS/SRTMGL1_003')

        # Calcula a média no município todo
        # Escala de 90m e bestEffort=True garantem que não vai dar erro de limite de memória
        stats = srtm.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=geom_mun,
            scale=90,
            maxPixels=1e13,
            bestEffort=True
        ).getInfo()

        altitude_media = stats.get('elevation', 0)
        return {"media": altitude_media}
    except Exception as e:
        return {"erro": str(e)}