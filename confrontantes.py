import streamlit as st
import geopandas as gpd
import pandas as pd
import requests
import folium
import time
import io
import utils
import ui
import xml.etree.ElementTree as ET
from streamlit_folium import st_folium
from shapely.geometry import shape, Polygon
import ssl
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.poolmanager import PoolManager

# ==========================================
# 1. CONFIGURAÇÃO DE REDE
# ==========================================

class LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False):
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.poolmanager = PoolManager(
            num_pools=connections, maxsize=maxsize,
            block=block, ssl_context=ctx
        )

def get_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    adapter = LegacySSLAdapter(max_retries=retries)
    session.mount('https://', adapter)
    return session


@st.cache_resource(show_spinner=False)
def _incra_session():
    """Sessão HTTP persistente (pool de conexão) para o WFS do INCRA.

    Reaproveita a conexão TLS entre as consultas (por UF e entre SIGEF/SNCI),
    evitando um handshake novo a cada requisição — o que pesa bastante quando
    o app roda longe do servidor (ex.: Streamlit Cloud no exterior -> INCRA BR).
    Vive entre reruns via cache_resource.
    """
    return get_session()

# ==========================================
# 2. CSS LOCAL DA ABA
# ==========================================

CSS_CONFRONTANTES = """
<style>
/* Cards de métrica customizados */
.metric-card {
    background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
    border-radius: 12px;
    padding: 18px 20px;
    border-left: 4px solid #27a64a;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    margin-bottom: 8px;
}
.metric-card.danger { border-left-color: #c0392b; }
.metric-card.warning { border-left-color: #d4a017; }
.metric-card.info { border-left-color: #2C3E50; }

.metric-label {
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #6c757d;
    margin: 0;
}
.metric-value {
    font-size: 2rem;
    font-weight: 700;
    color: #2C3E50;
    margin: 4px 0 0 0;
    line-height: 1;
}
.metric-card.danger .metric-value { color: #c0392b; }
.metric-card.warning .metric-value { color: #d4a017; }

/* Badge de classificação */
.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.4px;
}
.badge-danger {
    background-color: #fbe9e7;
    color: #c0392b;
    border: 1px solid #f5c2bb;
}
.badge-warning {
    background-color: #fff8e1;
    color: #b8860b;
    border: 1px solid #fde7a0;
}

/* Legenda do mapa */
.legenda-mapa {
    background: white;
    padding: 12px 16px;
    border-radius: 8px;
    border: 1px solid #e0e0e0;
    margin-bottom: 12px;
    display: flex;
    gap: 24px;
    align-items: center;
    font-size: 0.85rem;
}
.legenda-item {
    display: flex;
    align-items: center;
    gap: 8px;
}
.legenda-cor {
    width: 16px;
    height: 16px;
    border-radius: 3px;
    border: 2px solid;
}

/* Cabeçalho da aba */
.confront-header {
    background: linear-gradient(135deg, #2C3E50 0%, #1a252f 100%);
    border-radius: 14px;
    padding: 24px 28px;
    color: white;
    margin-bottom: 20px;
    box-shadow: 0 4px 12px rgba(44,62,80,0.15);
}
.confront-header h2 {
    color: white !important;
    margin: 0 0 6px 0 !important;
    font-size: 1.4rem !important;
}
.confront-header p {
    margin: 0;
    opacity: 0.85;
    font-size: 0.92rem;
}

/* Card de detalhes */
.detalhe-card {
    background: white;
    border-radius: 12px;
    padding: 20px 24px;
    border: 1px solid #e9ecef;
    box-shadow: 0 2px 8px rgba(0,0,0,0.03);
}
.detalhe-titulo {
    font-size: 1.1rem;
    font-weight: 700;
    color: #2C3E50;
    margin-bottom: 4px;
}
.detalhe-codigo {
    font-family: 'SF Mono', Monaco, 'Courier New', monospace;
    font-size: 0.85rem;
    color: #27a64a;
    background: #e8f5e9;
    padding: 2px 8px;
    border-radius: 4px;
    display: inline-block;
}
.detalhe-linha {
    display: flex;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid #f0f0f0;
}
.detalhe-linha:last-child { border-bottom: none; }
.detalhe-chave {
    color: #6c757d;
    font-size: 0.88rem;
}
.detalhe-valor {
    color: #2C3E50;
    font-weight: 600;
    font-size: 0.88rem;
}
</style>
"""

# ==========================================
# 3. FUNÇÕES DE DADOS
# ==========================================

def detectar_ufs(gdf):
    """Detecta UFs que o bbox do imóvel toca, com múltiplas camadas + fallback."""
    bounds = gdf.total_bounds
    bbox = f"{bounds[0]},{bounds[1]},{bounds[2]},{bounds[3]}"

    # Camadas de município do IBGE (CGMAT) — trazem 'sigla_uf' por feature.
    # Verificadas via GetCapabilities; substituem as camadas UF antigas (workspaces removidos).
    camadas_uf = [
        "CGMAT:qg_2025_030_munic",
        "CGMAT:qg_2024_030_munic",
        "CGMAT:qg_2023_030_munic",
    ]
    campos_sigla = ['SIGLA_UF', 'sigla_uf', 'sigla', 'SIGLA', 'uf', 'UF', 'sigla_estado']

    # timeout 15s: quando o geoserver do IBGE está com o pool de conexões
    # esgotado ele pendura ~21s por request e devolve erro; 15s aborta antes e
    # cai logo nos fallbacks (o Nominatim e o bbox local). Sem retry aqui: o
    # erro de pool não é 5xx e não melhora repetindo.
    for layer in camadas_uf:
        try:
            params = {
                "service": "WFS", "version": "2.0.0", "request": "GetFeature",
                "typeName": layer, "outputFormat": "application/json",
                "srsName": "EPSG:4674",
                "BBOX": f"{bbox},EPSG:4674"
            }
            resp = requests.get(
                "https://geoservicos.ibge.gov.br/geoserver/ows",
                params=params, timeout=15,
                headers={"User-Agent": "Mozilla/5.0"}
            )
        except Exception:
            # timeout/rede: o geoserver está fora — as outras camadas vão falhar
            # igual, não adianta insistir. Vai direto pros fallbacks.
            break

        if resp.status_code != 200 or not resp.text.strip().startswith("{"):
            # erro de servidor (400/500/pool esgotado) afeta todas as camadas
            # do mesmo geoserver; não gasta 15s por camada tentando as outras.
            break

        data = resp.json()
        ufs = []
        for f in data.get("features", []):
            props = f.get("properties", {})
            for campo in campos_sigla:
                if campo in props and props[campo]:
                    val = str(props[campo]).strip().lower()
                    if len(val) == 2:
                        ufs.append(val)
                        break
        if ufs:
            return list(set(ufs))
        # 200 mas sem UF útil (camada daquele ano vazia/ausente): tenta a próxima.

    # Fallback 1: Nominatim (preciso, mas pode ser bloqueado a partir de IPs de nuvem).
    try:
        centroid = gdf.to_crs("EPSG:4326").unary_union.centroid
        url = f"https://nominatim.openstreetmap.org/reverse?lat={centroid.y}&lon={centroid.x}&format=json&addressdetails=1"
        r = requests.get(url, headers={"User-Agent": "GEOCAPUTI/1.0"}, timeout=10)
        if r.status_code == 200:
            addr = r.json().get("address", {})
            for campo in ["ISO3166-2-lvl4", "state_code"]:
                val = addr.get(campo, "")
                if "BR-" in val:
                    return [val.replace("BR-", "").lower()]
            # Última tentativa: mapear nome do estado
            estado_nome = addr.get("state", "").lower()
            mapa_uf = {
                "acre": "ac", "alagoas": "al", "amapá": "ap", "amazonas": "am",
                "bahia": "ba", "ceará": "ce", "distrito federal": "df",
                "espírito santo": "es", "goiás": "go", "maranhão": "ma",
                "mato grosso": "mt", "mato grosso do sul": "ms", "minas gerais": "mg",
                "pará": "pa", "paraíba": "pb", "paraná": "pr", "pernambuco": "pe",
                "piauí": "pi", "rio de janeiro": "rj", "rio grande do norte": "rn",
                "rio grande do sul": "rs", "rondônia": "ro", "roraima": "rr",
                "santa catarina": "sc", "são paulo": "sp", "sergipe": "se", "tocantins": "to"
            }
            if estado_nome in mapa_uf:
                return [mapa_uf[estado_nome]]
    except Exception:
        pass

    # Fallback 2 (garantido, sem rede): estados cujo bounding box contém o
    # centroide do imóvel. É grosseiro (bboxes de estados se sobrepõem, então
    # pode devolver 2-3 UFs), mas nunca falha — evita o "não foi possível
    # identificar a UF" quando IBGE e Nominatim estão indisponíveis. As
    # consultas por fonte filtram espacialmente, então UFs a mais só custam tempo.
    try:
        c = gdf.to_crs("EPSG:4326").unary_union.centroid
        lon, lat = c.x, c.y
        ufs_bbox = [
            uf.lower() for uf, (x0, y0, x1, y1) in utils.LIMITES_ESTADOS.items()
            if x0 <= lon <= x1 and y0 <= lat <= y1
        ]
        if ufs_bbox:
            return ufs_bbox
    except Exception:
        pass

    return []


def buscar_cars_por_bbox(gdf_imovel, ufs):
    """Busca CARs no SICAR pelo bounding box, com buffer de 500m de segurança."""
    base_url = "https://geoserver.car.gov.br/geoserver/sicar/wfs"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
    session = get_session()

    gdf_proj = gdf_imovel.to_crs(gdf_imovel.estimate_utm_crs())
    gdf_buffer = gdf_proj.buffer(500).to_crs("EPSG:4674")
    bounds = gdf_buffer.total_bounds
    bbox = f"{bounds[0]},{bounds[1]},{bounds[2]},{bounds[3]}"

    gdfs = []
    for uf in ufs:
        try:
            layer = f"sicar:sicar_imoveis_{uf}"
            params = {
                "service": "WFS", "version": "1.0.0", "request": "GetFeature",
                "typeName": layer, "outputFormat": "application/json",
                "BBOX": f"{bbox},EPSG:4674"
            }
            resp = session.get(base_url, params=params, headers=headers, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("features"):
                    gdf_uf = gpd.GeoDataFrame.from_features(data["features"], crs="EPSG:4674")
                    gdf_uf["_uf_fonte"] = uf.upper()
                    gdfs.append(gdf_uf)
        except Exception:
            continue

    if not gdfs:
        return gpd.GeoDataFrame()

    return pd.concat(gdfs, ignore_index=True)


# ==========================================
# 3b. FONTES INCRA (SIGEF / SNCI) — WFS por UF
# ==========================================

INCRA_WFS = "https://acervofundiario.incra.gov.br/i3geo/ogc.php"
TEMA_INCRA = {
    "SIGEF": "certificada_sigef_particular_{uf}",
    "SNCI": "imoveiscertificados_privado_{uf}",
}
NS_GML = "{http://www.opengis.net/gml}"
NS_MS = "http://www.omsug.ca/osgis2004"

# Prioridade de certificação: menor = mais certificado (vence na consolidação).
PRIORIDADE_FONTE = {"SIGEF": 1, "SNCI": 2, "CAR": 3}

# Cores por fonte (distintas entre si e do imóvel principal).
COR_FONTE = {
    "SIGEF": "#b31417",  # vermelho — mais certificado
    "SNCI": "#e8820c",   # laranja
    "CAR": "#7b3fb5",    # roxo
}
COR_IMOVEL = "#00E5FF"   # ciano — perímetro analisado (sempre em destaque)

# Limiar de "mesmo imóvel" na consolidação: sobreposição >= X% da área do menor.
LIMIAR_DUPLICATA = 0.70

# Limiar para considerar uma feição como o PRÓPRIO imóvel (não um vizinho):
# IoU (interseção/união) com o perímetro analisado >= X.
LIMIAR_PROPRIO = 0.90


def _bbox_buffer_str(gdf_wgs, metros=500):
    """Bounds do imóvel com buffer de segurança, em EPSG:4674 (lon,lat)."""
    gdf_proj = gdf_wgs.to_crs(gdf_wgs.estimate_utm_crs())
    gdf_buffer = gdf_proj.buffer(metros).to_crs("EPSG:4674")
    b = gdf_buffer.total_bounds
    return f"{b[0]},{b[1]},{b[2]},{b[3]}"


def _incra_gml_para_gdf(conteudo):
    """Parseia o GML2 do MapServer (INCRA) direto, sem o driver GML do GDAL."""
    try:
        root = ET.fromstring(conteudo)
    except Exception:
        return gpd.GeoDataFrame()

    regs = []
    for fm in root.iter(f"{NS_GML}featureMember"):
        filhos = list(fm)
        if not filhos:
            continue
        feat = filhos[0]
        props, polys = {}, []
        for el in feat.iter():
            tag = el.tag.split('}')[-1]
            if tag == "coordinates" and el.text:
                pts = []
                for par in el.text.split():
                    xy = par.split(',')
                    if len(xy) >= 2:
                        try:
                            pts.append((float(xy[0]), float(xy[1])))
                        except ValueError:
                            continue
                if len(pts) >= 3:
                    polys.append(Polygon(pts))
            elif el.tag.startswith("{" + NS_MS) and el.text and el.text.strip():
                props[tag] = el.text.strip()
        if polys:
            props["geometry"] = polys[0] if len(polys) == 1 else max(polys, key=lambda p: p.area)
            regs.append(props)

    if not regs:
        return gpd.GeoDataFrame()
    return gpd.GeoDataFrame(regs, geometry="geometry", crs="EPSG:4326")


def buscar_incra_por_bbox(bbox_str, ufs, fonte):
    """Consulta SIGEF ou SNCI no WFS do INCRA por bbox, por UF, com retry."""
    tema_fmt = TEMA_INCRA[fonte]
    sessao = _incra_session()  # conexão reaproveitada entre UFs e entre SIGEF/SNCI
    gdfs = []
    for uf in ufs:
        tema = tema_fmt.format(uf=uf.lower())
        params = {
            "tema": tema, "service": "WFS", "version": "1.0.0",
            "request": "GetFeature", "typename": tema, "bbox": bbox_str,
        }
        # timeout 30s: mesmo áreas grandes (~66km / 23 MB) respondem em ~5s quando
        # o INCRA está saudável, então 30s dá folga e ainda falha rápido num
        # travamento (evita spinner preso por 135s/UF quando o servidor pendura).
        resp = None
        for attempt in range(3):
            try:
                resp = sessao.get(INCRA_WFS, params=params, timeout=30,
                                  headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code in (500, 502, 503, 504):
                    time.sleep(2 ** attempt)  # INCRA instável — espera e tenta de novo
                    continue
                break
            except requests.RequestException:
                time.sleep(2 ** attempt)
                resp = None
        if resp is None or resp.status_code != 200:
            continue
        gdf_uf = _incra_gml_para_gdf(resp.content)
        if not gdf_uf.empty:
            gdf_uf["_uf_fonte"] = uf.upper()
            gdfs.append(gdf_uf)

    if not gdfs:
        return gpd.GeoDataFrame()
    out = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs="EPSG:4326")
    return out.to_crs("EPSG:4674")


@st.cache_data(show_spinner=False, ttl=86400)
def _nome_municipio(cod_ibge):
    """Resolve o código IBGE do município para 'Nome - UF' (API de localidades)."""
    cod = "".join(c for c in str(cod_ibge) if c.isdigit())
    if not cod:
        return None
    try:
        r = requests.get(
            f"https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{cod}",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            data = r.json()
            nome = data.get("nome")
            try:
                uf = data["microrregiao"]["mesorregiao"]["UF"]["sigla"]
            except Exception:
                uf = None
            if nome:
                return f"{nome} - {uf}" if uf else nome
    except Exception:
        pass
    return None


def buscar_fonte(gdf_wgs, ufs, fonte):
    """Busca uma fonte (CAR/SIGEF/SNCI) e devolve o gdf com a coluna _fonte
    e a área declarada normalizada (_area_declarada_ha)."""
    if fonte == "CAR":
        gdf = buscar_cars_por_bbox(gdf_wgs, ufs)
    else:
        gdf = buscar_incra_por_bbox(_bbox_buffer_str(gdf_wgs), ufs, fonte)

    if gdf is None or gdf.empty:
        return gpd.GeoDataFrame()

    gdf = gdf.copy()
    gdf["_fonte"] = fonte

    # SIGEF não traz área declarada -> calcula da geometria (UTM).
    if fonte == "SIGEF":
        try:
            utm = gdf.estimate_utm_crs()
            gdf["_area_declarada_ha"] = (gdf.to_crs(utm).geometry.area / 10000).round(2)
        except Exception:
            gdf["_area_declarada_ha"] = None
        # Resolve nome do município (SIGEF só traz o código IBGE), 1x por código.
        if "codigo_municipio" in gdf.columns:
            mapa = {c: _nome_municipio(c) for c in gdf["codigo_municipio"].dropna().unique()}
            gdf["_municipio_nome"] = gdf["codigo_municipio"].map(mapa)
    return gdf


def consolidar_fontes(gdf_all):
    """Deduplica vizinhos entre fontes, mantendo o de maior prioridade de
    certificação (SIGEF > SNCI > CAR). Dois registros são o 'mesmo imóvel' se a
    sobreposição for >= LIMIAR_DUPLICATA da área do menor. Marca '_tambem_em'
    com as fontes equivalentes descartadas."""
    if gdf_all.empty:
        gdf_all = gdf_all.copy()
        gdf_all["_tambem_em"] = []
        return gdf_all

    utm = gdf_all.estimate_utm_crs()
    geoms = gdf_all.to_crs(utm).geometry.tolist()
    areas = [g.area if (g is not None and not g.is_empty) else 0.0 for g in geoms]
    fontes = gdf_all["_fonte"].tolist()
    n = len(gdf_all)

    ordem = sorted(range(n), key=lambda i: PRIORIDADE_FONTE.get(fontes[i], 9))
    usados, manter, tambem = set(), [], {}

    for i in ordem:
        if i in usados:
            continue
        usados.add(i)
        equivalentes = set()
        gi = geoms[i]
        for j in ordem:
            if j in usados or j == i:
                continue
            gj = geoms[j]
            if gi is None or gj is None or gi.is_empty or gj.is_empty:
                continue
            try:
                inter = gi.intersection(gj).area
            except Exception:
                continue
            menor = min(areas[i], areas[j]) or 1.0
            if inter / menor >= LIMIAR_DUPLICATA:
                usados.add(j)
                equivalentes.add(fontes[j])
        manter.append(i)
        tambem[i] = sorted(equivalentes)

    gdf_out = gdf_all.iloc[manter].copy()
    gdf_out["_tambem_em"] = [", ".join(tambem[i]) for i in manter]
    return gdf_out.reset_index(drop=True)


def classificar_imoveis(gdf_cars, gdf_imovel):
    """Classifica cada feição:
    - 'Próprio'      = é o próprio imóvel (IoU >= LIMIAR_PROPRIO com o perímetro);
    - 'Sobreposição' = invade o interior do perímetro;
    - 'Confrontante' = toca a borda."""
    if gdf_cars.empty:
        return gdf_cars

    if gdf_cars.crs != gdf_imovel.crs:
        gdf_cars = gdf_cars.to_crs(gdf_imovel.crs)

    perimetro_real = gdf_imovel.unary_union
    gdf_proj = gdf_imovel.to_crs(gdf_imovel.estimate_utm_crs())
    buffer_toque = gdf_proj.buffer(1).to_crs(gdf_imovel.crs).unary_union

    classificacoes, ious = [], []
    for _, row in gdf_cars.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            classificacoes.append("Outro")
            ious.append(0.0)
            continue

        if geom.intersects(perimetro_real):
            inter = geom.intersection(perimetro_real)
            if not inter.is_empty and inter.area > 1e-10:
                # IoU alto -> é o próprio imóvel cadastrado nesta base, não vizinho.
                uniao = geom.union(perimetro_real).area
                iou = (inter.area / uniao) if uniao > 0 else 0.0
                ious.append(iou)
                classificacoes.append("Próprio" if iou >= LIMIAR_PROPRIO else "Sobreposição")
            else:
                classificacoes.append("Confrontante")
                ious.append(0.0)
        elif geom.intersects(buffer_toque):
            classificacoes.append("Confrontante")
            ious.append(0.0)
        else:
            classificacoes.append("Outro")
            ious.append(0.0)

    gdf_cars["_classificacao"] = classificacoes
    gdf_cars["_iou"] = ious
    gdf_resultado = gdf_cars[
        gdf_cars["_classificacao"].isin(["Próprio", "Sobreposição", "Confrontante"])
    ].copy()
    return gdf_resultado.reset_index(drop=True)


def calcular_area_sobreposicao_ha(geom_car, geom_imovel, utm_crs):
    """Área de sobreposição em hectares, projetando para UTM."""
    try:
        inter = geom_car.intersection(geom_imovel)
        if inter.is_empty:
            return 0.0
        gdf_inter = gpd.GeoDataFrame({'geometry': [inter]}, crs="EPSG:4674")
        gdf_proj = gdf_inter.to_crs(utm_crs)
        return round(gdf_proj.geometry.area.iloc[0] / 10000, 4)
    except Exception:
        return 0.0


# --- Extratores robustos (cientes da fonte: CAR / SIGEF / SNCI) ---
_COLS_CODIGO = {
    "CAR": ['cod_imovel', 'codigo_imovel', 'cod_car', 'codimovel', 'COD_IMOVEL'],
    "SIGEF": ['parcela_codigo', 'codigo_imovel'],
    "SNCI": ['cod_imovel_rural', 'num_certificacao'],
}
_COLS_MUNICIPIO = {
    "CAR": ['nom_municipio', 'municipio', 'cidade', 'nome_municipio', 'NOM_MUNICIPIO'],
    "SIGEF": ['codigo_municipio'],
    "SNCI": [],
}
_COLS_STATUS = {
    "CAR": ['ind_status_imovel', 'situacao', 'status', 'des_condicao_aguardando_analise'],
    "SIGEF": ['status', 'situacao_informada'],
    "SNCI": ['data_certificacao'],
}
_COLS_AREA = {
    "CAR": ['num_area_imovel', 'val_area_imovel', 'area_imovel', 'area_ha', 'nu_area_imovel'],
    "SIGEF": [],
    "SNCI": ['qtd_area_peca_tecnica'],
}


def _primeiro_valido(row, cols):
    for col in cols:
        val = row.get(col)
        if val and str(val).strip() not in ['nan', 'None', '']:
            return str(val).strip()
    return None


def extrair_codigo_car(row):
    fonte = row.get("_fonte", "CAR")
    return _primeiro_valido(row, _COLS_CODIGO.get(fonte, _COLS_CODIGO["CAR"])) or "—"


def extrair_municipio(row):
    fonte = row.get("_fonte", "CAR")
    if fonte == "SIGEF":
        nome = row.get("_municipio_nome")
        if nome and str(nome).strip() not in ['nan', 'None', '']:
            return str(nome).strip()
        cod = _primeiro_valido(row, _COLS_MUNICIPIO["SIGEF"])
        return f"Cód. {cod}" if cod else "—"  # fallback se a API falhar
    val = _primeiro_valido(row, _COLS_MUNICIPIO.get(fonte, _COLS_MUNICIPIO["CAR"]))
    return val or "—"


def extrair_status(row):
    fonte = row.get("_fonte", "CAR")
    return _primeiro_valido(row, _COLS_STATUS.get(fonte, _COLS_STATUS["CAR"])) or "—"


def extrair_area(row):
    # Área já normalizada (ex.: SIGEF calculado da geometria) tem prioridade.
    v = row.get("_area_declarada_ha")
    if v is not None and str(v).strip() not in ['nan', 'None', '']:
        try:
            return round(float(v), 2)
        except Exception:
            pass
    fonte = row.get("_fonte", "CAR")
    val = _primeiro_valido(row, _COLS_AREA.get(fonte, _COLS_AREA["CAR"]))
    if val:
        try:
            return round(float(val.replace(',', '.')), 2)
        except Exception:
            return None
    return None


def casar_matriculas(gdf_cand, matriculas, utm):
    """Casa cada matrícula do imóvel (~100%) com um registro das bases.

    A ANÁLISE de confrontantes é geral (imóvel inteiro); esta função é o passo
    POR MATRÍCULA: para cada matrícula tenta achar o registro (SIGEF/CAR/SNCI)
    que coincide ~100% com ela (IoU >= LIMIAR_PROPRIO) e diz qual é. Esses
    registros casados são os "perímetros principais", que recebem a numeração
    inicial (#1..#N). Um CAR que cobre várias matrículas NÃO casa com uma só
    (IoU baixo) — ele fica como sobreposição na análise geral.

    Args:
      gdf_cand: candidatos das bases (EPSG:4674), com coluna _fonte. Índice reset.
      matriculas: lista de (rotulo, geom_4674) — as matrículas (ou o imóvel único).
      utm: CRS métrico para medir o IoU.

    Retorna (gdf_principais, proprio_idx):
      gdf_principais: 1 linha por matrícula (EPSG:4674), na ORDEM das matrículas,
        com geometry = registro casado de maior prioridade (SIGEF>SNCI>CAR) ou a
        própria matrícula se não casar; colunas _rotulo, _casado, _num,
        _matches (lista {fonte,codigo,iou}) e _match_txt (resumo legível).
      proprio_idx: set de índices de gdf_cand consumidos como 'próprio' de alguma
        matrícula (para excluí-los dos vizinhos/confrontantes).
    """
    proprio_idx = set()
    cand_utm = (gdf_cand.to_crs(utm).geometry.tolist()
                if gdf_cand is not None and not gdf_cand.empty else [])
    linhas = []
    for rotulo, mat_geom in matriculas:
        mat_u = gpd.GeoSeries([mat_geom], crs="EPSG:4674").to_crs(utm).iloc[0]
        melhores = {}  # fonte -> {idx, iou, row}
        for idx in range(len(cand_utm)):
            gu = cand_utm[idx]
            if gu is None or gu.is_empty or not gu.intersects(mat_u):
                continue
            inter = gu.intersection(mat_u).area
            uni = gu.union(mat_u).area
            iou = (inter / uni) if uni > 0 else 0.0
            if iou < LIMIAR_PROPRIO:
                continue
            r = gdf_cand.iloc[idx]
            f = r.get("_fonte", "CAR")
            proprio_idx.add(idx)
            if f not in melhores or iou > melhores[f]["iou"]:
                melhores[f] = {"idx": idx, "iou": iou, "row": r}

        fontes_ord = sorted(melhores, key=lambda f: PRIORIDADE_FONTE.get(f, 9))
        casado = len(fontes_ord) > 0
        if casado:
            geom_princ = melhores[fontes_ord[0]]["row"].geometry
            matches = [{"fonte": f,
                        "codigo": extrair_codigo_car(melhores[f]["row"]),
                        "iou": round(melhores[f]["iou"] * 100, 1)}
                       for f in fontes_ord]
            match_txt = " · ".join(f"{m['fonte']} {m['codigo']} ({m['iou']:.0f}%)"
                                   for m in matches)
        else:
            geom_princ = mat_geom
            matches = []
            match_txt = "não localizada nas bases"

        linhas.append({"_rotulo": rotulo, "_casado": casado,
                        "_matches": matches, "_match_txt": match_txt,
                        "geometry": geom_princ})

    gdf_princ = gpd.GeoDataFrame(linhas, geometry="geometry", crs="EPSG:4674")
    gdf_princ["_num"] = range(1, len(gdf_princ) + 1)
    return gdf_princ, proprio_idx


# ==========================================
# 4. GERAÇÃO DE KML
# ==========================================

def _geom_to_kml_polygons(geom):
    """Converte geometria shapely em tags <Polygon> do KML."""
    if geom is None or geom.is_empty:
        return ""

    if geom.geom_type == 'Polygon':
        polys = [geom]
    elif geom.geom_type == 'MultiPolygon':
        polys = list(geom.geoms)
    else:
        return ""

    parts = []
    for poly in polys:
        if poly.is_empty:
            continue
        coords = " ".join([f"{p[0]},{p[1]},0" for p in poly.exterior.coords])
        parts.append(
            f'<Polygon><outerBoundaryIs><LinearRing>'
            f'<coordinates>{coords}</coordinates>'
            f'</LinearRing></outerBoundaryIs></Polygon>'
        )

    if len(parts) > 1:
        return '<MultiGeometry>' + ''.join(parts) + '</MultiGeometry>'
    if len(parts) == 1:
        return parts[0]
    return ""


# Estilos KML (cores em formato AABBGGRR)
_KML_STYLES = """
    <Style id="imovel">
        <LineStyle><color>ffffe500</color><width>4</width></LineStyle>
        <PolyStyle><color>20ffe500</color></PolyStyle>
    </Style>
    <Style id="sobreposicao">
        <LineStyle><color>ff2b39c0</color><width>3</width></LineStyle>
        <PolyStyle><color>662b39c0</color></PolyStyle>
    </Style>
    <Style id="confrontante">
        <LineStyle><color>ff17a0d4</color><width>3</width></LineStyle>
        <PolyStyle><color>4017a0d4</color></PolyStyle>
    </Style>
"""


def _placemark(num, nome, descricao, style_id, geom):
    """Gera um Placemark KML."""
    coords = _geom_to_kml_polygons(geom)
    if not coords:
        return ""
    desc_safe = (descricao
                 .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
    return (
        '<Placemark>'
        f'<name>#{num} - {nome}</name>'
        f'<description><![CDATA[{desc_safe}]]></description>'
        f'<styleUrl>#{style_id}</styleUrl>'
        f'{coords}'
        '</Placemark>'
    )


def gerar_kml_completo(gdf_princ, gdf_res, codigo_imovel):
    """KML único: perímetros principais (matrículas) + confrontantes/sobreposições,
    numerados na mesma sequência (#1..#N principais, depois os vizinhos)."""
    placemarks = []
    if gdf_princ is not None and not gdf_princ.empty:
        for _, p in gdf_princ.iterrows():
            pnum = int(p["_num"])
            prot = str(p["_rotulo"])
            desc = ("Perímetro principal (matrícula)\n"
                    f"Matrícula: {prot}\n"
                    f"Correspondência: {p['_match_txt']}")
            placemarks.append(
                _placemark(pnum, f"Principal · {prot}", desc, "imovel", p.geometry))

    for idx, row in gdf_res.iterrows():
        num = int(row["_num"])
        classificacao = row["_classificacao"]
        cod = extrair_codigo_car(row)
        mun = extrair_municipio(row)
        status = extrair_status(row)
        area = extrair_area(row)
        area_sob = row.get("_area_sobreposicao_ha", 0)
        fonte = row.get("_fonte", "CAR")
        tambem = row.get("_tambem_em", "")

        desc = (
            f"Tipo: {classificacao}\n"
            f"Fonte: {fonte}" + (f" (também em {tambem})" if tambem else "") + "\n"
            f"Código: {cod}\n"
            f"Município: {mun}\n"
            f"Área Declarada: {area if area else '—'} ha\n"
            f"Status: {status}"
        )
        if classificacao == "Sobreposição":
            desc += f"\nÁrea Sobreposta: {area_sob:.4f} ha"

        style = "sobreposicao" if classificacao == "Sobreposição" else "confrontante"
        nome = f"{fonte} · {classificacao} - {cod}"

        placemarks.append(_placemark(num, nome, desc, style, row.geometry))

    kml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2">'
        '<Document>'
        f'<name>Confrontantes_{codigo_imovel}</name>'
        f'{_KML_STYLES}'
        + ''.join(placemarks) +
        '</Document></kml>'
    )
    return kml.encode('utf-8')


def gerar_kml_individual(row, num):
    """KML de um único registro (confrontante ou sobreposição)."""
    classificacao = row["_classificacao"]
    cod = extrair_codigo_car(row)
    mun = extrair_municipio(row)
    status = extrair_status(row)
    area = extrair_area(row)
    area_sob = row.get("_area_sobreposicao_ha", 0)
    fonte = row.get("_fonte", "CAR")
    tambem = row.get("_tambem_em", "")

    desc = (
        f"Tipo: {classificacao}\n"
        f"Fonte: {fonte}" + (f" (também em {tambem})" if tambem else "") + "\n"
        f"Código: {cod}\n"
        f"Município: {mun}\n"
        f"Área Declarada: {area if area else '—'} ha\n"
        f"Status: {status}"
    )
    if classificacao == "Sobreposição":
        desc += f"\nÁrea Sobreposta: {area_sob:.4f} ha"

    style = "sobreposicao" if classificacao == "Sobreposição" else "confrontante"
    nome = f"{fonte} · {classificacao} - {cod}"

    kml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2">'
        '<Document>'
        f'<name>{cod}</name>'
        f'{_KML_STYLES}'
        + _placemark(num, nome, desc, style, row.geometry) +
        '</Document></kml>'
    )
    return kml.encode('utf-8')


# ==========================================
# 5. RENDERIZAÇÃO
# ==========================================

def _det_linha(chave, valor, cor=None):
    """Uma linha chave/valor do card de detalhe (HTML em linha única)."""
    estilo = f' style="color:{cor};"' if cor else ''
    return (f'<div class="detalhe-linha"><span class="detalhe-chave">{chave}</span>'
            f'<span class="detalhe-valor"{estilo}>{valor}</span></div>')


def render_tab():
    st.markdown(CSS_CONFRONTANTES, unsafe_allow_html=True)

    # --- Cabeçalho da aba ---
    st.markdown("""
        <div class="confront-header">
            <h2>🗺️ Confrontantes e Sobreposições</h2>
            <p>Identifica imóveis (CAR, SIGEF e SNCI) que tocam ou se sobrepõem ao perímetro do imóvel analisado.</p>
        </div>
    """, unsafe_allow_html=True)

    # --- Verifica imóvel carregado ---
    gdf_imovel = st.session_state.get('gdf_imovel')
    if gdf_imovel is None or not isinstance(gdf_imovel, gpd.GeoDataFrame) or gdf_imovel.empty:
        ui.vazio()
        return

    codigo_display = st.session_state.get('last_code', 'Imóvel Carregado')
    ui.barra_imovel(nome=codigo_display)

    # A ANÁLISE é sempre GERAL (imóvel inteiro / união das matrículas). O passo
    # POR MATRÍCULA fica só no casamento ~100% (casar_matriculas): cada matrícula
    # acha o registro que coincide com ela e vira um "perímetro principal"
    # numerado no início (#1..#N); os vizinhos vêm depois.
    feats = st.session_state.get('gdf_features')

    # --- Seletor de fonte ---
    fonte_sel = st.radio(
        "Fonte de dados",
        ["CAR", "SIGEF", "SNCI", "Consolidado"],
        index=3, horizontal=True, key="confront_fonte",
        help=("CAR = ambiental (autodeclarado). SIGEF/SNCI = georreferenciados "
              "certificados. Consolidado cruza as três fontes e mantém, para cada "
              "vizinho, a mais certificada (SIGEF > SNCI > CAR).")
    )

    # --- Painel de ação ---
    col_info, col_btn = st.columns([2.2, 1], vertical_alignment="center")
    with col_info:
        st.caption("Busca imóveis (CAR/SIGEF/SNCI) que tocam ou se sobrepõem ao "
                   "perímetro, na(s) UF(s) do imóvel.")
    with col_btn:
        rodar = st.button("🔍 Buscar Confrontantes", type="primary", use_container_width=True)

    if rodar:
        st.session_state['confrontantes_resultado'] = None
        st.session_state['confrontantes_principais'] = None
        st.session_state['confrontantes_proprio'] = None  # bloco antigo aposentado
        st.session_state['confrontantes_done'] = False

        with st.spinner("Detectando UFs do imóvel..."):
            gdf_wgs = gdf_imovel.to_crs("EPSG:4674") if gdf_imovel.crs.to_epsg() != 4674 else gdf_imovel.copy()
            ufs = detectar_ufs(gdf_wgs)

        if not ufs:
            st.error("Não foi possível identificar a UF do imóvel. Verifique o KML carregado.")
            return

        st.caption(f"UFs detectadas: **{', '.join([u.upper() for u in ufs])}**")

        fontes_alvo = ["CAR", "SIGEF", "SNCI"] if fonte_sel == "Consolidado" else [fonte_sel]
        cand_partes, diag = [], []
        with st.spinner(f"Consultando {fonte_sel} ({', '.join(u.upper() for u in ufs)})..."):
            for f in fontes_alvo:
                # Cronometra cada fonte: como o INCRA é lento a partir de servidor
                # no exterior (Streamlit Cloud), o tempo por fonte no diagnóstico
                # ajuda a ver onde o gargalo realmente está.
                t0 = time.time()
                gdf_f = buscar_fonte(gdf_wgs, ufs, f)  # candidatos crus
                dt = time.time() - t0
                n = 0 if gdf_f is None or gdf_f.empty else len(gdf_f)
                diag.append(f"{f}: {n} ({dt:.0f}s)")
                if n:
                    cand_partes.append(gdf_f)

        st.caption("Encontrados por fonte → " + " · ".join(diag))

        if not cand_partes:
            st.warning("Nenhum confrontante/sobreposição encontrado (ou as bases estão indisponíveis no momento).")
            return

        gdf_cand = gpd.GeoDataFrame(
            pd.concat(cand_partes, ignore_index=True), crs="EPSG:4674"
        ).reset_index(drop=True)

        utm_crs = gdf_wgs.estimate_utm_crs()

        # --- PASSO POR MATRÍCULA: casa cada matrícula (~100%) com um registro. ---
        # Vira "perímetro principal" e recebe a numeração inicial (#1..#N).
        if feats is not None and len(feats) > 1:
            feats_4674 = feats.to_crs("EPSG:4674").reset_index(drop=True)
            matriculas = [(str(feats.iloc[i]["_rotulo"]), feats_4674.geometry.iloc[i])
                          for i in range(len(feats_4674))]
        else:
            matriculas = [(codigo_display, gdf_wgs.unary_union)]

        gdf_princ, proprio_idx = casar_matriculas(gdf_cand, matriculas, utm_crs)
        n_princ = len(gdf_princ)

        # --- ANÁLISE GERAL: vizinhos contra a UNIÃO, tirando os já casados. ---
        gdf_viz_cand = gdf_cand.drop(index=list(proprio_idx)).reset_index(drop=True)
        gdf_all = (classificar_imoveis(gdf_viz_cand, gdf_wgs)
                   if not gdf_viz_cand.empty else gdf_viz_cand)

        if not gdf_all.empty and "_classificacao" in gdf_all.columns:
            gdf_vizinhos = gdf_all[
                gdf_all["_classificacao"].isin(["Sobreposição", "Confrontante"])
            ].copy()
        else:
            gdf_vizinhos = gdf_all.copy()

        if fonte_sel == "Consolidado":
            gdf_resultado = consolidar_fontes(gdf_vizinhos)
        else:
            gdf_resultado = gdf_vizinhos.copy()
            gdf_resultado["_tambem_em"] = ""

        if gdf_resultado.empty and n_princ == 0:
            st.success("Nenhum confrontante ou sobreposição encontrado na área consultada.")
            return

        # Área de sobreposição (só vizinhos), contra a união.
        perimetro_real = gdf_wgs.unary_union
        areas_sob = [
            calcular_area_sobreposicao_ha(row.geometry, perimetro_real, utm_crs)
            if row["_classificacao"] == "Sobreposição" else 0.0
            for _, row in gdf_resultado.iterrows()
        ] if not gdf_resultado.empty else []
        gdf_resultado["_area_sobreposicao_ha"] = areas_sob

        # NUMERAÇÃO: principais #1..#N; vizinhos a partir de N+1.
        # Consolidado: ordem por prioridade de fonte (SIGEF > SNCI > CAR), e
        # dentro de cada fonte sobreposição antes de confrontante.
        if not gdf_resultado.empty:
            gdf_resultado["_ordem_classe"] = gdf_resultado["_classificacao"].apply(
                lambda x: 0 if x == "Sobreposição" else 1
            )
            if fonte_sel == "Consolidado":
                gdf_resultado["_ordem_fonte"] = (
                    gdf_resultado["_fonte"].map(PRIORIDADE_FONTE).fillna(9)
                )
                gdf_resultado = gdf_resultado.sort_values(
                    ["_ordem_fonte", "_ordem_classe"]
                ).reset_index(drop=True)
                gdf_resultado = gdf_resultado.drop(columns=["_ordem_fonte"])
            else:
                gdf_resultado = gdf_resultado.sort_values("_ordem_classe").reset_index(drop=True)
            gdf_resultado["_num"] = range(n_princ + 1, len(gdf_resultado) + n_princ + 1)
            gdf_resultado = gdf_resultado.drop(columns=["_ordem_classe"])

        # Garante colunas mesmo quando só há principais (sem vizinhos), para o render.
        for _c in ("_classificacao", "_fonte", "_tambem_em"):
            if _c not in gdf_resultado.columns:
                gdf_resultado[_c] = pd.Series([], dtype=object)

        st.session_state['confrontantes_resultado'] = gdf_resultado
        st.session_state['confrontantes_principais'] = gdf_princ
        st.session_state['confrontantes_imovel_wgs'] = gdf_wgs
        st.session_state['confrontantes_fonte'] = fonte_sel
        st.session_state['confrontantes_done'] = True
        st.rerun()

    # --- Resultados ---
    if not (st.session_state.get('confrontantes_done') and st.session_state.get('confrontantes_resultado') is not None):
        st.info("👆 Clique em **Buscar Confrontantes** para iniciar a análise.")
        return

    gdf_res = st.session_state['confrontantes_resultado']
    gdf_wgs = st.session_state['confrontantes_imovel_wgs']
    # Compatibilidade: cria numeração se vier de sessão antiga
    if "_num" not in gdf_res.columns:
        gdf_res = gdf_res.copy()
        gdf_res["_ordem"] = gdf_res["_classificacao"].apply(
            lambda x: 0 if x == "Sobreposição" else 1
        )
        gdf_res = gdf_res.sort_values("_ordem").reset_index(drop=True)
        gdf_res["_num"] = range(2, len(gdf_res) + 2)
        gdf_res = gdf_res.drop(columns=["_ordem"])
        st.session_state['confrontantes_resultado'] = gdf_res

    # Compatibilidade com sessões anteriores (só tinham CAR)
    if "_fonte" not in gdf_res.columns or "_tambem_em" not in gdf_res.columns:
        gdf_res = gdf_res.copy()
        if "_fonte" not in gdf_res.columns:
            gdf_res["_fonte"] = "CAR"
        if "_tambem_em" not in gdf_res.columns:
            gdf_res["_tambem_em"] = ""
        st.session_state['confrontantes_resultado'] = gdf_res

    fonte_ativa = st.session_state.get('confrontantes_fonte', 'CAR')

    n_sob = len(gdf_res[gdf_res["_classificacao"] == "Sobreposição"])
    n_conf = len(gdf_res[gdf_res["_classificacao"] == "Confrontante"])
    area_total_sob = gdf_res["_area_sobreposicao_ha"].sum()

    st.markdown("---")

    # --- Bloco: perímetros principais (cada matrícula ↔ registro casado ~100%) ---
    gdf_princ = st.session_state.get('confrontantes_principais')
    if gdf_princ is not None and not gdf_princ.empty:
        n_casadas = int(gdf_princ["_casado"].sum())
        linhas_p = []
        for _, p in gdf_princ.iterrows():
            n = int(p["_num"])
            rot = p["_rotulo"]
            if p["_casado"]:
                linhas_p.append(f"**#{n} · {rot}** → {p['_match_txt']}")
            else:
                linhas_p.append(f"**#{n} · {rot}** → ⚠️ não localizada nas bases")
        titulo = (
            f"📌 **Perímetros principais** — {len(gdf_princ)} matrícula(s), "
            f"{n_casadas} casada(s) ~100% com o registro correspondente "
            "(recebem a numeração inicial; não contam como sobreposição):"
        )
        st.success(titulo + "  \n" + "  \n".join(linhas_p))

    # --- Cards de métricas ---
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"""
            <div class="metric-card info">
                <p class="metric-label">Total Encontrados</p>
                <p class="metric-value">{len(gdf_res)}</p>
            </div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
            <div class="metric-card danger">
                <p class="metric-label">Sobreposições</p>
                <p class="metric-value">{n_sob}</p>
            </div>
        """, unsafe_allow_html=True)
    with c3:
        st.markdown(f"""
            <div class="metric-card warning">
                <p class="metric-label">Confrontantes</p>
                <p class="metric-value">{n_conf}</p>
            </div>
        """, unsafe_allow_html=True)
    with c4:
        area_fmt = f"{area_total_sob:.2f}".replace(".", ",") if area_total_sob > 0 else "0,00"
        st.markdown(f"""
            <div class="metric-card danger">
                <p class="metric-label">Área Sobreposta (ha)</p>
                <p class="metric-value">{area_fmt}</p>
            </div>
        """, unsafe_allow_html=True)

    # --- Quebra por fonte ---
    vc = gdf_res["_fonte"].value_counts()
    chips = " · ".join(f"**{k}** {int(v)}" for k, v in vc.items())
    modo = "Consolidado (SIGEF › SNCI › CAR)" if fonte_ativa == "Consolidado" else fonte_ativa
    st.caption(f"Modo: **{modo}**  |  Por fonte → {chips}")

    st.write("")

    # --- Legenda (cor = fonte) ---
    st.markdown("""
        <div class="legenda-mapa">
            <div class="legenda-item">
                <div class="legenda-cor" style="background:#00E5FF22;border-color:#00E5FF;"></div>
                <span><b>Imóvel Analisado</b></span>
            </div>
            <div class="legenda-item">
                <div class="legenda-cor" style="background:#b3141766;border-color:#b31417;"></div>
                <span><b>SIGEF</b></span>
            </div>
            <div class="legenda-item">
                <div class="legenda-cor" style="background:#e8820c66;border-color:#e8820c;"></div>
                <span><b>SNCI</b></span>
            </div>
            <div class="legenda-item">
                <div class="legenda-cor" style="background:#7b3fb566;border-color:#7b3fb5;"></div>
                <span><b>CAR</b></span>
            </div>
        </div>
    """, unsafe_allow_html=True)
    st.caption("Cor indica a fonte. Preenchimento mais forte = **sobreposição** (invade o perímetro); mais leve = **confrontante** (toca a borda).")

    # --- Layout: Mapa + Tabela ---
    col_mapa, col_tabela = st.columns([1.7, 1], gap="medium")

    with col_mapa:
        centro = [gdf_wgs.unary_union.centroid.y, gdf_wgs.unary_union.centroid.x]
        m = folium.Map(location=centro, zoom_start=13, tiles=None)

        folium.TileLayer(
            tiles="Esri World Imagery",
            name="Satélite",
            attr="Esri"
        ).add_to(m)
        folium.TileLayer("OpenStreetMap", name="Mapa").add_to(m)

        # ---- 1º: CARs (cada um em seu FeatureGroup numerado) ----
        for _, row in gdf_res.iterrows():
            classificacao = row["_classificacao"]
            num = int(row["_num"])

            fonte = row.get("_fonte", "CAR")
            cor = COR_FONTE.get(fonte, "#666666")
            if classificacao == "Sobreposição":
                emoji = "🔴"
                peso = 3
                fill_opacity = 0.45
            else:
                emoji = "🟡"
                peso = 2
                fill_opacity = 0.15

            cod = extrair_codigo_car(row)
            mun = extrair_municipio(row)
            status = extrair_status(row)
            area = extrair_area(row)
            area_str = f"{area:.2f} ha" if area else "—"
            area_sob = row.get("_area_sobreposicao_ha", 0)
            sob_str = f"<br><b>Área Sobreposta:</b> {area_sob:.4f} ha" if classificacao == "Sobreposição" else ""

            tambem = row.get("_tambem_em", "")
            fonte_str = f"<br><b>Fonte:</b> {fonte}" + (f" <i>(também em {tambem})</i>" if tambem else "")

            cod_short = cod[:25] + "..." if len(cod) > 25 else cod
            fg_nome = f"{emoji} #{num} · {fonte} · {classificacao} ({cod_short})"
            fg_item = folium.FeatureGroup(name=fg_nome, show=True)

            tooltip_html = f"""
                <div style="font-family:sans-serif;font-size:12px;line-height:1.5;min-width:220px;">
                    <div style="font-weight:700;color:{cor};margin-bottom:4px;text-transform:uppercase;">
                        #{num} · {classificacao}
                    </div>
                    <b>Código:</b> <span style="font-family:monospace;">{cod}</span><br>
                    <b>Município:</b> {mun}<br>
                    <b>Área:</b> {area_str}<br>
                    <b>Status:</b> {status}
                    {fonte_str}
                    {sob_str}
                </div>
            """

            try:
                # Polígono
                folium.GeoJson(
                    row.geometry.__geo_interface__,
                    style_function=lambda x, c=cor, p=peso, fo=fill_opacity: {
                        'color': c, 'weight': p,
                        'fillColor': c, 'fillOpacity': fo
                    },
                    tooltip=folium.Tooltip(tooltip_html)
                ).add_to(fg_item)

                # Marcador numérico no centroide
                centroide = row.geometry.centroid
                folium.Marker(
                    location=[centroide.y, centroide.x],
                    icon=folium.DivIcon(
                        html=(
                            f'<div style="background:{cor};color:white;'
                            f'border-radius:50%;width:30px;height:30px;'
                            f'display:flex;align-items:center;justify-content:center;'
                            f'font-weight:700;font-size:13px;font-family:sans-serif;'
                            f'border:2.5px solid white;'
                            f'box-shadow:0 2px 6px rgba(0,0,0,0.4);">'
                            f'{num}</div>'
                        ),
                        icon_size=(30, 30),
                        icon_anchor=(15, 15)
                    )
                ).add_to(fg_item)
            except Exception:
                continue

            fg_item.add_to(m)

        # ---- 2º: Perímetros principais (matrículas casadas) POR ÚLTIMO ----
        # Cada matrícula é desenhada com a geometria do registro casado (ou a
        # própria matrícula, tracejada fina, se não casou), com seu número inicial.
        gdf_princ_map = st.session_state.get('confrontantes_principais')
        if gdf_princ_map is not None and not gdf_princ_map.empty:
            for _, p in gdf_princ_map.iterrows():
                pnum = int(p["_num"])
                prot = str(p["_rotulo"])
                pcasado = bool(p["_casado"])
                ptxt = p["_match_txt"]
                geo = p.geometry.__geo_interface__
                fg_p = folium.FeatureGroup(name=f"🔷 #{pnum} - {prot}", show=True)

                folium.GeoJson(geo, style_function=lambda x: {
                    'color': '#003844', 'weight': 9, 'fill': False, 'opacity': 0.55
                }).add_to(fg_p)
                folium.GeoJson(
                    geo,
                    style_function=lambda x, dash=('4, 8' if not pcasado else '10, 6'): {
                        'color': COR_IMOVEL, 'weight': 5, 'fillColor': COR_IMOVEL,
                        'fillOpacity': 0.06, 'dashArray': dash
                    },
                    tooltip=folium.Tooltip(f"<b>#{pnum} · {prot}</b><br>{ptxt}")
                ).add_to(fg_p)

                cent = p.geometry.centroid
                folium.Marker(
                    location=[cent.y, cent.x],
                    icon=folium.DivIcon(
                        html=(
                            f'<div style="background:{COR_IMOVEL};color:#003844;'
                            'border-radius:50%;width:38px;height:38px;'
                            'display:flex;align-items:center;justify-content:center;'
                            'font-weight:800;font-size:16px;font-family:sans-serif;'
                            'border:3px solid white;'
                            f'box-shadow:0 2px 10px rgba(0,0,0,0.5);">{pnum}</div>'
                        ),
                        icon_size=(38, 38), icon_anchor=(19, 19)
                    )
                ).add_to(fg_p)
                fg_p.add_to(m)
            n_princ_map = len(gdf_princ_map)
        else:
            # Fallback (sessão sem principais): desenha o imóvel como #1.
            fg_imovel = folium.FeatureGroup(name="🔷 #1 - Imóvel Analisado", show=True)
            folium.GeoJson(gdf_wgs, style_function=lambda x: {
                'color': '#003844', 'weight': 9, 'fill': False, 'opacity': 0.55}).add_to(fg_imovel)
            folium.GeoJson(gdf_wgs, style_function=lambda x: {
                'color': COR_IMOVEL, 'weight': 5, 'fillColor': COR_IMOVEL,
                'fillOpacity': 0.06, 'dashArray': '10, 6'},
                tooltip=folium.Tooltip(f"<b>#1 · Imóvel Analisado:</b> {codigo_display}")).add_to(fg_imovel)
            cent_imovel = gdf_wgs.unary_union.centroid
            folium.Marker(location=[cent_imovel.y, cent_imovel.x], icon=folium.DivIcon(
                html=(f'<div style="background:{COR_IMOVEL};color:#003844;border-radius:50%;'
                      'width:40px;height:40px;display:flex;align-items:center;'
                      'justify-content:center;font-weight:800;font-size:17px;'
                      'font-family:sans-serif;border:3px solid white;'
                      'box-shadow:0 2px 10px rgba(0,0,0,0.5);">1</div>'),
                icon_size=(40, 40), icon_anchor=(20, 20))).add_to(fg_imovel)
            fg_imovel.add_to(m)
            n_princ_map = 1

        folium.LayerControl(collapsed=True).add_to(m)

        # Key dinâmica força re-mount limpo do mapa
        map_key = f"mapa_conf_{len(gdf_res)}_{n_princ_map}"
        st_folium(m, height=ui.MAPA_H_INTER, use_container_width=True, key=map_key)

    with col_tabela:
        ui.secao("Registros Encontrados")
        st.caption("Selecione uma linha para ver detalhes e baixar")

        # Botão de download completo (principais + todos os vizinhos)
        _n_princ_kml = 0 if gdf_princ is None or gdf_princ.empty else len(gdf_princ)
        kml_completo = gerar_kml_completo(gdf_princ, gdf_res, codigo_display)
        st.download_button(
            label=f"📥 Baixar KML Completo ({len(gdf_res) + _n_princ_kml} feições)",
            data=kml_completo,
            file_name=f"confrontantes_{codigo_display.replace(' ', '_').replace(':', '')}.kml",
            mime="application/vnd.google-earth.kml+xml",
            use_container_width=True,
            key="dl_kml_completo"
        )

        st.write("")

        mostrar_tambem = fonte_ativa == "Consolidado"
        linhas = []
        for _, row in gdf_res.iterrows():
            num = int(row["_num"])
            cod = extrair_codigo_car(row)
            mun = extrair_municipio(row)
            area = extrair_area(row)
            classificacao = row["_classificacao"]
            area_sob = row.get("_area_sobreposicao_ha", 0)
            fonte = row.get("_fonte", "CAR")
            tambem = row.get("_tambem_em", "")

            icone = "🔴" if classificacao == "Sobreposição" else "🟡"

            registro = {
                "#": num,
                "Tipo": f"{icone} {classificacao}",
                "Fonte": fonte,
                "Código": cod,
                "Município": mun,
                "Área (ha)": area if area else "—",
                "Sob. (ha)": round(area_sob, 4) if classificacao == "Sobreposição" else "—",
            }
            if mostrar_tambem:
                registro["Também em"] = tambem or "—"
            linhas.append(registro)

        df_display = pd.DataFrame(linhas)

        event = st.dataframe(
            df_display,
            use_container_width=True,
            selection_mode="single-row",
            on_select="rerun",
            height=440,
            hide_index=True,
            # Coluna larga por padrão: os códigos (UUID do SIGEF ~36 chars,
            # CAR longo) cabem inteiros; o usuário ainda pode redimensionar.
            column_config={"Código": st.column_config.TextColumn("Código", width="large")},
            key="tabela_confrontantes"
        )

        # Download explícito em Excel: o botão nativo do st.dataframe gera um CSV
        # separado por vírgula que o Excel pt-BR abre tudo numa coluna só. O .xlsx
        # já vem em colunas de verdade e com os códigos completos (sem reticências).
        buffer_xlsx = io.BytesIO()
        with pd.ExcelWriter(buffer_xlsx, engine="xlsxwriter") as writer:
            df_display.to_excel(writer, index=False, sheet_name="Confrontantes")
            ws = writer.sheets["Confrontantes"]
            # Largura de coluna proporcional ao maior conteúdo (código cabe inteiro).
            for i, col in enumerate(df_display.columns):
                largura = max(len(str(col)), df_display[col].astype(str).map(len).max())
                ws.set_column(i, i, min(largura + 2, 60))

        st.download_button(
            "📥 Baixar planilha (Excel) — códigos completos",
            data=buffer_xlsx.getvalue(),
            file_name=f"confrontantes_{fonte_ativa.lower()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="dl_planilha_confrontantes"
        )
        st.caption(
            "Use este botão para a planilha em colunas com os códigos inteiros. "
            "O ícone de download no canto da tabela gera um CSV que o Excel pt-BR "
            "não separa em colunas."
        )

    # --- Detalhe do registro selecionado ---
    if len(event.selection.rows) > 0:
        idx_sel = event.selection.rows[0]

        # gdf_res já está ordenado e numerado no session_state
        if idx_sel < len(gdf_res):
            row_sel = gdf_res.iloc[idx_sel]
            num_sel = int(row_sel["_num"])

            st.markdown("---")
            st.markdown(f"##### 📋 Detalhes do Registro #{num_sel}")

            cod = extrair_codigo_car(row_sel)
            mun = extrair_municipio(row_sel)
            status = extrair_status(row_sel)
            area = extrair_area(row_sel)
            classificacao = row_sel["_classificacao"]
            area_sob = row_sel.get("_area_sobreposicao_ha", 0)

            badge_class = "badge-danger" if classificacao == "Sobreposição" else "badge-warning"

            col_det_info, col_det_mapa = st.columns([1, 1.3], gap="medium")

            with col_det_info:
                area_fmt = f"{area:.2f} ha" if area else "—"
                uf_fonte = row_sel.get('_uf_fonte', '—')
                fonte_det = row_sel.get('_fonte', 'CAR')
                tambem_det = row_sel.get('_tambem_em', '')

                linhas_html = [
                    _det_linha("UF", uf_fonte),
                    _det_linha("Área Declarada", area_fmt),
                    _det_linha("Status", status),
                ]
                if tambem_det:
                    linhas_html.append(_det_linha("Também consta em", tambem_det))
                if classificacao == "Sobreposição":
                    linhas_html.append(_det_linha("Área Sobreposta", f"{area_sob:.4f} ha", "#c0392b"))

                # HTML em string única (sem linhas indentadas/vazias que o
                # Markdown interpretaria como bloco de código).
                card_html = (
                    '<div class="detalhe-card">'
                    '<div style="display:flex;align-items:center;gap:10px;">'
                    '<span style="background:#2C3E50;color:white;border-radius:50%;width:32px;'
                    'height:32px;display:inline-flex;align-items:center;justify-content:center;'
                    f'font-weight:700;font-size:14px;">#{num_sel}</span>'
                    f'<span class="badge {badge_class}">{classificacao}</span>'
                    '<span class="badge" style="background:#eef2f7;color:#2C3E50;'
                    f'border:1px solid #d6dee8;">{fonte_det}</span>'
                    '</div>'
                    f'<div class="detalhe-titulo" style="margin-top:12px;">{mun}</div>'
                    f'<div style="margin:8px 0 14px 0;"><span class="detalhe-codigo">{cod}</span></div>'
                    + ''.join(linhas_html) +
                    '</div>'
                )
                st.markdown(card_html, unsafe_allow_html=True)

                st.write("")

                # Botão de download individual
                try:
                    kml_ind = gerar_kml_individual(row_sel, num_sel)
                    cod_clean = cod.replace('/', '_').replace(' ', '_')[:40]
                    st.download_button(
                        label=f"📥 Baixar KML deste registro (#{num_sel})",
                        data=kml_ind,
                        file_name=f"{num_sel:02d}_{cod_clean}.kml",
                        mime="application/vnd.google-earth.kml+xml",
                        use_container_width=True,
                        key=f"dl_kml_ind_{num_sel}"
                    )
                except Exception as e:
                    st.caption(f"Não foi possível gerar o KML: {e}")

                with st.expander("Ver todos os atributos disponíveis"):
                    attrs = {
                        k: v for k, v in row_sel.items()
                        if k not in ['geometry', '_classificacao', '_area_sobreposicao_ha',
                                     '_uf_fonte', '_ordem', '_num', '_fonte', '_tambem_em',
                                     '_area_declarada_ha', '_municipio_nome', '_iou']
                        and str(v).strip() not in ['nan', 'None', 'NaT', '']
                    }
                    if attrs:
                        df_attrs = pd.DataFrame(
                            [{"Atributo": k, "Valor": str(v)} for k, v in attrs.items()]
                        )
                        st.dataframe(df_attrs, use_container_width=True, hide_index=True)
                    else:
                        st.caption("Nenhum atributo adicional disponível.")

            with col_det_mapa:
                try:
                    centro_sel = [row_sel.geometry.centroid.y, row_sel.geometry.centroid.x]
                    m2 = folium.Map(location=centro_sel, zoom_start=15, tiles="Esri World Imagery")

                    cor_sel = COR_FONTE.get(fonte_det, "#666666")
                    fo_sel = 0.45 if classificacao == "Sobreposição" else 0.15

                    # Vizinho selecionado (cor da fonte)
                    folium.GeoJson(
                        row_sel.geometry.__geo_interface__,
                        style_function=lambda x, c=cor_sel, fo=fo_sel: {
                            'color': c, 'weight': 3,
                            'fillColor': c, 'fillOpacity': fo
                        },
                        tooltip=folium.Tooltip(f"<b>#{num_sel} · {fonte_det} · {cod}</b>")
                    ).add_to(m2)

                    # Imóvel por cima (halo + ciano, sempre em destaque)
                    folium.GeoJson(
                        gdf_wgs,
                        style_function=lambda x: {
                            'color': '#003844', 'weight': 7, 'fill': False, 'opacity': 0.5
                        }
                    ).add_to(m2)
                    folium.GeoJson(
                        gdf_wgs,
                        style_function=lambda x: {
                            'color': COR_IMOVEL, 'weight': 4,
                            'fillColor': COR_IMOVEL, 'fillOpacity': 0.06,
                            'dashArray': '10, 6'
                        }
                    ).add_to(m2)

                    st_folium(m2, height=380, use_container_width=True,
                              key=f"mapa_detalhe_{num_sel}")
                except Exception:
                    st.caption("Não foi possível renderizar o mapa.")

    # ==========================================
    # 6. REGISTROS DO PRÓPRIO IMÓVEL (visualização)
    # ==========================================
    gdf_proprio = st.session_state.get('confrontantes_proprio')
    if gdf_proprio is not None and not gdf_proprio.empty:
        st.markdown("---")
        ui.secao("📌 Registros do Próprio Imóvel")
        st.caption(
            "Imóveis das bases que coincidem com o perímetro analisado "
            "(o próprio imóvel cadastrado). Útil para confirmar/validar o registro."
        )

        # 1 registro por fonte (o de maior compatibilidade, se houver repetição)
        gp = gdf_proprio.copy()
        if "_iou" not in gp.columns:
            gp["_iou"] = 0.0
        gp = (gp.sort_values("_iou", ascending=False)
                .drop_duplicates(subset=["_fonte"], keep="first")
                .reset_index(drop=True))

        opcoes = list(gp["_fonte"])
        if len(opcoes) > 1:
            escolha_p = st.radio("Base:", opcoes, horizontal=True, key="proprio_base_sel")
        else:
            escolha_p = opcoes[0]
        row_p = gp[gp["_fonte"] == escolha_p].iloc[0]

        cod_p = extrair_codigo_car(row_p)
        mun_p = extrair_municipio(row_p)
        area_p = extrair_area(row_p)
        status_p = extrair_status(row_p)
        iou_p = float(row_p.get("_iou", 0.0)) * 100
        cor_p = COR_FONTE.get(escolha_p, "#666666")

        col_pi, col_pm = st.columns([1, 1.3], gap="medium")

        with col_pi:
            linhas_p = [
                _det_linha("Fonte", escolha_p),
                _det_linha("Código", cod_p),
                _det_linha("Município", mun_p),
                _det_linha("Área", f"{area_p:.2f} ha" if area_p else "—"),
                _det_linha("Situação", status_p),
                _det_linha("Compatibilidade", f"{iou_p:.1f}% com o perímetro", cor_p),
            ]
            st.markdown(
                '<div class="detalhe-card">' + ''.join(linhas_p) + '</div>',
                unsafe_allow_html=True
            )
            st.write("")
            try:
                kml_p = gerar_kml_individual(row_p, 1)
                st.download_button(
                    f"📥 Baixar KML ({escolha_p})",
                    data=kml_p,
                    file_name=f"proprio_{escolha_p}_{str(cod_p).replace('/', '_')[:30]}.kml",
                    mime="application/vnd.google-earth.kml+xml",
                    use_container_width=True,
                    key=f"dl_proprio_{escolha_p}"
                )
            except Exception:
                pass

        with col_pm:
            try:
                cen_p = [row_p.geometry.centroid.y, row_p.geometry.centroid.x]
                mp = folium.Map(location=cen_p, zoom_start=15, tiles="Esri World Imagery")
                # registro da base (contorno na cor da fonte)
                folium.GeoJson(
                    row_p.geometry.__geo_interface__,
                    style_function=lambda x, c=cor_p: {
                        'color': c, 'weight': 3,
                        'fillColor': c, 'fillOpacity': 0.18
                    },
                    tooltip=folium.Tooltip(f"<b>{escolha_p}:</b> {cod_p}")
                ).add_to(mp)
                # perímetro analisado por cima (halo + ciano)
                folium.GeoJson(
                    gdf_wgs,
                    style_function=lambda x: {
                        'color': '#003844', 'weight': 7, 'fill': False, 'opacity': 0.5
                    }
                ).add_to(mp)
                folium.GeoJson(
                    gdf_wgs,
                    style_function=lambda x: {
                        'color': COR_IMOVEL, 'weight': 4,
                        'fillColor': COR_IMOVEL, 'fillOpacity': 0.04,
                        'dashArray': '10, 6'
                    },
                    tooltip=folium.Tooltip("<b>Perímetro analisado</b>")
                ).add_to(mp)
                st_folium(mp, height=360, use_container_width=True,
                          key=f"mapa_proprio_{escolha_p}")
            except Exception:
                st.caption("Não foi possível renderizar o mapa de comparação.")
