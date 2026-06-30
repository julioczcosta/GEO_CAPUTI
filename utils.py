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

IBGE_WFS_URL = "https://geoservicos.ibge.gov.br/geoserver/ows"

# Mapa código IBGE (2 primeiros dígitos) -> sigla da UF. Usado em vários
# fallbacks de identificação de município.
UF_POR_CODIGO = {
    '11': 'RO', '12': 'AC', '13': 'AM', '14': 'RR', '15': 'PA', '16': 'AP', '17': 'TO',
    '21': 'MA', '22': 'PI', '23': 'CE', '24': 'RN', '25': 'PB', '26': 'PE', '27': 'AL',
    '28': 'SE', '29': 'BA', '31': 'MG', '32': 'ES', '33': 'RJ', '35': 'SP',
    '41': 'PR', '42': 'SC', '43': 'RS', '50': 'MS', '51': 'MT', '52': 'GO', '53': 'DF'
}

def ibge_wfs_get(params, timeout=30, retries=3):
    """GET no WFS do IBGE com retry/backoff para 502/503/504 (servidor instável)."""
    resp = None
    for attempt in range(retries):
        resp = requests.get(IBGE_WFS_URL, params=params, timeout=timeout,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code in (502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        break
    return resp

def ibge_api_json(url, timeout=15, retries=3, session=None):
    """GET JSON nas APIs REST do IBGE (SIDRA, malhas) com retry/backoff.
    Retorna o JSON decodificado ou None em caso de falha."""
    getter = session.get if session is not None else requests.get
    for attempt in range(retries):
        try:
            resp = getter(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code in (429, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 200:
                return resp.json()
            return None
        except requests.RequestException:
            time.sleep(2 ** attempt)
    return None

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

# NÃO chamar init_gee() no nível do módulo: gera efeito colateral na importação
# (st.error/st.stop durante o import) e inicialização dupla. O app.py é o único
# responsável por chamá-la, uma vez, após o login.

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
    """Adapter para servidores com TLS legado (ex.: geoserver.car.gov.br).

    ATENÇÃO: a verificação de certificado/hostname fica DESLIGADA aqui porque o
    geoserver.car.gov.br falha o handshake TLS com a verificação ligada
    (testado: 'SSLV3_ALERT_HANDSHAKE_FAILURE'). Não é negligência — é a única
    forma de a Consulta CAR funcionar enquanto o servidor do governo não corrige
    sua configuração de TLS.

    Mitigação de risco: este adapter é montado SOMENTE em sessões dedicadas a
    endpoints .gov.br do CAR (ver get_legacy_session / get_car_geometry). Nunca
    use esta sessão para tráfego sensível ou de terceiros.
    """
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

# NÃO usar @st.cache_data aqui: a função retorna objetos ee.Geometry, que o
# cache do Streamlit não serializa de forma confiável (pode gerar resultados
# inconsistentes/corrompidos). É chamada por clique de botão, então o cache
# traria pouco ganho e muito risco.
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
                except Exception: continue
        
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
        except Exception: layers = [0] 

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
        except Exception: pass

        return gdf_final, None
    except Exception as e:
        return None, f"Erro ao processar arquivo: {str(e)}"

# ==========================================
# 5. DADOS DE CONTEXTO E ALTIMETRIA
# ==========================================

@st.cache_data(show_spinner=False)
def obter_municipios_interseccao(_geom_gee, cache_id):
    """Detecta municípios que intersectam o imóvel (bbox expandido + fallback por ponto)."""
    try:
        geojson = _geom_gee.getInfo()
        geom_shapely = shape(geojson)
        gdf_imovel = gpd.GeoDataFrame({'geometry': [geom_shapely]}, crs="EPSG:4674")
        gdf_imovel["geometry"] = gdf_imovel.geometry.buffer(0)

        b = gdf_imovel.total_bounds
        cx, cy = (b[0]+b[2])/2, (b[1]+b[3])/2
        # Expande o bbox em ~0.05 grau (~5km) para imóveis pequenos
        m = 0.05
        bbox = f"{b[0]-m},{b[1]-m},{b[2]+m},{b[3]+m}"

        # Camadas verificadas via GetCapabilities em 2025-06 (CGMAT workspace, série qg_YYYY_030_munic)
        camadas_mun = [
            "CGMAT:qg_2025_030_munic",
            "CGMAT:qg_2024_030_munic",
            "CGMAT:qg_2023_030_munic",
        ]
        gdf_muns = gpd.GeoDataFrame()
        diag = []

        def _wfs_request(layer, bbox_str, retries=3):
            params = {
                "service": "WFS", "version": "2.0.0", "request": "GetFeature",
                "typeName": layer, "outputFormat": "application/json",
                "srsName": "EPSG:4674", "BBOX": f"{bbox_str},EPSG:4674",
            }
            for attempt in range(retries):
                resp = requests.get("https://geoservicos.ibge.gov.br/geoserver/ows",
                                    params=params, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code in (502, 503, 504):
                    # servidor instável — espera e tenta novamente
                    time.sleep(2 ** attempt)
                    continue
                return resp
            return resp  # retorna o último erro após os retries

        for layer in camadas_mun:
            try:
                resp = _wfs_request(layer, bbox)
                if resp.status_code == 200 and resp.text.strip().startswith("{"):
                    data = resp.json()
                    diag.append(f"{layer}={len(data.get('features',[]))}")
                    if data.get("features"):
                        gdf_muns = gpd.GeoDataFrame.from_features(data["features"], crs="EPSG:4674")
                        break
                else:
                    diag.append(f"{layer}=HTTP{resp.status_code}")
            except Exception:
                diag.append(f"{layer}=ERR")
                continue

        if gdf_muns.empty:
            return [{"erro": "Camadas IBGE: " + " | ".join(diag)}]

        utm_crs = gdf_imovel.estimate_utm_crs()
        gdf_imovel_utm = gdf_imovel.to_crs(utm_crs)
        gdf_muns_utm = gdf_muns.to_crs(utm_crs)
        geom_imovel_utm = gdf_imovel_utm.geometry.iloc[0]

        def montar(props, area_ha):
            # ordem prioriza campos de CGMAT:qg_YYYY_030_munic (nm_mun, cd_mun, sigla_uf)
            nome = (props.get('nm_mun') or props.get('nm_municipio') or props.get('NM_MUN')
                    or props.get('nome') or props.get('nome_municipio') or 'Desconhecido')
            cod = (props.get('cd_mun') or props.get('cd_municipio') or props.get('CD_MUN')
                   or props.get('cd_geocodi') or props.get('geocodigo') or props.get('codigo_ibge') or '')
            uf = (props.get('sigla_uf') or props.get('SIGLA_UF') or props.get('uf')
                  or props.get('sigla') or props.get('nm_uf') or '')
            if not uf and cod:
                uf = UF_POR_CODIGO.get(str(cod)[:2], '')
            return {'municipio': f"{nome} - {uf}".strip(" -"), 'nome_puro': nome,
                    'uf': uf, 'area_ha': area_ha, 'cod_ibge': str(cod)}

        resultados, area_total = [], 0
        for _, mun in gdf_muns_utm.iterrows():
            try:
                if not mun.geometry.intersects(geom_imovel_utm):
                    continue
                inter = mun.geometry.intersection(geom_imovel_utm)
                if inter.is_empty:
                    continue
                area_ha = inter.area / 10000
                if area_ha > 0.001:
                    resultados.append(montar(mun.to_dict(), area_ha))
                    area_total += area_ha
            except Exception:
                continue

        # FALLBACK: se a interseção por área falhou, usa o ponto do centroide
        if not resultados:
            ponto = gpd.GeoDataFrame(geometry=[Point(cx, cy)], crs="EPSG:4674").to_crs(utm_crs).geometry.iloc[0]
            for _, mun in gdf_muns_utm.iterrows():
                try:
                    if mun.geometry.contains(ponto) or mun.geometry.intersects(ponto):
                        resultados.append(montar(mun.to_dict(), 0.0))
                        break
                except Exception:
                    continue

        for r in resultados:
            r['porcentagem'] = (r['area_ha'] / area_total) * 100 if area_total > 0 else 100.0
        resultados = sorted(resultados, key=lambda x: x['area_ha'], reverse=True)
        return resultados if resultados else [{"erro": "Camadas IBGE: " + " | ".join(diag) + " | sem cruzamento"}]

    except Exception as e:
        return [{"erro": str(e)}]

@st.cache_data(show_spinner=False)
def get_altimetria_municipio(cod_ibge, lat_fallback, lon_fallback):
    try:
        srtm = ee.Image('USGS/SRTMGL1_003')
        
        try:
            mun_col = ee.FeatureCollection("projects/mapbiomas-workspace/AUXILIAR/municipios-2020")
            municipio = mun_col.filter(ee.Filter.or_(
                ee.Filter.eq('CD_MUN', str(cod_ibge)),
                ee.Filter.eq('CD_MUN', int(cod_ibge))
            )).first()
            geom_alvo = municipio.geometry()
        except Exception:
            geom_alvo = ee.Geometry.Point([lon_fallback, lat_fallback]).buffer(15000)

        stats = srtm.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=geom_alvo,
            scale=90,
            maxPixels=1e13,
            bestEffort=True
        ).getInfo()

        altitude_media = stats.get('elevation', 0)
        if altitude_media is None: altitude_media = 0
        
        return {"media": altitude_media}
    except Exception as e:
        return {"erro": str(e)}

@st.cache_data(show_spinner=False, ttl=86400)
def get_geometria_municipio(cod_ibge):
    """Retorna o geojson do limite municipal (MapBiomas municípios-2020) por
    código IBGE. Usado quando o usuário escolhe manualmente um município entre
    vários que cruzam o imóvel (ex.: na climatologia). Retorna None se falhar."""
    try:
        mun_col = ee.FeatureCollection("projects/mapbiomas-workspace/AUXILIAR/municipios-2020")
        filtros = [ee.Filter.eq('CD_MUN', str(cod_ibge))]
        try:
            filtros.append(ee.Filter.eq('CD_MUN', int(cod_ibge)))
        except (TypeError, ValueError):
            pass
        municipio = mun_col.filter(ee.Filter.or_(*filtros)).first()
        feat = ee.Feature(municipio).getInfo()
        if not feat:
            return None
        return feat.get("geometry")
    except Exception:
        return None

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
    try:
        session = requests.Session()
        
        populacao, area_km2 = None, None
        try:
            r_pop = ibge_api_json(f"https://apisidra.ibge.gov.br/values/t/4714/n6/{cod_ibge}/v/93/p/last%201", session=session)
            if r_pop and len(r_pop) > 1: populacao = float(r_pop[1].get("V"))
        except Exception: pass

        try:
            r_area = ibge_api_json(f"https://servicodados.ibge.gov.br/api/v3/malhas/municipios/{cod_ibge}/metadados", session=session)
            if r_area and len(r_area) > 0: area_km2 = float(r_area[0].get("area", {}).get("dimensao"))
        except Exception: pass

        densidade = (populacao / area_km2) if (populacao and area_km2) else None

        reg_int, reg_ime = "---", "---"
        try:
            bbox = f"{lon-0.001},{lat-0.001},{lon+0.001},{lat+0.001}"
            # Regiões geográficas (Divisão Regional 2017) — camadas CGMAT verificadas via GetCapabilities
            p_int = {"service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": "CGMAT:qg_2025_120_reggeoginterm", "outputFormat": "application/json", "bbox": bbox}
            r_int = ibge_wfs_get(p_int).json()
            if r_int.get("features"): reg_int = r_int["features"][0]["properties"].get("nm_rgint", "---")

            p_ime = {"service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": "CGMAT:qg_2025_110_reggeogimed", "outputFormat": "application/json", "bbox": bbox}
            r_ime = ibge_wfs_get(p_ime).json()
            if r_ime.get("features"): reg_ime = r_ime["features"][0]["properties"].get("nm_rgi", "---")
        except Exception: pass

        return {
            "municipio": nome_oficial, "uf": uf, "area_km2": area_km2, "populacao": populacao, 
            "densidade": densidade, "codigo_ibge": cod_ibge,
            "regiao_intermediaria": reg_int, "regiao_imediata": reg_ime
        }
    except Exception as e: return {"erro": str(e)}

@st.cache_data
def get_bacia_info(lat, lon):
    try:
        bbox = f"{lon-0.01},{lat-0.01},{lon+0.01},{lat+0.01}"

        try:
            r = ibge_wfs_get({"service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": "CREN:bacias_nivel_6", "outputFormat": "application/json", "bbox": bbox}).json()
            props = r["features"][0]["properties"] if r.get("features") else {}
        except Exception: props = {}

        if not props:
            try:
                r = ibge_wfs_get({"service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": "CREN:bacias_nivel_4", "outputFormat": "application/json", "bbox": bbox}).json()
                props = r["features"][0]["properties"] if r.get("features") else {}
            except Exception: pass

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
        # TemporaryDirectory remove o arquivo automaticamente ao sair do bloco
        with tempfile.TemporaryDirectory() as tmp_dir:
            caminho = os.path.join(tmp_dir, "export.kml")
            gdf.to_file(caminho, driver='KML')
            with open(caminho, 'rb') as f:
                return f.read()
    except Exception:
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
        # TemporaryDirectory remove o arquivo automaticamente ao sair do bloco
        with tempfile.TemporaryDirectory() as tmp_dir:
            caminho = os.path.join(tmp_dir, "export.gpkg")
            gdf.to_file(caminho, driver="GPKG")
            with open(caminho, 'rb') as f:
                return f.read()
    except Exception:
        return None

# ==========================================
# 7. LOGÍSTICA E ROTAS (OSRM)
# ==========================================

COORDENADAS_CAPITAIS = {
    'AC': (-9.974, -67.807), 'AL': (-9.665, -35.735), 'AM': (-3.101, -60.025),
    'AP': (0.034, -51.069), 'BA': (-12.971, -38.510), 'CE': (-3.717, -38.543),
    'DF': (-15.779, -47.929), 'ES': (-20.315, -40.312), 'GO': (-16.679, -49.253),
    'MA': (-2.530, -44.302), 'MG': (-19.920, -43.937), 'MS': (-20.442, -54.646),
    'MT': (-15.595, -56.095), 'PA': (-1.455, -48.502), 'PB': (-7.115, -34.863),
    'PE': (-8.047, -34.877), 'PI': (-5.089, -42.801), 'PR': (-25.428, -49.273),
    'RJ': (-22.906, -43.172), 'RN': (-5.794, -35.211), 'RO': (-8.761, -63.903),
    'RR': (2.823, -60.675), 'RS': (-30.027, -51.228), 'SC': (-27.595, -48.548),
    'SE': (-10.947, -37.073), 'SP': (-23.550, -46.633), 'TO': (-10.212, -48.360)
}

NOMES_CAPITAIS = {
    'AC': 'Rio Branco', 'AL': 'Maceió', 'AM': 'Manaus', 'AP': 'Macapá',
    'BA': 'Salvador', 'CE': 'Fortaleza', 'DF': 'Brasília', 'ES': 'Vitória',
    'GO': 'Goiânia', 'MA': 'São Luís', 'MG': 'Belo Horizonte', 'MS': 'Campo Grande',
    'MT': 'Cuiabá', 'PA': 'Belém', 'PB': 'João Pessoa', 'PE': 'Recife',
    'PI': 'Teresina', 'PR': 'Curitiba', 'RJ': 'Rio de Janeiro', 'RN': 'Natal',
    'RO': 'Porto Velho', 'RR': 'Boa Vista', 'RS': 'Porto Alegre', 'SC': 'Florianópolis',
    'SE': 'Aracaju', 'SP': 'São Paulo', 'TO': 'Palmas'
}

LIMITES_ESTADOS = {
    'AC': (-73.99, -11.14, -66.60, -7.11), 'AL': (-38.29, -10.50, -35.15, -8.81),
    'AM': (-73.80, -9.81, -56.10, 2.24), 'AP': (-54.87, -1.23, -49.88, 4.30),
    'BA': (-46.61, -18.34, -37.34, -8.53), 'CE': (-41.41, -7.86, -37.25, -2.78),
    'DF': (-48.28, -16.05, -47.30, -15.50), 'ES': (-41.87, -21.30, -39.66, -17.89),
    'GO': (-53.25, -19.49, -45.90, -12.39), 'MA': (-48.99, -10.26, -41.82, -1.04),
    'MG': (-51.04, -22.92, -39.85, -14.23), 'MS': (-57.83, -24.06, -50.99, -17.16),
    'MT': (-61.60, -18.04, -50.22, -7.84), 'PA': (-58.89, -9.84, -46.06, 2.59),
    'PB': (-38.76, -8.30, -34.79, -6.02), 'PE': (-41.35, -9.48, -34.72, -7.04),
    'PI': (-45.92, -10.92, -41.05, -2.74), 'PR': (-54.61, -26.71, -48.02, -22.51),
    'RJ': (-44.88, -23.36, -40.96, -20.76), 'RN': (-38.58, -6.98, -34.97, -4.83),
    'RO': (-66.80, -13.69, -59.77, -7.96), 'RR': (-64.82, -1.61, -58.88, 5.27),
    'RS': (-57.64, -33.75, -49.69, -27.08), 'SC': (-53.83, -29.35, -48.36, -25.97),
    'SE': (-38.24, -11.49, -36.39, -9.51), 'SP': (-53.11, -25.31, -44.16, -19.77),
    'TO': (-50.74, -13.09, -45.52, -5.16)
}

@st.cache_data(show_spinner=False)
def get_distancia_capital(cod_ibge, uf):
    if uf not in COORDENADAS_CAPITAIS:
        return {"erro": "UF não encontrada."}
    
    lat_cap, lon_cap = COORDENADAS_CAPITAIS[uf]
    
    try:
        url_wfs = "https://geoservicos.ibge.gov.br/geoserver/ows"
        # Camada de pontos de sede verificada via GetCapabilities (workspace CGMAT, estável)
        params_wfs = {
            "service": "WFS", "version": "1.0.0", "request": "GetFeature",
            "typeName": "CGMAT:pbqg22_00_Bc250_2021Cidade", "outputFormat": "application/json",
            "cql_filter": f"geocodigo='{cod_ibge}'"
        }
        r_wfs = None
        for attempt in range(3):
            r_wfs = requests.get(url_wfs, params=params_wfs, timeout=30,
                                 headers={"User-Agent": "Mozilla/5.0"})
            if r_wfs.status_code in (502, 503, 504):
                time.sleep(2 ** attempt)  # servidor instável — espera e tenta de novo
                continue
            break

        if r_wfs.status_code != 200 or not r_wfs.json().get("features"):
            return {"erro": "Sede municipal não localizada no IBGE."}

        coords = r_wfs.json()["features"][0]["geometry"]["coordinates"]
        lon_sede, lat_sede = coords[0], coords[1]

    except Exception as e:
        return {"erro": f"Falha ao buscar sede: {e}"}

    try:
        url_osrm = f"https://router.project-osrm.org/route/v1/driving/{lon_sede},{lat_sede};{lon_cap},{lat_cap}?overview=false"
        r_osrm = requests.get(url_osrm, timeout=10)
        
        if r_osrm.status_code == 200:
            dados = r_osrm.json()
            if dados.get("code") == "Ok":
                dist_km = dados["routes"][0]["distance"] / 1000
                dur_h = dados["routes"][0]["duration"] / 3600
                
                horas = int(dur_h)
                minutos = int((dur_h - horas) * 60)
                tempo_str = f"{horas}h {minutos}m" if horas > 0 else f"{minutos}m"
                
                return {
                    "distancia_km": dist_km,
                    "tempo_estimado": tempo_str
                }
        return {"erro": "Rota indisponível no OSRM."}
    except Exception as e:
        return {"erro": f"Falha no OSRM: {e}"}


@st.cache_data(show_spinner=False)
def _carregar_armazens_conab():
    """Baixa o cadastro de armazéns da CONAB uma vez e devolve os pontos."""
    if gpd is None:
        return None
    url = "https://portaldeinformacoes.conab.gov.br/downloads/arquivos/ArmazensCadastrados.txt"
    try:
        df = pd.read_csv(
            url, delimiter=';', encoding='latin1',
            on_bad_lines='skip', dtype=str, engine='python'
        )
        # Normaliza nomes de coluna (tira espaços acidentais)
        df.columns = [c.strip() for c in df.columns]

        # Função de conversão segura: pega só o primeiro número válido da célula
        def _to_float(serie):
            s = serie.astype(str).str.strip()
            # troca vírgula decimal por ponto
            s = s.str.replace(',', '.', regex=False)
            # se houver mais de um ponto (lixo concatenado), mantém só o primeiro número
            s = s.str.extract(r'(-?\d+\.?\d*)', expand=False)
            return pd.to_numeric(s, errors='coerce')

        df['latitude'] = _to_float(df['latitude'])
        df['longitude'] = _to_float(df['longitude'])
        df = df.dropna(subset=['latitude', 'longitude'])

        # Sanidade: coordenadas do Brasil
        df = df[
            (df['latitude'].between(-34, 6)) &
            (df['longitude'].between(-74, -34))
        ]

        if df.empty:
            return None

        gdf = gpd.GeoDataFrame(
            df, geometry=gpd.points_from_xy(df['longitude'], df['latitude']),
            crs="EPSG:4674"
        )
        return gdf
    except Exception:
        return None


@st.cache_data(show_spinner=False)
def get_armazens_proximos(lat, lon, raio_km=100):
    """Conta armazéns CONAB e soma a capacidade num raio em torno do imóvel."""
    gdf_arm = _carregar_armazens_conab()
    if gdf_arm is None or gdf_arm.empty:
        return {"erro": "Cadastro CONAB indisponível."}
    try:
        ponto = gpd.GeoDataFrame(geometry=gpd.points_from_xy([lon], [lat]), crs="EPSG:4674")
        utm = ponto.estimate_utm_crs()
        buffer_geom = ponto.to_crs(utm).buffer(raio_km * 1000)
        buffer_4674 = gpd.GeoDataFrame(geometry=buffer_geom, crs=utm).to_crs("EPSG:4674")
        dentro = gpd.sjoin(gdf_arm, buffer_4674, how='inner', predicate='intersects')

        col_cap = next(
            (c for c in ['qtd_capacidade_estatica(t)', 'capacidade'] if c in dentro.columns),
            None
        )

        cap = 0.0
        if col_cap:
            cap_serie = (
                dentro[col_cap].astype(str).str.strip()
                .str.replace(',', '.', regex=False)
                .str.extract(r'(-?\d+\.?\d*)', expand=False)
            )
            cap = float(pd.to_numeric(cap_serie, errors='coerce').fillna(0).sum())

        return {"quantidade": len(dentro), "capacidade_t": cap, "raio_km": raio_km}
    except Exception as e:
        return {"erro": f"Falha ao processar armazéns: {e}"}
    
    
# ==========================================
# 8. REDAÇÃO DO LAUDO E CLIMATOLOGIA EXTRA
# ==========================================

def obter_direcao_estado(uf, lat, lon):
    if uf not in LIMITES_ESTADOS: return "região"
    min_lon, min_lat, max_lon, max_lat = LIMITES_ESTADOS[uf]

    terco_lat = (max_lat - min_lat) / 3
    terco_lon = (max_lon - min_lon) / 3

    if lat > max_lat - terco_lat: pos_lat = "norte"
    elif lat < min_lat + terco_lat: pos_lat = "sul"
    else: pos_lat = "central"

    if lon > max_lon - terco_lon: pos_lon = "leste"
    elif lon < min_lon + terco_lon: pos_lon = "oeste"
    else: pos_lon = "central"

    if pos_lat == "central" and pos_lon == "central": return "região central"
    if pos_lat == "central": return f"região {pos_lon}"
    if pos_lon == "central": return f"região {pos_lat}"
    
    if pos_lat == "norte" and pos_lon == "leste": return "região nordeste"
    if pos_lat == "norte" and pos_lon == "oeste": return "região noroeste"
    if pos_lat == "sul" and pos_lon == "leste": return "região sudeste"
    if pos_lat == "sul" and pos_lon == "oeste": return "região sudoeste"
    
    return "região"

def gerar_texto_resumo_laudo(uf, mun_nome, pop, area_km2, lat_dec, lon_dec, lat_dms, lon_dms, altitude, dist_km):
    try:
        direcao = obter_direcao_estado(uf, lat_dec, lon_dec)

        def formata_br(numero, decimais=0):
            try:
                if isinstance(numero, str) and numero == "N/A": return "N/A"
                val = float(numero)
                return f"{val:,.{decimais}f}".replace(",", "X").replace(".", ",").replace("X", ".")
            except Exception:
                return str(numero)

        pop_fmt = formata_br(pop, 0)
        area_fmt = formata_br(area_km2, 3)
        alt_fmt = formata_br(altitude, 0)
        dist_fmt = formata_br(dist_km, 0)

        nome_capital = NOMES_CAPITAIS.get(uf, "a capital")

        if mun_nome.upper() == nome_capital.upper():
            texto = (
                f"{mun_nome} é a capital do estado ({uf}), situada na {direcao}. "
                f"Sua população é de {pop_fmt} habitantes, segundo dados do Censo 2022 do Instituto Brasileiro de Geografia e Estatística (IBGE), "
                f"distribuída em uma área de {area_fmt} quilômetros quadrados. "
                f"Localiza-se à latitude {lat_dms} e à longitude {lon_dms}, com altitude de {alt_fmt} metros."
            )

        else:
            texto = (
                f"{mun_nome} é um município brasileiro situado na {direcao} do estado ({uf}), "
                f"a aproximadamente {dist_fmt} quilômetros da capital, {nome_capital}. "
                f"Sua população é de {pop_fmt} habitantes, segundo dados do Censo 2022 do Instituto Brasileiro de Geografia e Estatística (IBGE), "
                f"distribuída em uma área de {area_fmt} quilômetros quadrados. "
                f"Localiza-se à latitude {lat_dms} e à longitude {lon_dms}, com altitude de {alt_fmt} metros."
            )

        return texto
    except Exception as e:
        return f"Erro ao gerar redação: {str(e)}"

@st.cache_data(show_spinner=False, ttl=86400)
def get_limites_municipio_clima(lon, lat, _geometry_gee=None, cache_id=None):
    """
    Localiza a geometria municipal para a climatologia com múltiplos fallbacks.

    Ordem de tentativa:
    1) MapBiomas municípios-2020 por interseção com o perímetro completo;
    2) MapBiomas municípios-2020 pelo ponto/centroide;
    3) WFS IBGE por interseção espacial, usando várias camadas conhecidas.
    """

    def _limpar_valor(valor):
        if valor is None:
            return ""
        txt = str(valor).strip()
        if txt.lower() in ["", "none", "nan", "null", "<na>"]:
            return ""
        return txt

    def _primeiro(props, campos):
        if not props:
            return ""
        props_lower = {str(k).lower(): v for k, v in props.items()}
        for campo in campos:
            if campo in props:
                val = _limpar_valor(props.get(campo))
                if val:
                    return val
            val = _limpar_valor(props_lower.get(str(campo).lower()))
            if val:
                return val
        return ""

    def _uf_por_codigo(cod):
        cod_txt = "".join(ch for ch in str(cod) if ch.isdigit())
        return UF_POR_CODIGO.get(cod_txt[:2], "") if cod_txt else ""

    def _extrair_nome_uf_cod(props):
        nome = _primeiro(props, [
            "NM_MUN", "nm_mun", "nm_municipio", "nome_municipio",
            "nome", "municipio", "NM_MUNICIPIO", "name"
        ])
        uf = _primeiro(props, [
            "SIGLA_UF", "sigla_uf", "uf", "UF", "sigla", "SIGLA", "nm_uf"
        ])
        cod = _primeiro(props, [
            "CD_MUN", "cd_mun", "cd_municipio", "CD_MUNICIPIO",
            "geocodigo", "cd_geocodi", "codigo_ibge", "cod_ibge"
        ])

        if not uf and cod:
            uf = _uf_por_codigo(cod)

        return nome or "Município", uf or "", str(cod) if cod else ""

    def _json_safe_geometry(geom_mapping):
        try:
            return json.loads(json.dumps(geom_mapping))
        except Exception:
            return geom_mapping

    # 1) Preferencial: MapBiomas por interseção com o perímetro completo.
    try:
        if _geometry_gee is not None:
            geo_simple = _geometry_gee.simplify(maxError=100)
            mun_col = ee.FeatureCollection("projects/mapbiomas-workspace/AUXILIAR/municipios-2020")
            candidatos = mun_col.filterBounds(geo_simple)

            def _add_area_inter(feature):
                inter = feature.geometry().intersection(geo_simple, ee.ErrorMargin(100))
                return feature.set("_area_inter_m2", inter.area(100))

            candidatos = (
                candidatos
                .map(_add_area_inter)
                .filter(ee.Filter.gt("_area_inter_m2", 0))
                .sort("_area_inter_m2", False)
            )

            if candidatos.size().getInfo() > 0:
                feat = ee.Feature(candidatos.first()).getInfo()
                props = feat.get("properties", {})
                nome, uf, cod = _extrair_nome_uf_cod(props)

                return {
                    "geojson": feat.get("geometry"),
                    "nome": nome,
                    "uf": uf,
                    "cod_ibge": cod,
                    "metodo": "MapBiomas municípios-2020 por interseção do perímetro"
                }
    except Exception:
        pass

    # 2) Fallback atual: MapBiomas pelo ponto do centroide.
    try:
        mun_col = ee.FeatureCollection("projects/mapbiomas-workspace/AUXILIAR/municipios-2020")
        ponto = ee.Geometry.Point([float(lon), float(lat)])
        mun_info = mun_col.filterBounds(ponto).limit(1).getInfo().get("features", [])

        if mun_info:
            props = mun_info[0].get("properties", {})
            nome, uf, cod = _extrair_nome_uf_cod(props)

            return {
                "geojson": mun_info[0].get("geometry"),
                "nome": nome,
                "uf": uf,
                "cod_ibge": cod,
                "metodo": "MapBiomas municípios-2020 pelo centroide"
            }
    except Exception:
        pass

    # 3) Fallback IBGE WFS: várias camadas + maior interseção com o imóvel.
    try:
        if gpd is not None and _geometry_gee is not None:
            geojson = _geometry_gee.getInfo()
            geom_shapely = shape(geojson)
            gdf_imovel = gpd.GeoDataFrame({"geometry": [geom_shapely]}, crs="EPSG:4674")
            bounds = gdf_imovel.total_bounds
            bbox = f"{bounds[0]},{bounds[1]},{bounds[2]},{bounds[3]}"

            camadas_mun = [
                "CGMAT:qg_2025_030_munic",
                "CGMAT:qg_2024_030_munic",
                "CGMAT:qg_2023_030_munic",
            ]

            for layer in camadas_mun:
                try:
                    params = {
                        "service": "WFS",
                        "version": "2.0.0",
                        "request": "GetFeature",
                        "typeName": layer,
                        "outputFormat": "application/json",
                        "srsName": "EPSG:4674",
                        "BBOX": f"{bbox},EPSG:4674"
                    }
                    resp = ibge_wfs_get(params)

                    if resp.status_code != 200 or not resp.text.strip().startswith("{"):
                        continue

                    data = resp.json()
                    if not data.get("features"):
                        continue

                    gdf_muns = gpd.GeoDataFrame.from_features(data["features"], crs="EPSG:4674")
                    if gdf_muns.empty:
                        continue

                    if gdf_muns.crs is None:
                        gdf_muns = gdf_muns.set_crs("EPSG:4674", allow_override=True)
                    else:
                        gdf_muns = gdf_muns.to_crs("EPSG:4674")

                    utm_crs = gdf_imovel.estimate_utm_crs()
                    gdf_imovel_utm = gdf_imovel.to_crs(utm_crs)
                    gdf_muns_utm = gdf_muns.to_crs(utm_crs)
                    geom_imovel_utm = gdf_imovel_utm.geometry.iloc[0]

                    areas = []
                    for _, row in gdf_muns_utm.iterrows():
                        try:
                            if not row.geometry.intersects(geom_imovel_utm):
                                areas.append(0.0)
                                continue
                            inter = row.geometry.intersection(geom_imovel_utm)
                            areas.append(0.0 if inter.is_empty else inter.area / 10000)
                        except Exception:
                            areas.append(0.0)

                    gdf_muns = gdf_muns.copy()
                    gdf_muns["_area_inter_ha"] = areas
                    gdf_muns = gdf_muns[gdf_muns["_area_inter_ha"] > 0.05]

                    if gdf_muns.empty:
                        continue

                    row_sel = gdf_muns.sort_values("_area_inter_ha", ascending=False).iloc[0]
                    props = row_sel.drop(labels=["geometry"], errors="ignore").to_dict()
                    nome, uf, cod = _extrair_nome_uf_cod(props)

                    return {
                        "geojson": _json_safe_geometry(mapping(row_sel.geometry)),
                        "nome": nome,
                        "uf": uf,
                        "cod_ibge": cod,
                        "metodo": f"IBGE WFS por interseção ({layer})"
                    }
                except Exception:
                    continue
    except Exception:
        pass

    # 4) Fallback robusto: consulta WFS pelo PONTO (lon/lat), só com shapely.
    #    Não depende de Earth Engine nem de geopandas — última linha de defesa.
    try:
        ponto = Point(float(lon), float(lat))
        m = 0.02  # ~2 km de margem ao redor do ponto
        bbox = f"{lon-m},{lat-m},{lon+m},{lat+m}"
        for layer in ("CGMAT:qg_2025_030_munic", "CGMAT:qg_2024_030_munic", "CGMAT:qg_2023_030_munic"):
            try:
                resp = ibge_wfs_get({
                    "service": "WFS", "version": "2.0.0", "request": "GetFeature",
                    "typeName": layer, "outputFormat": "application/json",
                    "srsName": "EPSG:4674", "BBOX": f"{bbox},EPSG:4674"
                })
                if resp.status_code != 200 or not resp.text.strip().startswith("{"):
                    continue
                feats = resp.json().get("features", [])
                if not feats:
                    continue

                escolhido = None
                for f in feats:
                    try:
                        if shape(f["geometry"]).intersects(ponto):
                            escolhido = f
                            break
                    except Exception:
                        continue
                if escolhido is None:
                    escolhido = feats[0]  # fallback: primeiro do bbox

                props = escolhido.get("properties", {})
                nome, uf, cod = _extrair_nome_uf_cod(props)
                return {
                    "geojson": _json_safe_geometry(escolhido.get("geometry")),
                    "nome": nome,
                    "uf": uf,
                    "cod_ibge": cod,
                    "metodo": f"IBGE WFS pelo ponto ({layer})"
                }
            except Exception:
                continue
    except Exception:
        pass

    return None

