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
    """Inicializa o Google Earth Engine."""
    try:
        meu_projeto = 'ee-julioczcosta'
        ee.Initialize(project=meu_projeto)
    except:
        try:
            ee.Authenticate()
            ee.Initialize(project='ee-julioczcosta')
        except Exception as e:
            st.error(f"Erro crítico GEE: {e}")
            st.stop()

def init_session_state():
    """Inicializa variáveis básicas da sessão."""
    # Sentinel
    if 'camadas_fixas' not in st.session_state: st.session_state['camadas_fixas'] = []
    if 'camada_preview' not in st.session_state: st.session_state['camada_preview'] = None
    if 'ndvi_stats' not in st.session_state: st.session_state['ndvi_stats'] = None
    if 'ndvi_colorbar' not in st.session_state: st.session_state['ndvi_colorbar'] = None
    
    # Geometria Principal (Confirmada)
    if 'current_geometry' not in st.session_state: st.session_state['current_geometry'] = None
    if 'source_name' not in st.session_state: st.session_state['source_name'] = "Nenhuma seleção"
    
    # Variáveis de Preview (Home)
    if 'preview_geometry' not in st.session_state: st.session_state['preview_geometry'] = None
    if 'preview_data' not in st.session_state: st.session_state['preview_data'] = None
    if 'last_car_searched' not in st.session_state: st.session_state['last_car_searched'] = None
    
    # Variáveis de Consulta CAR
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
    """Limpa apenas o preview do Sentinel."""
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
    """Busca geometria do imóvel no WFS do SICAR."""
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
    """Lê arquivo KML (XML) e converte para geometria Earth Engine diretamente."""
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
    """Remove a coordenada Z (altitude) das geometrias para evitar erros."""
    if geometry.has_z:
        return transform(lambda x, y, z: (x, y), geometry)
    return geometry

def carregar_kml_geopandas(uploaded_file):
    """
    Lê KML, KMZ ou ZIP e retorna um GeoDataFrame (GPD) consolidado com TODAS as geometrias.
    Usa Geopandas e Fiona para processar arquivos complexos.
    """
    if gpd is None: return None, "Biblioteca Geopandas não instalada."

    try:
        # Cria diretório temporário
        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, uploaded_file.name)
        
        # Salva o arquivo enviado
        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        target_file = file_path

        # Lógica de extração para KMZ ou ZIP
        if uploaded_file.name.lower().endswith(('.kmz', '.zip')):
            with zipfile.ZipFile(file_path, 'r') as z:
                # Procura por arquivo .kml dentro do zip/kmz
                kml_filename = [n for n in z.namelist() if n.lower().endswith('.kml')]
                if not kml_filename:
                    return None, "Nenhum arquivo .kml encontrado dentro do pacote."
                z.extract(kml_filename[0], temp_dir)
                target_file = os.path.join(temp_dir, kml_filename[0])

        gdfs = []
        # Tenta listar camadas. KMLs complexos podem ter várias (ex: Pontos, Linhas, Polígonos).
        try:
            layers = fiona.listlayers(target_file)
        except:
            layers = [0] # Fallback se não conseguir listar

        for layer in layers:
            try:
                # Lê a camada específica
                gdf = gpd.read_file(target_file, layer=layer)
                
                if not gdf.empty:
                    # Remove Z (3D) para evitar erro no GEE ou Shapefile
                    gdf.geometry = gdf.geometry.apply(_force_2d)
                    
                    # Padroniza CRS para WGS84
                    if gdf.crs is None:
                        gdf.set_crs(epsg=4326, inplace=True)
                    else:
                        gdf = gdf.to_crs(epsg=4326)
                    
                    gdfs.append(gdf)
            except Exception:
                continue # Pula camadas vazias ou inválidas

        if not gdfs:
            return None, "Nenhuma geometria válida encontrada."

        # Consolida pontos, linhas e polígonos num único GeoDataFrame
        gdf_final = pd.concat(gdfs, ignore_index=True)
        
        # Limpa diretório temporário (opcional, SO limpa depois)
        try: shutil.rmtree(temp_dir)
        except: pass

        return gdf_final, None

    except Exception as e:
        return None, f"Erro ao processar arquivo: {str(e)}"

# ==========================================
# 5. DADOS DE CONTEXTO
# ==========================================

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
        
    # Fallback aproximado se não achar o arquivo
    if lat > -10: code, desc = "Am", "Tropical de Monção"
    elif lat > -20: code, desc = "Aw", "Tropical de Savana"
    elif lat > -25: code, desc = "Cwa", "Subtropical Úmido (Inverno Seco)"
    else: code, desc = "Cfa", "Subtropical Úmido"
    return {"Classificacao": code, "Descricao": desc}

@st.cache_data
def get_ibge_context(lat, lon):
    try:
        session = requests.Session()
        headers = {"User-Agent": "GeoDashboard/1.0"}
        
        # Geolocalização Reversa (Nominatim)
        url_geo = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=10"
        try:
            resp_geo = session.get(url_geo, headers=headers, timeout=5)
            data_geo = resp_geo.json()
            address = data_geo.get("address", {})
            cidade = address.get("city") or address.get("town") or address.get("village") or address.get("municipality")
            state_raw = address.get("ISO3166-2-lvl4", "") 
            uf = state_raw.split("-")[1] if "-" in state_raw else "BR"
            if not cidade: return {"erro": "Local não identificado."}
        except: return {"erro": "Erro na geolocalização."}

        # Busca Município no IBGE
        url_loc = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"
        resp_loc = session.get(url_loc, timeout=5)
        lista = resp_loc.json()
        
        def normalizar(s): return s.lower().replace("á","a").replace("ã","a").replace("â","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u").replace("ç","c")
        
        target = normalizar(cidade)
        municipio_alvo = next((m for m in lista if normalizar(m['nome']) == target), None)
        if not municipio_alvo: municipio_alvo = next((m for m in lista if target in normalizar(m['nome'])), None)
        if not municipio_alvo: return {"erro": f"Município {cidade} não encontrado."}
        
        cod_ibge = municipio_alvo['id']
        nome_oficial = municipio_alvo['nome']

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

        # Regiões (Intermediária e Imediata via WFS)
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
            "municipio": nome_oficial, "uf": uf, "area_km2": area_km2, "populacao": populacao, "densidade": densidade, "codigo_ibge": cod_ibge,
            "regiao_intermediaria": reg_int, "regiao_imediata": reg_ime
        }
    except Exception as e: return {"erro": str(e)}

@st.cache_data
def get_bacia_info(lat, lon):
    try:
        session = requests.Session()
        url = "https://geoservicos.ibge.gov.br/geoserver/ows"
        bbox = f"{lon-0.01},{lat-0.01},{lon+0.01},{lat+0.01}"
        
        # Tenta Nível 6 (Mais detalhado)
        try:
            r = session.get(url, params={"service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": "CREN:bacias_nivel_6", "outputFormat": "application/json", "bbox": bbox}, timeout=6).json()
            props = r["features"][0]["properties"] if r.get("features") else {}
        except: props = {}

        # Fallback Nível 4
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
    """Converte geometria GEE e propriedades para GeoDataFrame."""
    if gpd is None: return None
    try:
        geojson = gee_geometry.getInfo()
        shapely_geom = shape(geojson)
        if not properties or not isinstance(properties, dict):
            properties = {}
        gdf = gpd.GeoDataFrame([properties], geometry=[shapely_geom])
        gdf.set_crs(epsg=4326, inplace=True)
        return gdf
    except Exception as e:
        print(f"Erro ao converter GDF: {e}")
        return None

def gerar_kml_bytes(gdf, nome_arquivo):
    """Gera bytes de um arquivo KML."""
    if gpd is None: return None
    try:
        with tempfile.NamedTemporaryFile(suffix='.kml', delete=False) as tmp:
            gdf.to_file(tmp.name, driver='KML')
            tmp.seek(0)
            return tmp.read()
    except:
        return gdf.to_json().encode('utf-8')

def gerar_shapefile_zip(gdf):
    """Gera um ZIP contendo o Shapefile."""
    if gpd is None: return None
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            nome_base = "imovel_car"
            caminho_completo = os.path.join(temp_dir, nome_base + ".shp")
            
            # Salva o shapefile
            gdf.to_file(caminho_completo, encoding='utf-8')
            
            # Zipa os arquivos gerados
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
    """Gera bytes de um arquivo GPKG."""
    if gpd is None: return None
    try:
        with tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False) as tmp:
            gdf.to_file(tmp.name, driver="GPKG")
            tmp.seek(0)
            return tmp.read()
    except: return None