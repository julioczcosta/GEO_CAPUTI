import streamlit as st
import geopandas as gpd
import pandas as pd
import requests
import folium
import ui
from streamlit_folium import st_folium
from io import BytesIO
import math
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from shapely import wkb
import shapely

# A verificação roda em threads (segundo plano); as funções com st.cache_data
# chamadas de lá avisam "missing ScriptRunContext", o que é inofensivo.
for _lg in ("streamlit.runtime.scriptrunner_utils.script_run_context",
            "streamlit.runtime.scriptrunner.script_run_context",
            "streamlit.runtime.caching.cache_data_api"):
    logging.getLogger(_lg).setLevel(logging.ERROR)

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
    'rotulo_anm': 'Processo Minerário',
    'FASE': 'Fase',
    'NOME': 'Titular',
    'SUBS': 'Substância',
    'USO': 'Uso',
    'AREA_HA': 'Área do Processo (ha)',
    'ULT_EVENTO': 'Último Evento',
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
    "anm_processos": ['rotulo_anm', 'FASE', 'NOME', 'SUBS', 'USO', 'AREA_HA', 'ULT_EVENTO'],
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
    # ANM/SIGMINE: o geoserver da INDE só serve imagem (WMS); o ArcGIS da ANM tem o vetor.
    { "name": "Processos Minerários (ANM)", "tipo": "arcgis", "typename": "anm_processos", "color": "#5D4037", "timeout": 30,
      "base_url": "https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/MapServer/0/query" },
]

# Agrupamento do checklist (ordem de exibição). Mineração fica numa seção à parte:
# processo minerário é direito de terceiro sobre o subsolo, não restrição ambiental.
GRUPO_MINERACAO = "Mineração"
GRUPOS = {
    "Embargos e autuações": ["Embargo IBAMA", "Embargo ICMBio", "Autos Infração ICMBio"],
    "Desmatamento": ["Desmatamento após 2008 (PRODES/INPE)", "Alertas de Desmatamento (DETER/INPE)"],
    "Áreas protegidas e florestas públicas": ["UCs ICMBio", "UCs MMA", "Florestas Públicas Não Destinadas (SFB)"],
    "Povos e comunidades tradicionais": ["Terras Indígenas (Polígonos)", "Terras Indígenas (Pontos)",
                                         "Aldeias Indígenas", "Territórios Quilombolas (INCRA)"],
    "Fundiário": ["Assentamentos (INCRA)"],
    "Patrimônio cultural": ["Sítios Arq. (Pontos) IPHAN", "Sítios Arq. (Polígonos) IPHAN"],
    GRUPO_MINERACAO: ["Processos Minerários (ANM)"],
}
_GRUPO_DE = {n: g for g, ns in GRUPOS.items() for n in ns}


def _eh_mineracao(item):
    return _GRUPO_DE.get(item["nome"]) == GRUPO_MINERACAO

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
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.exceptions.SSLError:
            # Vários servidores .gov.br (ex.: ANM) mandam a cadeia de certificados
            # incompleta; são dados públicos só de leitura, como nas demais fontes.
            r = requests.get(url, params=params, headers=headers, timeout=timeout, verify=False)
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
        if srv["typename"] == "anm_processos":
            g["rotulo_anm"] = ("Processo " + g["DSProcesso"].astype(str).str.strip()
                               + " · " + g["SUBS"].astype(str).str.strip().str.capitalize())
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
    # fontes como o PRODES trazem polígonos inválidos (auto-interseção)
    gdf_fonte = gdf_fonte.set_geometry(shapely.make_valid(gdf_fonte.geometry.values))

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
               'rotulo_desmat', 'rotulo_alerta', 'rotulo_cnfp', 'rotulo_anm', 'num_auto_infracao', 'processo']


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


# --- 3. VERIFICAÇÃO EM SEGUNDO PLANO ---
# Começa assim que o perímetro é confirmado (home.py) e segue enquanto o usuário
# navega pelas outras abas; um aviso abaixo do menu mostra o andamento e o resultado.

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="impedimentos")


def _preparar(gdf):
    g = corrigir_geometrias(gdf.copy())
    if g.crs is None:
        g = g.set_crs("EPSG:4326")
    if g.crs != WFS_CRS:
        g = g.to_crs(WFS_CRS)
    return g


def _assinatura(gdf_4674):
    return hashlib.md5(gdf_4674.unary_union.wkb).hexdigest()


def _verificar_um(srv, bbox, geom_uniao, crs_proj):
    try:
        gdf_wfs = consultar_servico(srv, bbox)
        cols = WFS_COLUNAS.get(srv["typename"], [])
        achou, gdf_res, msg = processar_camada(gdf_wfs, geom_uniao, crs_proj, cols)
        return {"nome": srv["name"], "status": achou, "dados": gdf_res, "cor": srv["color"], "erro": msg}
    except Exception as e:   # FonteIndisponivel ou erro inesperado: não verificada
        return {"nome": srv["name"], "status": False, "dados": None, "cor": "#ccc",
                "erro": str(e)[:200], "indisponivel": True}


def _verificar(gdf_4674, prog):
    """Consulta todas as fontes em paralelo. Roda fora do script: nada de st.* aqui."""
    geom_uniao = gdf_4674.unary_union
    crs_proj = calcular_epsg_utm(geom_uniao.centroid)
    b = gdf_4674.total_bounds
    bbox = f"{b[0]},{b[1]},{b[2]},{b[3]}"
    por_nome = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_verificar_um, s, bbox, geom_uniao, crs_proj): s for s in SERVICES_TO_CHECK}
        for f in as_completed(futs):
            por_nome[futs[f]["name"]] = f.result()
            prog["feitos"] += 1
    return [por_nome[s["name"]] for s in SERVICES_TO_CHECK]


def _alvo():
    for key in ('gdf_imovel', 'imovel_upload', 'gdf_perimetro', 'kml_data', 'gdf_data'):
        v = st.session_state.get(key)
        if isinstance(v, gpd.GeoDataFrame) and not v.empty:
            return v
    return None


def iniciar_verificacao(gdf=None, forcar=False):
    """Dispara a verificação do perímetro em segundo plano (uma vez por geometria)."""
    gdf = gdf if gdf is not None else _alvo()
    if gdf is None:
        return None
    g = _preparar(gdf)
    sig = _assinatura(g)
    job = st.session_state.get("imp_job")
    if job and job["sig"] == sig and not forcar:
        job["src_id"] = id(gdf)
        return job
    prog = {"feitos": 0}
    job = {"sig": sig, "src_id": id(gdf), "prog": prog, "total": len(SERVICES_TO_CHECK),
           "fut": _EXECUTOR.submit(_verificar, g, prog), "avisado": False}
    st.session_state["imp_job"] = job
    return job


def _job_atual():
    """Job do perímetro ativo (None se não há perímetro ou o job é de outro imóvel)."""
    gdf = _alvo()
    job = st.session_state.get("imp_job")
    if gdf is None or not job:
        return None
    if job.get("src_id") == id(gdf):
        return job
    if job["sig"] == _assinatura(_preparar(gdf)):
        job["src_id"] = id(gdf)
        return job
    return None


def _resultados(job):
    """Resultados prontos, ou None enquanto a verificação roda."""
    if job is None or not job["fut"].done():
        return None
    try:
        return job["fut"].result()
    except Exception as e:
        return [{"nome": s["name"], "status": False, "dados": None, "cor": "#ccc",
                 "erro": str(e)[:200], "indisponivel": True} for s in SERVICES_TO_CHECK]


def _separar(resultados):
    return ([r for r in resultados if not _eh_mineracao(r)],
            [r for r in resultados if _eh_mineracao(r)])


def _fmt_ha(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _area_uniao(itens, crs_proj):
    """Área (ha) da união das sobreposições: PRODES e DETER, por exemplo, se repetem."""
    geoms = [g for r in itens if r.get("dados") is not None for g in r["dados"].geometry
             if g is not None and not g.is_empty and g.geom_type not in ("Point", "MultiPoint")]
    if not geoms:
        return 0.0
    proj = gpd.GeoSeries(geoms, crs=WFS_CRS).to_crs(crs_proj).values
    # só a parte de área (recortes podem trazer linhas/pontos) e válida após reprojetar
    poli = shapely.make_valid(proj)
    for _ in range(3):   # desaninha GeometryCollection > MultiPolygon > Polygon
        poli = shapely.get_parts(poli)
    poli = poli[shapely.get_type_id(poli) == 3]   # Polygon
    if not len(poli):
        return 0.0
    return shapely.union_all(poli).area / 1e4


# --- 4. AVISO EM TODAS AS ABAS ---

_ESTILO_AVISO = {
    "rodando": "background:#eef4fb;border-left:4px solid #3b82c4;color:#1f4e79;",
    "alerta": "background:#ffe6e6;border-left:4px solid #ff4b4b;color:#7a1010;",
    "atencao": "background:#fff4e0;border-left:4px solid #e0a020;color:#6e4300;",
    "ok": "background:#e6ffec;border-left:4px solid #28a745;color:#155724;",
}


def _faixa(tipo, html):
    st.markdown(f'<div style="{_ESTILO_AVISO[tipo]}padding:9px 12px;border-radius:6px;'
                f'font-size:14px;margin:4px 0 10px 0;">{html}</div>', unsafe_allow_html=True)


def aviso_geral():
    """Faixa abaixo do menu: andamento da verificação e, ao terminar, o resultado."""
    job = _job_atual()
    if job is None:
        return
    if not job["fut"].done():
        _aviso_rodando()
        return
    res = _resultados(job)
    socio, mine = _separar(res)
    hits = [r for r in socio if r["status"]]
    falhas = [r for r in res if r.get("indisponivel")]
    n_min = sum(len(r["dados"]) for r in mine if r["status"])

    if hits:
        tipo = "alerta"
        txt = "⚠️ <b>Este imóvel possui impedimentos:</b> " + ", ".join(r["nome"] for r in hits) + "."
    elif falhas:
        tipo = "atencao"
        txt = (f"⚠️ Nenhum impedimento nas bases que responderam, mas {len(falhas)} fonte(s) "
               "não foram verificadas.")
    else:
        tipo = "ok"
        txt = "✅ Nenhum impedimento socioambiental encontrado."
    if n_min:
        txt += f" ⛏️ {n_min} processo(s) minerário(s) sobre a área."
    _faixa(tipo, txt + " Detalhes na aba <b>Impedimentos</b>.")

    if not job["avisado"]:
        job["avisado"] = True
        if hits:
            st.toast(f"Imóvel com {len(hits)} impedimento(s). Veja a aba Impedimentos.", icon="⚠️")
        elif n_min:
            st.toast("Há processo minerário sobre o imóvel. Veja a aba Impedimentos.", icon="⛏️")
        else:
            st.toast("Verificação de impedimentos concluída.", icon="✅")


@st.fragment(run_every=2)
def _aviso_rodando():
    job = st.session_state.get("imp_job")
    if job is None:
        return
    if job["fut"].done():
        st.rerun(scope="app")
    _faixa("rodando", f"🔎 Verificando impedimentos em segundo plano… "
                      f"{job['prog']['feitos']}/{job['total']} bases consultadas. "
                      "Pode seguir usando as outras abas.")


@st.fragment(run_every=1.5)
def _progresso_tab():
    job = st.session_state.get("imp_job")
    if job is None:
        return
    if job["fut"].done():
        st.rerun(scope="app")
    f, t = job["prog"]["feitos"], job["total"]
    st.progress(f / t, text=f"Consultando as bases oficiais… {f}/{t}")
    st.caption("A verificação começou ao confirmar o perímetro e continua mesmo se você trocar de aba.")


# --- 5. RENDERIZAÇÃO ---

def _badge(item):
    if item.get("indisponivel"):
        return ('<div style="background-color:#fff4e0;padding:7px 10px;border-radius:6px;'
                'border-left:4px solid #e0a020;margin-bottom:6px;font-size:14px;color:#6e4300;">'
                f'⚠️ <b>{item["nome"]}</b> · fonte não respondeu, não verificada</div>')
    if item["status"]:
        return ('<div style="background-color:#ffe6e6;padding:7px 10px;border-radius:6px;'
                'border-left:4px solid #ff4b4b;margin-bottom:6px;font-size:14px;color:#7a1010;">'
                f'❌ <b>{item["nome"]}</b></div>')
    return ('<div style="background-color:#e6ffec;padding:7px 10px;border-radius:6px;'
            'border-left:4px solid #28a745;margin-bottom:6px;font-size:14px;color:#155724;">'
            f'✅ <b>{item["nome"]}</b></div>')


def _checklist_html(itens):
    """Checklist agrupado por tema; cada grupo com seu título."""
    por_nome = {r["nome"]: r for r in itens}
    partes = []
    for grupo, nomes in GRUPOS.items():
        do_grupo = [por_nome[n] for n in nomes if n in por_nome]
        if not do_grupo:
            continue
        partes.append('<div style="font-size:12px;font-weight:600;letter-spacing:.04em;'
                      'text-transform:uppercase;opacity:.65;margin:10px 0 5px 0;">'
                      f'{grupo}</div>')
        partes.extend(_badge(r) for r in do_grupo)
    return "".join(partes)


def _mapa(gdf_alvo, itens, key, altura=400):
    centro = [gdf_alvo.unary_union.centroid.y, gdf_alvo.unary_union.centroid.x]
    m = folium.Map(location=centro, zoom_start=12, tiles="Esri World Imagery")
    folium.GeoJson(
        gdf_alvo, name="Imóvel",
        style_function=lambda x: {'color': '#00FFFF', 'fillColor': '#00FFFF', 'fillOpacity': 0.1, 'weight': 2}
    ).add_to(m)

    for item in itens:
        gdf_draw = item["dados"].drop(columns=['geom_original'], errors='ignore').copy()
        is_point = gdf_draw.geometry.iloc[0].geom_type in ['Point', 'MultiPoint']
        for col in gdf_draw.columns:
            if col == 'area_ha_sobreposta' and not is_point:
                gdf_draw[col] = gdf_draw[col].round(4)
            elif col != 'geometry' and (pd.api.types.is_datetime64_any_dtype(gdf_draw[col])
                                        or gdf_draw[col].dtype == 'object'):
                gdf_draw[col] = gdf_draw[col].astype(str)

        cols_tooltip = [c for c in gdf_draw.columns if c != 'geometry']
        if is_point and 'area_ha_sobreposta' in cols_tooltip:
            cols_tooltip.remove('area_ha_sobreposta')
        aliases = [COLUMN_ALIASES.get(c, c) for c in cols_tooltip]

        folium.GeoJson(
            gdf_draw, name=item["nome"],
            style_function=lambda x, c=item["cor"]: {'color': c, 'fillColor': c, 'fillOpacity': 0.5, 'weight': 1},
            tooltip=folium.GeoJsonTooltip(fields=cols_tooltip[:5], aliases=aliases[:5], sticky=True)
            if cols_tooltip else None
        ).add_to(m)

    b = gdf_alvo.total_bounds
    m.fit_bounds([[b[1], b[0]], [b[3], b[2]]])
    folium.LayerControl().add_to(m)
    st_folium(m, height=altura, use_container_width=True, key=key)


def _tabela(item):
    df = pd.DataFrame(item["dados"].drop(columns=['geometry', 'geom_original'], errors='ignore'))
    if item["dados"].geometry.iloc[0].geom_type in ['Point', 'MultiPoint']:
        df = df.drop(columns=['area_ha_sobreposta'], errors='ignore')
    return df.rename(columns=COLUMN_ALIASES)


def _bloco_matriculas(socio):
    feats = st.session_state.get('gdf_features')
    df_mat = _impedimentos_por_matricula(socio, feats)
    if df_mat.empty:
        return
    ui.secao("🗂️ Impedimentos por matrícula")
    n_mat = df_mat["Matrícula"].nunique()
    area_tot = float(df_mat["Área sobreposta (ha)"].sum())
    st.caption(
        f"Os impedimentos atingem **{n_mat}** matrícula(s) · área sobreposta "
        f"total **{_fmt_ha(area_tot)} ha**. Cada linha é um impedimento sobre a "
        "matrícula indicada, com a área invadida (ha) e o quanto representa dela (%). "
        "Pontos (sem área) entram com 0."
    )
    roll = (df_mat.groupby("Matrícula").agg(Impedimentos=("Fonte", "count")).reset_index())
    _a = df_mat.groupby("Matrícula")["Área sobreposta (ha)"].sum().round(4).reset_index()
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


def _secao_socioambiental(socio, gdf_alvo, crs_proj, area_imovel):
    hits = [r for r in socio if r["status"]]
    falhas = [r for r in socio if r.get("indisponivel")]

    ui.secao("🌳 Impedimentos socioambientais")
    if not hits and not falhas:
        st.success("✅ **Nada consta**: nenhuma sobreposição nas bases consultadas.")
    elif not hits:
        st.warning(f"⚠️ **Nada consta nas bases que responderam**, mas {len(falhas)} fonte(s) não "
                   "responderam agora e **não foram verificadas**: "
                   + ", ".join(f["nome"] for f in falhas) + ". Use “Verificar de novo” em instantes.")
    else:
        area = _area_uniao(hits, crs_proj)
        pct = f" ({_fmt_ha(area / area_imovel * 100)}% do imóvel)" if area_imovel > 0 else ""
        st.error(f"⚠️ **{len(hits)} impedimento(s)** sobre o imóvel · área atingida "
                 f"**{_fmt_ha(area)} ha**{pct}.")
        if falhas:
            st.warning(f"{len(falhas)} fonte(s) não responderam e não foram verificadas: "
                       + ", ".join(f["nome"] for f in falhas) + ".")

    if not hits:
        with st.container(border=True, height=430):
            st.markdown(_checklist_html(socio), unsafe_allow_html=True)
        return

    _bloco_matriculas(socio)

    col_check, col_mapa = st.columns([0.42, 0.58], gap="medium")
    with col_check:
        with st.container(border=True, height=470):
            ui.secao("Checklist de verificação")
            st.markdown(_checklist_html(socio), unsafe_allow_html=True)
    with col_mapa:
        with st.container(border=True, height=470):
            ui.secao("Localização das sobreposições")
            _mapa(gdf_alvo, hits, key="imp_map")

    ui.secao("Detalhamento técnico")
    abas = st.tabs([f"🔴 {h['nome']}" for h in hits])
    for aba, item in zip(abas, hits):
        with aba:
            st.dataframe(_tabela(item), use_container_width=True, hide_index=True)


def _secao_mineracao(mine, gdf_alvo, crs_proj, area_imovel):
    ui.secao("⛏️ Mineração (ANM / SIGMINE)")
    st.caption("Processos minerários registrados na ANM sobre a área. Não são restrição ambiental: "
               "indicam direito de terceiro (pesquisa ou lavra) sobre o subsolo, que pode gerar "
               "servidão e afetar o uso e o valor do imóvel.")
    if not mine:
        return
    item = mine[0]
    if item.get("indisponivel"):
        st.warning("⚠️ A base da ANM não respondeu agora; mineração **não verificada**. "
                   "Use “Verificar de novo” em instantes.")
        return
    if not item["status"]:
        st.success("✅ Nenhum processo minerário ativo sobre o imóvel.")
        return

    dados = item["dados"]
    area = _area_uniao([item], crs_proj)
    c1, c2, c3 = st.columns(3)
    c1.metric("Processos sobre o imóvel", len(dados))
    c2.metric("Área atingida", f"{_fmt_ha(area)} ha")
    if area_imovel > 0:
        c3.metric("% do imóvel", f"{_fmt_ha(area / area_imovel * 100)}%")

    col_tab, col_mapa = st.columns([0.5, 0.5], gap="medium")
    with col_tab:
        st.dataframe(_tabela(item), use_container_width=True, hide_index=True,
                     height=min(360, 38 + 35 * len(dados)))
    with col_mapa:
        _mapa(gdf_alvo, [item], key="imp_map_min", altura=360)


def render_tab():
    st.markdown("### Análise de Impedimentos")

    gdf = _alvo()
    if gdf is None:
        ui.vazio()
        return
    gdf_alvo = _preparar(gdf)
    ui.barra_imovel(nome=st.session_state.get('last_code', 'Imóvel Carregado'))

    job = _job_atual() or iniciar_verificacao(gdf)
    if not job["fut"].done():
        _progresso_tab()
        return
    job["avisado"] = True   # quem está nesta aba já vê o resultado

    res = _resultados(job)
    socio, mine = _separar(res)
    geom_uniao = gdf_alvo.unary_union
    crs_proj = calcular_epsg_utm(geom_uniao.centroid)
    area_imovel = gpd.GeoSeries([geom_uniao], crs=WFS_CRS).to_crs(crs_proj).area.iloc[0] / 1e4

    c_txt, c_btn = st.columns([0.72, 0.28], vertical_alignment="center")
    with c_txt:
        n_ok = sum(1 for r in res if not r.get("indisponivel"))
        st.caption(f"Verificação automática ao confirmar o perímetro · {n_ok}/{len(res)} bases responderam.")
    with c_btn:
        if st.button("🔄 Verificar de novo", use_container_width=True):
            iniciar_verificacao(gdf, forcar=True)
            st.rerun()

    _secao_socioambiental(socio, gdf_alvo, crs_proj, area_imovel)
    st.divider()
    _secao_mineracao(mine, gdf_alvo, crs_proj, area_imovel)
