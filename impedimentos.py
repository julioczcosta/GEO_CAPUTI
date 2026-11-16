import streamlit as st
import geopandas as gpd
import pandas as pd
import requests
import folium
import ui
from streamlit_folium import st_folium
from io import BytesIO
import math
from shapely import wkb

# --- 1. CONFIGURAÇÕES DOS SERVIÇOS WFS ---
WFS_CRS = "EPSG:4674"  # SIRGAS 2000

# Dicionário para renomear colunas (Aliasing)
COLUMN_ALIASES = {
    'area_ha_sobreposta': 'Área Sobreposta (ha)',
    'geometry': 'Geometria',
    'nom_pessoa': 'Infrator',
    'nome_embargado': 'Embargado',
    'cpf_cnpj_embargado': 'CPF/CNPJ',
    'num_tad': 'Termo de Embargo',
    'dat_embargo': 'Data do Embargo',
    'qtd_area_embargada': 'Área Embargada (ha)',
    'cpf_cnpj_infrator': 'CPF/CNPJ',
    'num_auto_infracao': 'Auto de Infração',
    'qtd_area_desmatada': 'Área Desmatada',
    'data_cadastro_tad': 'Data Cadastro',
    'des_infracao': 'Descrição',
    'respeita_embargo': 'Respeita Embargo',
    'cpf_cnpj': 'CPF/CNPJ',
    'autuado': 'Autuado',
    'desc_infra': 'Infração',
    'tipo_infra': 'Tipo Infração',
    'processo': 'Processo',
    'desc_ai': 'Descrição AI',
    'valor_mult': 'Valor Multa',
    'nome_uc': 'Nome da UC',
    'nomeuc': 'Nome da UC',
    'ano': 'Ano Criação',
    'cria_ano': 'Ano Criação',
    'criacaoano': 'Ano Criação',
    'area': 'Área Total',
    'area_ha': 'Área Total (ha)',
    'grupo': 'Grupo',
    'grupouc': 'Grupo',
    'categoria': 'Categoria',
    'esfera': 'Esfera',
    'municipio': 'Município',
    'identificacao_bem': 'Identificação',
    'ds_natureza': 'Natureza',
    'ds_classificacao': 'Classificação',
    'sintese_bem': 'Síntese',
    'dt_cadastro': 'Data Cadastro',
    'terrai_nome': 'Terra Indígena',
    'etnia_nome': 'Etnia',
    'fase_ti': 'Fase',
    'modalidade_ti': 'Modalidade',
    'municipio_nome': 'Município',
    'nome_aldeia': 'Aldeia',
    'cod_aldeia': 'Cód. Aldeia',
    'nome_cr': 'Coord. Regional',
    # Assentamentos e quilombolas (INCRA, via geoserver CMR da FUNAI)
    'no_projeto': 'Projeto de Assentamento',
    'sg_modalidade': 'Modalidade',
    'cd_sipra': 'Código SIPRA',
    'no_municipio': 'Município',
    'nu_ano_criacao': 'Ano Criação',
    'nu_area_ha': 'Área Total (ha)',
    'ds_forma_obtencao': 'Forma de Obtenção',
    'no_comunidade': 'Comunidade Quilombola',
    'nu_processo': 'Processo INCRA',
    'ds_fase': 'Fase',
    'sg_uf': 'UF',
    'no_esfera': 'Esfera',
    'dt_titulo': 'Data do Título',
    # Desmatamento (INPE)
    'rotulo_desmat': 'Desmatamento',
    'ano_desmat': 'Ano PRODES',
    'apos_2020': 'Após 31/12/2020',
    'bioma_prodes': 'Bioma',
    'satellite': 'Satélite',
    'rotulo_alerta': 'Alerta DETER',
    'classname': 'Classe',
    'data_alerta': 'Data da Imagem',
    'areatotalkm': 'Área do Alerta (km²)',
    # Florestas públicas (SFB)
    'rotulo_cnfp': 'Floresta Pública',
    'nome': 'Nome',
    'tipo': 'Tipo',
    'classe': 'Classe',
    'governo': 'Esfera',
    'orgao': 'Órgão',
    'codigo': 'Código CNFP',
}

WFS_COLUNAS = {
    "publica:vw_brasil_adm_embargo_a": ['nom_pessoa', 'cpf_cnpj_infrator', 'num_auto_infracao', 'qtd_area_desmatada', 'data_cadastro_tad', 'des_infracao', 'respeita_embargo'],
    "embargo_ibama": ['nome_embargado', 'cpf_cnpj_embargado', 'num_auto_infracao', 'num_tad', 'dat_embargo',
                      'qtd_area_embargada', 'des_infracao', 'municipio'],
    "ICMBio:embargos_icmbio": ['cpf_cnpj', 'autuado', 'desc_infra', 'tipo_infra', 'nome_uc', 'ano', 'area', 'processo'],
    "ICMBio:autos_infracao_icmbio": ['embargo', 'autuado', 'cpf_cnpj', 'desc_ai', 'valor_mult', 'tipo_infra', 'nome_uc', 'processo'],
    "ICMBio:limiteucsfederais_a": ['nomeuc', 'criacaoano', 'grupouc', 'area_ha'], 
    "MMA:cnuc_2025_08": ['nome_uc', 'cria_ano', 'grupo', 'categoria', 'esfera', 'municipio'],
    "SICG:sitios": ['identificacao_bem', 'ds_natureza', 'ds_classificacao', 'sintese_bem', 'dt_cadastro'],
    "SICG:sitios_pol": ['identificacao_bem', 'id_natureza', 'ds_natureza', 'ds_classificacao', 'sintese_bem', 'dt_cadastro'],
    "Funai:tis_poligonais_portarias": ['terrai_nome', 'etnia_nome', 'fase_ti', 'modalidade_ti', 'municipio_nome'],
    "Funai:tis_pontos_portarias": ['terrai_nome', 'etnia_nome', 'fase_ti', 'modalidade_ti'],
    "Funai:aldeias_pontos": ['nome_aldeia', 'cod_aldeia', 'nome_cr'],
    "CMR-PUBLICO:lim_assentamento_rural_a": ['no_projeto', 'sg_modalidade', 'cd_sipra', 'no_municipio',
                                             'nu_ano_criacao', 'nu_area_ha', 'ds_forma_obtencao'],
    "CMR-PUBLICO:lim_quilombolas_a": ['no_comunidade', 'nu_processo', 'ds_fase', 'no_municipio', 'sg_uf',
                                      'no_esfera', 'dt_titulo'],
    "prodes": ['rotulo_desmat', 'ano_desmat', 'apos_2020', 'bioma_prodes', 'satellite'],
    "deter": ['rotulo_alerta', 'classname', 'data_alerta', 'areatotalkm', 'satellite'],
    "cnfp": ['rotulo_cnfp', 'tipo', 'classe', 'governo', 'orgao', 'categoria', 'nome'],
}

# INPE/TerraBrasilis: um geoserver por bioma. O imóvel só cruza o(s) do seu bioma;
# os demais respondem vazio em ~0,3 s.
_TB = "https://terrabrasilis.dpi.inpe.br/geoserver"
CAMADAS_PRODES = [
    ("Amazônia", f"{_TB}/prodes-amazon-nb/ows", "prodes-amazon-nb:yearly_deforestation_biome"),
    ("Cerrado", f"{_TB}/prodes-cerrado-nb/ows", "prodes-cerrado-nb:yearly_deforestation"),
    ("Caatinga", f"{_TB}/prodes-caatinga-nb/ows", "prodes-caatinga-nb:yearly_deforestation"),
    ("Mata Atlântica", f"{_TB}/prodes-mata-atlantica-nb/ows", "prodes-mata-atlantica-nb:yearly_deforestation"),
    ("Pantanal", f"{_TB}/prodes-pantanal-nb/ows", "prodes-pantanal-nb:yearly_deforestation"),
    ("Pampa", f"{_TB}/prodes-pampa-nb/ows", "prodes-pampa-nb:yearly_deforestation"),
]
CAMADAS_DETER = [
    ("Amazônia", f"{_TB}/deter-amz/ows", "deter-amz:deter_amz"),
    ("Cerrado", f"{_TB}/deter-cerrado-nb/ows", "deter-cerrado-nb:deter_cerrado"),
]
# Ano PRODES t = agosto de t-1 a julho de t. O marco do Código Florestal é
# 22/07/2008, então desmatamento "após o marco" = ano PRODES >= 2009.
ANO_MARCO_PRODES = 2009
CLASSES_DETER = {
    "DESMATAMENTO_CR": "Desmatamento (corte raso)",
    "DESMATAMENTO_VEG": "Desmatamento com vegetação",
    "MINERACAO": "Mineração",
    "DEGRADACAO": "Degradação",
    "CICATRIZ_DE_QUEIMADA": "Cicatriz de queimada",
    "CS_DESORDENADO": "Corte seletivo (desordenado)",
    "CS_GEOMETRICO": "Corte seletivo (geométrico)",
}
SFB_CNFP = ("https://mapas.florestal.gov.br/server/rest/services/Hosted/"
            "CNFP_v19_03_retificado_17072025/FeatureServer/9/query")
_CMR = "https://cmr.funai.gov.br/geoserver/ows"

SERVICES_TO_CHECK = [
    # O geoserver antigo (siscom.ibama.gov.br/geoserver) saiu do ar em 2026; os embargos
    # agora estão no ArcGIS do IBAMA (PAMGIA).
    { "name": "Embargo IBAMA", "tipo": "arcgis", "typename": "embargo_ibama", "color": "#FF0000", "timeout": 30,
      "base_url": "https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/adm_embargos_ibama_a/FeatureServer/0/query" },
    { "name": "Embargo ICMBio", "base_url": "https://geoservicos.inde.gov.br/geoserver/ICMBio/ows", "typename": "ICMBio:embargos_icmbio", "color": "#8B0000" },
    { "name": "Autos Infração ICMBio", "base_url": "https://geoservicos.inde.gov.br/geoserver/ICMBio/ows", "typename": "ICMBio:autos_infracao_icmbio", "color": "#FF4500" },
    { "name": "UCs ICMBio", "base_url": "https://geoservicos.inde.gov.br/geoserver/ICMBio/ows", "typename": "ICMBio:limiteucsfederais_a", "color": "#006400" },
    { "name": "UCs MMA", "base_url": "https://geoservicos.inde.gov.br/geoserver/MMA/ows", "typename": "MMA:cnuc_2025_08", "color": "#228B22" },
    { "name": "Sítios Arq. (Pontos) IPHAN", "base_url": "http://portal.iphan.gov.br/geoserver/ows", "typename": "SICG:sitios", "color": "#DAA520" },
    { "name": "Sítios Arq. (Polígonos) IPHAN", "base_url": "http://portal.iphan.gov.br/geoserver/ows", "typename": "SICG:sitios_pol", "special_params": {"outputFormat": "application/json"}, "color": "#B8860B" },
    { "name": "Terras Indígenas (Polígonos)", "base_url": "https://geoserver.funai.gov.br/geoserver/ows", "typename": "Funai:tis_poligonais_portarias", "version": "1.0.0", "color": "#8B4513" },
    { "name": "Terras Indígenas (Pontos)", "base_url": "https://geoserver.funai.gov.br/geoserver/ows", "typename": "Funai:tis_pontos_portarias", "version": "1.0.0", "color": "#8B4513" },
    { "name": "Aldeias Indígenas", "base_url": "https://geoserver.funai.gov.br/geoserver/ows", "typename": "Funai:aldeias_pontos", "version": "1.0.0", "color": "#A0522D" },
    # INCRA (o acervo fundiário do INCRA cai com frequência; o CMR da FUNAI publica as mesmas camadas)
    { "name": "Territórios Quilombolas (INCRA)", "base_url": _CMR, "typename": "CMR-PUBLICO:lim_quilombolas_a", "color": "#6A1B9A" },
    { "name": "Assentamentos (INCRA)", "base_url": _CMR, "typename": "CMR-PUBLICO:lim_assentamento_rural_a", "color": "#E91E63" },
    # INPE
    { "name": "Desmatamento após 2008 (PRODES/INPE)", "tipo": "prodes", "typename": "prodes", "color": "#FF8C00", "timeout": 40 },
    { "name": "Alertas de Desmatamento (DETER/INPE)", "tipo": "deter", "typename": "deter", "color": "#FFD000", "timeout": 40 },
    # Serviço Florestal Brasileiro: só Tipo B (glebas públicas ainda não destinadas);
    # o Tipo A repete TI, UC e assentamentos, que já são verificados acima.
    { "name": "Florestas Públicas Não Destinadas (SFB)", "tipo": "arcgis", "typename": "cnfp", "base_url": SFB_CNFP,
      "where": "tipo = 'TIPO B'", "color": "#2E7D32", "timeout": 40 },
]

# --- 2. FUNÇÕES AUXILIARES ---

def corrigir_geometrias(gdf):
    if gdf is None or gdf.empty:
        return gdf
    try:
        # Força 2D
        gdf.geometry = gdf.geometry.apply(
            lambda geom: wkb.loads(wkb.dumps(geom, output_dimension=2)) if geom.has_z else geom
        )
        gdf = gdf.explode(index_parts=False).reset_index(drop=True)
        gdf.geometry = gdf.geometry.buffer(0)
        gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
        return gdf
    except Exception as e:
        return gdf

def calcular_epsg_utm(geometria_centroide):
    try:
        lon, lat = geometria_centroide.x, geometria_centroide.y
        zone = math.floor((lon + 180) / 6) + 1
        return f"EPSG:{31950 + zone}" if lat >= 0 else f"EPSG:{31960 + zone}"
    except Exception:
        return "EPSG:31983"

class FonteIndisponivel(Exception):
    """O serviço não respondeu (ou respondeu erro): não dá para dizer "nada consta"."""


_VAZIO = ('numberMatched="0"', 'numberReturned="0"', 'numberOfFeatures="0"',
          '"features":[]', '"features": []', '"totalFeatures":0')


@st.cache_data(ttl=3600, show_spinner=False)
def baixar_wfs(url, params, timeout=15):
    """GeoDataFrame da consulta. Resposta válida sem feições -> vazio.
    Falha de rede/serviço -> FonteIndisponivel (não fica no cache)."""
    headers = {"User-Agent": "Mozilla/5.0"}
    ultimo = ""
    for tentativa in range(2):
        try:
            try:
                r = requests.get(url, params=params, headers=headers, timeout=timeout)
            except requests.exceptions.SSLError:
                r = requests.get(url, params=params, headers=headers, verify=False, timeout=timeout)
            r.raise_for_status()
        except Exception as e:
            ultimo = str(e)[:200]
            continue
        try:
            return gpd.read_file(BytesIO(r.content))
        except Exception as e:
            cab = r.content[:3000].decode("utf-8", "ignore").replace(" ", "")
            if any(v.replace(" ", "") in cab for v in _VAZIO):
                return gpd.GeoDataFrame()
            ultimo = f"resposta ilegível: {str(e)[:120]}"
    raise FonteIndisponivel(ultimo or "sem resposta")


def _params_wfs(typename, bbox, version="2.0.0", extra=None):
    p = {'service': 'WFS', 'version': version, 'request': 'GetFeature', 'typename': typename,
         'srsName': WFS_CRS, 'BBOX': f"{bbox},{WFS_CRS}"}
    if version in ("1.0.0", "1.1.0"):
        p['BBOX'] = bbox
    if extra:
        p.update(extra)
    return p


def _num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _baixar_prodes(bbox, timeout):
    """Desmatamento anual PRODES de todos os biomas no bbox, só após o marco de 2008."""
    partes = []
    for bioma, url, typ in CAMADAS_PRODES:
        g = baixar_wfs(url, _params_wfs(typ, bbox, extra={"outputFormat": "application/json"}), timeout)
        if g.empty:
            continue
        g = g.copy()
        g["bioma_prodes"] = bioma
        partes.append(g)
    if not partes:
        return gpd.GeoDataFrame()
    g = gpd.GeoDataFrame(pd.concat(partes, ignore_index=True), crs=partes[0].crs)
    g["ano_desmat"] = g["year"].map(_num) if "year" in g else None
    if "main_class" in g:
        g = g[g["main_class"].astype(str).str.upper().str.contains("DESMATAMENTO")]
    g = g[g["ano_desmat"].notna() & (g["ano_desmat"] >= ANO_MARCO_PRODES)].copy()
    g["rotulo_desmat"] = "Desmatamento " + g["ano_desmat"].astype(int).astype(str)
    g["apos_2020"] = g["ano_desmat"].map(lambda a: "Sim" if a and a >= 2021 else "Não")
    return g


def _baixar_deter(bbox, timeout):
    """Alertas DETER (Amazônia e Cerrado) no bbox."""
    partes = []
    for bioma, url, typ in CAMADAS_DETER:
        g = baixar_wfs(url, _params_wfs(typ, bbox, extra={"outputFormat": "application/json"}), timeout)
        if not g.empty:
            partes.append(g)
    if not partes:
        return gpd.GeoDataFrame()
    g = gpd.GeoDataFrame(pd.concat(partes, ignore_index=True), crs=partes[0].crs)
    g["data_alerta"] = pd.to_datetime(g.get("view_date"), errors="coerce").dt.strftime("%d/%m/%Y")
    classe = g["classname"].astype(str).str.upper()
    g["classname"] = classe.map(CLASSES_DETER).fillna(classe.str.replace("_", " ").str.capitalize())
    g["rotulo_alerta"] = g["classname"] + " · " + g["data_alerta"].fillna("s/ data")
    return g


@st.cache_data(ttl=3600, show_spinner=False)
def _baixar_arcgis(url, bbox, timeout, where="1=1"):
    """Consulta espacial num FeatureServer ArcGIS REST (IBAMA, SFB)."""
    params = {"geometry": bbox, "geometryType": "esriGeometryEnvelope", "inSR": "4674",
              "spatialRel": "esriSpatialRelIntersects", "where": where,
              "outFields": "*", "outSR": "4674", "f": "geojson"}
    try:
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        r.raise_for_status()
    except Exception as e:
        raise FonteIndisponivel(str(e)[:200])
    txt = r.text
    if txt.lstrip().startswith('{"error"'):
        raise FonteIndisponivel(txt[:200])
    try:
        return gpd.read_file(BytesIO(r.content))
    except Exception as e:
        if '"features":[]' in txt[:3000].replace(" ", ""):
            return gpd.GeoDataFrame()
        raise FonteIndisponivel(f"resposta ilegível: {str(e)[:120]}")


def consultar_servico(srv, bbox):
    """GeoDataFrame bruto de um serviço (FonteIndisponivel se não responder)."""
    timeout = srv.get("timeout", 15)
    tipo = srv.get("tipo", "wfs")
    if tipo == "prodes":
        return _baixar_prodes(bbox, timeout)
    if tipo == "deter":
        return _baixar_deter(bbox, timeout)
    if tipo == "arcgis":
        g = _baixar_arcgis(srv["base_url"], bbox, timeout, srv.get("where", "1=1"))
        if g.empty:
            return g
        g = g.copy()
        if srv["typename"] == "cnfp":   # o campo "nome" costuma ser só um número ("01")
            g["rotulo_cnfp"] = (g["categoria"].astype(str).str.strip().str.capitalize()
                                + " · " + g["codigo"].astype(str).str.strip())
        if srv["typename"] == "embargo_ibama":
            for c in ("dat_embargo",):
                if c in g:
                    g[c] = pd.to_datetime(pd.to_numeric(g[c], errors="coerce"), unit="ms",
                                          errors="coerce").dt.strftime("%d/%m/%Y")
        return g
    params = _params_wfs(srv["typename"], bbox, srv.get("version", "2.0.0"), srv.get("special_params"))
    return baixar_wfs(srv["base_url"], params, timeout)

def processar_camada(gdf_fonte, aoi_geom, aoi_crs_proj, colunas):
    if gdf_fonte.empty: return False, None, "Sem dados na fonte"

    if gdf_fonte.crs is None: gdf_fonte.set_crs(WFS_CRS, allow_override=True, inplace=True)
    if gdf_fonte.crs != WFS_CRS: gdf_fonte = gdf_fonte.to_crs(WFS_CRS)

    try:
        gdf_inter = gdf_fonte[gdf_fonte.intersects(aoi_geom)].copy()
    except Exception as e: return False, None, str(e)

    if gdf_inter.empty: return False, None, "Sem interseção"

    try:
        gdf_inter['geom_original'] = gdf_inter.geometry
        gdf_inter['geometry'] = gdf_inter.geometry.intersection(aoi_geom)
        gdf_proj = gdf_inter.to_crs(aoi_crs_proj)
        gdf_inter['area_ha_sobreposta'] = gdf_proj.geometry.area / 10000
    except Exception:
        gdf_inter['area_ha_sobreposta'] = 0

    cols_existentes = [c for c in colunas if c in gdf_inter.columns]
    cols_final = ['area_ha_sobreposta', 'geometry'] + cols_existentes

    return True, gdf_inter[cols_final], None


# --- 2b. IMPEDIMENTOS POR MATRÍCULA ---

# Colunas (por fonte) que identificam o impedimento, em ordem de preferência.
_COLS_IDENT = ['nom_pessoa', 'nome_embargado', 'autuado', 'nome_uc', 'nomeuc', 'terrai_nome',
               'nome_aldeia', 'identificacao_bem', 'no_projeto', 'no_comunidade',
               'rotulo_desmat', 'rotulo_alerta', 'rotulo_cnfp', 'num_auto_infracao', 'processo']


def _identificar(row):
    """Rótulo curto do impedimento, escolhido conforme a fonte."""
    for c in _COLS_IDENT:
        v = row.get(c)
        if v is not None and str(v).strip() not in ('', 'nan', 'None', 'NaT'):
            return str(v).strip()
    return "—"


def _impedimentos_por_matricula(resultados, feats):
    """Distribui os impedimentos encontrados pelas matrículas do imóvel.

    Para cada impedimento (feição já recortada ao perímetro), descobre em
    qual(is) matrícula(s) ele incide e a área sobreposta em cada uma (ha e % da
    matrícula). Um embargo/UC/TI que atravessa duas matrículas aparece uma vez
    por matrícula, com a área correspondente a cada uma. Só se aplica quando há
    mais de uma matrícula. Pontos (sem área) entram com 0."""
    if feats is None or len(feats) < 2:
        return pd.DataFrame()

    feats_4674 = feats.to_crs(WFS_CRS).reset_index(drop=True)
    # Área (ha) e UTM de cada matrícula, calculados uma única vez.
    info_mat = []
    for i in range(len(feats_4674)):
        g = feats_4674.geometry.iloc[i]
        utm = calcular_epsg_utm(g.centroid)
        area_ha = gpd.GeoSeries([g], crs=WFS_CRS).to_crs(utm).area.iloc[0] / 1e4
        info_mat.append((utm, area_ha))

    linhas = []
    for item in resultados:
        if not item.get("status") or item.get("dados") is None or item["dados"].empty:
            continue
        fonte = item["nome"]
        for _, r in item["dados"].iterrows():
            geom = r.geometry
            if geom is None or geom.is_empty:
                continue
            ident = _identificar(r)
            eh_ponto = geom.geom_type in ("Point", "MultiPoint")
            for i in range(len(feats_4674)):
                mat_geom = feats_4674.geometry.iloc[i]
                if not geom.intersects(mat_geom):
                    continue
                rotulo = str(feats.iloc[i]["_rotulo"])
                if eh_ponto:
                    linhas.append({
                        "Matrícula": rotulo, "Fonte": fonte, "Identificação": ident,
                        "Área sobreposta (ha)": 0.0, "% da matrícula": 0.0})
                    continue
                inter = geom.intersection(mat_geom)
                if inter.is_empty:
                    continue
                utm, area_m_ha = info_mat[i]
                a = gpd.GeoSeries([inter], crs=WFS_CRS).to_crs(utm).area.iloc[0] / 1e4
                if a <= 0:
                    continue
                pct = (a / area_m_ha * 100) if area_m_ha > 0 else 0.0
                linhas.append({
                    "Matrícula": rotulo, "Fonte": fonte, "Identificação": ident,
                    "Área sobreposta (ha)": round(a, 4), "% da matrícula": round(pct, 2)})

    if not linhas:
        return pd.DataFrame()
    df = pd.DataFrame(linhas)
    return df.sort_values(
        ["Matrícula", "Área sobreposta (ha)"], ascending=[True, False]
    ).reset_index(drop=True)


# --- 3. RENDERIZAÇÃO ---

def render_tab():
    st.markdown("### Análise de Impedimentos Socioambientais")
    
    gdf_alvo = None
    
    possible_keys = ['gdf_imovel', 'imovel_upload', 'gdf_perimetro', 'kml_data', 'gdf_data']
    for key in possible_keys:
        if key in st.session_state and isinstance(st.session_state[key], gpd.GeoDataFrame):
            # Usamos o .copy() para não sujar a variável global acidentalmente
            gdf_alvo = st.session_state[key].copy()
            break

    if gdf_alvo is None:
        ui.vazio()
        return
        
    # =======================================================
    # APLICA A FAXINA AQUI NO TOPO! 
    # Assim o KML fica limpo para o Botão E para o Mapa.
    # =======================================================
    gdf_alvo = corrigir_geometrias(gdf_alvo)

    codigo_display = st.session_state.get('last_code', 'Imóvel Carregado')
    ui.barra_imovel(nome=codigo_display)

    if 'impedimentos_done' not in st.session_state: st.session_state['impedimentos_done'] = False
    if 'impedimentos_results' not in st.session_state: st.session_state['impedimentos_results'] = []
    
    if st.button("🚫 Verificar Sobreposições", type="primary", use_container_width=True):
        
        if gdf_alvo.crs != WFS_CRS: gdf_alvo = gdf_alvo.to_crs(WFS_CRS)
        
        geom_uniao = gdf_alvo.unary_union
        crs_proj = calcular_epsg_utm(geom_uniao.centroid)
        bounds = gdf_alvo.total_bounds
        bbox = f"{bounds[0]},{bounds[1]},{bounds[2]},{bounds[3]}"
        
        resultados = []
        bar = st.progress(0, text="Conectando aos serviços...")
        total = len(SERVICES_TO_CHECK)
        
        for i, srv in enumerate(SERVICES_TO_CHECK):
            bar.progress(int(((i+1)/total)*100), text=f"Verificando: {srv['name']}...")
            
            try:
                gdf_wfs = consultar_servico(srv, bbox)
                cols = WFS_COLUNAS.get(srv["typename"], [])
                achou, gdf_res, msg = processar_camada(gdf_wfs, geom_uniao, crs_proj, cols)

                resultados.append({
                    "nome": srv["name"], "status": achou, "dados": gdf_res, "cor": srv["color"], "erro": msg
                })
            except FonteIndisponivel as e:
                resultados.append({"nome": srv["name"], "status": False, "dados": None, "cor": "#ccc",
                                   "erro": str(e), "indisponivel": True})
            except Exception as e:
                resultados.append({"nome": srv["name"], "status": False, "dados": None, "cor": "#ccc",
                                   "erro": str(e)[:200], "indisponivel": True})

        bar.empty()
        st.session_state['impedimentos_results'] = resultados
        st.session_state['impedimentos_done'] = True
        st.rerun()

    # --- EXIBIÇÃO ---
    if st.session_state['impedimentos_done']:
        resultados = st.session_state['impedimentos_results']
        hits = [r for r in resultados if r["status"]]
        n_hits = len(hits)
        falhas = [r for r in resultados if r.get("indisponivel")]

        st.divider()

        # Resumo com severidade no topo (a resposta principal aparece primeiro).
        if n_hits == 0 and not falhas:
            st.success("✅ **Nada consta** — nenhuma sobreposição nas bases consultadas.")
        elif n_hits == 0:
            st.warning(f"⚠️ **Nada consta nas bases que responderam**, mas {len(falhas)} fonte(s) não "
                       "responderam agora e **não foram verificadas**: "
                       + ", ".join(f["nome"] for f in falhas) + ". Clique em verificar de novo em instantes.")
        else:
            st.error(f"⚠️ **{n_hits} sobreposição(ões)** encontrada(s) — veja o detalhamento abaixo.")
            if falhas:
                st.warning(f"{len(falhas)} fonte(s) não responderam e não foram verificadas: "
                           + ", ".join(f["nome"] for f in falhas) + ".")

        def _badge(item):
            if item.get("indisponivel"):
                return ('<div style="background-color:#fff4e0;padding:8px 10px;border-radius:6px;'
                        'border-left:4px solid #e0a020;margin-bottom:8px;font-size:14px;color:#6e4300;">'
                        f'⚠️ <b>{item["nome"]}</b> · fonte não respondeu, não verificada</div>')
            if item["status"]:
                return ('<div style="background-color:#ffe6e6;padding:8px 10px;border-radius:6px;'
                        'border-left:4px solid #ff4b4b;margin-bottom:8px;font-size:14px;">'
                        f'❌ <b>{item["nome"]}</b></div>')
            return ('<div style="background-color:#e6ffec;padding:8px 10px;border-radius:6px;'
                    'border-left:4px solid #28a745;margin-bottom:8px;font-size:14px;color:#155724;">'
                    f'✅ <b>{item["nome"]}</b></div>')

        # Sem sobreposição: só o checklist (tudo verde), num painel rolável.
        if n_hits == 0:
            with st.container(border=True, height=430):
                ui.secao("Checklist de verificação")
                st.markdown("".join(_badge(r) for r in resultados), unsafe_allow_html=True)
            return

        # --- Impedimentos por matrícula (imóvel com várias matrículas) ---
        feats = st.session_state.get('gdf_features')
        df_mat = _impedimentos_por_matricula(resultados, feats)
        if not df_mat.empty:
            ui.secao("🗂️ Impedimentos por matrícula")
            n_mat = df_mat["Matrícula"].nunique()
            area_tot = float(df_mat["Área sobreposta (ha)"].sum())
            st.caption(
                f"Os impedimentos atingem **{n_mat}** matrícula(s) · área sobreposta "
                f"total **{f'{area_tot:.2f}'.replace('.', ',')} ha**. Cada linha é um "
                "impedimento sobre a matrícula indicada, com a área invadida (ha) e o "
                "quanto representa dela (%). Pontos (sem área) entram com 0."
            )
            roll = (df_mat.groupby("Matrícula")
                          .agg(Impedimentos=("Fonte", "count")).reset_index())
            _a = (df_mat.groupby("Matrícula")["Área sobreposta (ha)"]
                        .sum().round(4).reset_index())
            roll = roll.merge(_a, on="Matrícula")
            st.dataframe(roll, use_container_width=True, hide_index=True)
            with st.expander("Ver impedimentos detalhados (um por matrícula atingida)"):
                st.dataframe(df_mat, use_container_width=True, hide_index=True)
                _buf = BytesIO()
                with pd.ExcelWriter(_buf, engine="xlsxwriter") as _w:
                    df_mat.to_excel(_w, index=False, sheet_name="Impedimentos")
                    roll.to_excel(_w, index=False, sheet_name="Resumo por matrícula")
                st.download_button(
                    "📥 Baixar impedimentos por matrícula (Excel)",
                    data=_buf.getvalue(),
                    file_name="impedimentos_por_matricula.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True, key="dl_imp_matricula")
            st.divider()

        # Com sobreposição: checklist e mapa LADO A LADO (painéis de altura fixa),
        # e o detalhamento em ABAS (uma por fonte) em vez de uma pilha de tabelas.
        col_check, col_mapa = st.columns([0.42, 0.58], gap="medium")

        with col_check:
            with st.container(border=True, height=470):
                ui.secao("Checklist de verificação")
                st.markdown("".join(_badge(r) for r in resultados), unsafe_allow_html=True)

        with col_mapa:
            with st.container(border=True, height=470):
                ui.secao("Localização das sobreposições")

                if gdf_alvo.crs != WFS_CRS:
                    gdf_alvo = gdf_alvo.to_crs(WFS_CRS)
                centro = [gdf_alvo.unary_union.centroid.y, gdf_alvo.unary_union.centroid.x]

                m = folium.Map(location=centro, zoom_start=12, tiles="Esri World Imagery")
                folium.GeoJson(
                    gdf_alvo, name="Imóvel",
                    style_function=lambda x: {'color': '#00FFFF', 'fillColor': '#00FFFF', 'fillOpacity': 0.1, 'weight': 2}
                ).add_to(m)

                for item in hits:
                    gdf_draw = item["dados"].copy()
                    is_point = gdf_draw.geometry.iloc[0].geom_type in ['Point', 'MultiPoint']

                    for col in gdf_draw.columns:
                        if col == 'area_ha_sobreposta' and not is_point:
                            gdf_draw[col] = gdf_draw[col].round(4)
                        elif pd.api.types.is_datetime64_any_dtype(gdf_draw[col]) or gdf_draw[col].dtype == 'object':
                            gdf_draw[col] = gdf_draw[col].astype(str)

                    aliases_map = {k: v for k, v in COLUMN_ALIASES.items() if k in gdf_draw.columns}
                    cols_tooltip = [c for c in gdf_draw.columns if c not in ['geometry', 'geom_original']]
                    if is_point and 'area_ha_sobreposta' in cols_tooltip:
                        cols_tooltip.remove('area_ha_sobreposta')
                    tooltips_aliased = [aliases_map.get(c, c) for c in cols_tooltip]

                    folium.GeoJson(
                        gdf_draw, name=item["nome"],
                        style_function=lambda x, c=item["cor"]: {'color': c, 'fillColor': c, 'fillOpacity': 0.5, 'weight': 1},
                        tooltip=folium.GeoJsonTooltip(
                            fields=cols_tooltip[:5], aliases=tooltips_aliased[:5], sticky=True
                        ) if cols_tooltip else None
                    ).add_to(m)

                folium.LayerControl().add_to(m)
                st_folium(m, height=400, use_container_width=True, key="imp_map")

        ui.secao("Detalhamento técnico")
        abas = st.tabs([f"🔴 {h['nome']}" for h in hits])
        for aba, item in zip(abas, hits):
            with aba:
                df_show = pd.DataFrame(item["dados"].drop(columns=['geometry'], errors='ignore'))
                is_point_table = item["dados"].geometry.iloc[0].geom_type in ['Point', 'MultiPoint']
                if is_point_table and 'area_ha_sobreposta' in df_show.columns:
                    df_show = df_show.drop(columns=['area_ha_sobreposta'])
                df_show = df_show.rename(columns=COLUMN_ALIASES)
                st.dataframe(df_show, use_container_width=True, hide_index=True)