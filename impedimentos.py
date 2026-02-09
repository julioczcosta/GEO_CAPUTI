import streamlit as st
import geopandas as gpd
import pandas as pd
import requests
import folium
from streamlit_folium import st_folium
from io import BytesIO
import math

# --- 1. CONFIGURAÇÕES DOS SERVIÇOS WFS ---
WFS_CRS = "EPSG:4674"  # SIRGAS 2000

# Dicionário para renomear colunas (Aliasing)
COLUMN_ALIASES = {
    # Gerais
    'area_ha_sobreposta': 'Área Sobreposta (ha)',
    'geometry': 'Geometria',
    
    # Embargos
    'nom_pessoa': 'Infrator',
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
    
    # UCs
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
    
    # Sítios Arqueológicos
    'identificacao_bem': 'Identificação',
    'ds_natureza': 'Natureza',
    'ds_classificacao': 'Classificação',
    'sintese_bem': 'Síntese',
    'dt_cadastro': 'Data Cadastro',
    
    # Indígenas
    'terrai_nome': 'Terra Indígena',
    'etnia_nome': 'Etnia',
    'fase_ti': 'Fase',
    'modalidade_ti': 'Modalidade',
    'municipio_nome': 'Município',
    'nome_aldeia': 'Aldeia',
    'cod_aldeia': 'Cód. Aldeia',
    'nome_cr': 'Coord. Regional'
}

# Colunas Técnicas para buscar no WFS
WFS_COLUNAS = {
    "publica:vw_brasil_adm_embargo_a": ['nom_pessoa', 'cpf_cnpj_infrator', 'num_auto_infracao', 'qtd_area_desmatada', 'data_cadastro_tad', 'des_infracao', 'respeita_embargo'],
    "ICMBio:embargos_icmbio": ['cpf_cnpj', 'autuado', 'desc_infra', 'tipo_infra', 'nome_uc', 'ano', 'area', 'processo'],
    "ICMBio:autos_infracao_icmbio": ['embargo', 'autuado', 'cpf_cnpj', 'desc_ai', 'valor_mult', 'tipo_infra', 'nome_uc', 'processo'],
    "ICMBio:limiteucsfederais_a": ['nomeuc', 'criacaoano', 'grupouc', 'area_ha'], 
    "MMA:cnuc_2025_08": ['nome_uc', 'cria_ano', 'grupo', 'categoria', 'esfera', 'municipio'],
    "SICG:sitios": ['identificacao_bem', 'ds_natureza', 'ds_classificacao', 'sintese_bem', 'dt_cadastro'],
    "SICG:sitios_pol": ['identificacao_bem', 'id_natureza', 'ds_natureza', 'ds_classificacao', 'sintese_bem', 'dt_cadastro'],
    "Funai:tis_poligonais_portarias": ['terrai_nome', 'etnia_nome', 'fase_ti', 'modalidade_ti', 'municipio_nome'],
    "Funai:tis_pontos_portarias": ['terrai_nome', 'etnia_nome', 'fase_ti', 'modalidade_ti'],
    "Funai:aldeias_pontos": ['nome_aldeia', 'cod_aldeia', 'nome_cr']
}

# Serviços
SERVICES_TO_CHECK = [
    { "name": "Embargo IBAMA", "base_url": "https://siscom.ibama.gov.br/geoserver/publica/ows", "typename": "publica:vw_brasil_adm_embargo_a", "color": "#FF0000" },
    { "name": "Embargo ICMBio", "base_url": "https://geoservicos.inde.gov.br/geoserver/ICMBio/ows", "typename": "ICMBio:embargos_icmbio", "color": "#8B0000" },
    { "name": "Autos Infração ICMBio", "base_url": "https://geoservicos.inde.gov.br/geoserver/ICMBio/ows", "typename": "ICMBio:autos_infracao_icmbio", "color": "#FF4500" },
    { "name": "UCs ICMBio", "base_url": "https://geoservicos.inde.gov.br/geoserver/ICMBio/ows", "typename": "ICMBio:limiteucsfederais_a", "color": "#006400" },
    { "name": "UCs MMA", "base_url": "https://geoservicos.inde.gov.br/geoserver/MMA/ows", "typename": "MMA:cnuc_2025_08", "color": "#228B22" },
    { "name": "Sítios Arq. (Pontos) IPHAN", "base_url": "http://portal.iphan.gov.br/geoserver/ows", "typename": "SICG:sitios", "color": "#DAA520" },
    { "name": "Sítios Arq. (Polígonos) IPHAN", "base_url": "http://portal.iphan.gov.br/geoserver/ows", "typename": "SICG:sitios_pol", "special_params": {"outputFormat": "application/json"}, "color": "#B8860B" },
    { "name": "Terras Indígenas (Polígonos)", "base_url": "https://geoserver.funai.gov.br/geoserver/ows", "typename": "Funai:tis_poligonais_portarias", "version": "1.0.0", "color": "#8B4513" },
    { "name": "Terras Indígenas (Pontos)", "base_url": "https://geoserver.funai.gov.br/geoserver/ows", "typename": "Funai:tis_pontos_portarias", "version": "1.0.0", "color": "#8B4513" },
    { "name": "Aldeias Indígenas", "base_url": "https://geoserver.funai.gov.br/geoserver/ows", "typename": "Funai:aldeias_pontos", "version": "1.0.0", "color": "#A0522D" }
]

# --- 2. FUNÇÕES AUXILIARES ---

def calcular_epsg_utm(geometria_centroide):
    """Calcula UTM automaticamente."""
    try:
        lon, lat = geometria_centroide.x, geometria_centroide.y
        zone = math.floor((lon + 180) / 6) + 1
        return f"EPSG:{31950 + zone}" if lat >= 0 else f"EPSG:{31960 + zone}"
    except:
        return "EPSG:31983"

@st.cache_data(ttl=3600)
def baixar_wfs(url, params):
    """Baixa dados do WFS com cache."""
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
        except:
            r = requests.get(url, params=params, headers=headers, verify=False, timeout=15)
        r.raise_for_status()
        return gpd.read_file(BytesIO(r.content))
    except Exception:
        return gpd.GeoDataFrame()

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
    except:
        gdf_inter['area_ha_sobreposta'] = 0

    cols_existentes = [c for c in colunas if c in gdf_inter.columns]
    cols_final = ['area_ha_sobreposta', 'geometry'] + cols_existentes
    
    return True, gdf_inter[cols_final], None

# --- 3. RENDERIZAÇÃO ---

def render_tab():
    st.markdown("### Análise de Impedimentos Socioambientais")
    
    # --- RESGATE DO IMÓVEL (HOME) ---
    gdf_alvo = None
    
    possible_keys = ['gdf_imovel', 'imovel_upload', 'gdf_perimetro', 'kml_data', 'gdf_data']
    for key in possible_keys:
        if key in st.session_state and isinstance(st.session_state[key], gpd.GeoDataFrame):
            gdf_alvo = st.session_state[key]
            break

    if gdf_alvo is None:
        st.warning("⚠️ Nenhum imóvel identificado.")
        st.markdown("Vá para a aba **Início**, faça o upload e clique em 'Usar Este Perímetro'.")
        return
    else:
        # Pega o nome do código para exibir
        codigo_display = st.session_state.get('last_code', 'Imóvel Carregado')
        st.info(f"Analisando perímetro: **{codigo_display}**")

    # Inicializa estado
    if 'impedimentos_done' not in st.session_state: st.session_state['impedimentos_done'] = False
    if 'impedimentos_results' not in st.session_state: st.session_state['impedimentos_results'] = []
    
    # Botão de Ação
    if st.button("Verificar Impedimentos", type="primary", use_container_width=True):
        
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
            
            params = {
                'service': 'WFS', 'version': srv.get("version", "2.0.0"), 
                'request': 'GetFeature', 'typename': srv["typename"], 
                'srsName': WFS_CRS, 'BBOX': f"{bbox},{WFS_CRS}"
            }
            if srv.get("version") in ["1.0.0", "1.1.0"]: params['BBOX'] = bbox
            if "special_params" in srv: params.update(srv["special_params"])

            try:
                gdf_wfs = baixar_wfs(srv["base_url"], params)
                cols = WFS_COLUNAS.get(srv["typename"], [])
                achou, gdf_res, msg = processar_camada(gdf_wfs, geom_uniao, crs_proj, cols)
                
                resultados.append({
                    "nome": srv["name"], "status": achou, "dados": gdf_res, "cor": srv["color"], "erro": msg
                })
            except Exception as e:
                resultados.append({"nome": srv["name"], "status": False, "dados": None, "cor": "#ccc", "erro": str(e)})

        bar.empty()
        st.session_state['impedimentos_results'] = resultados
        st.session_state['impedimentos_done'] = True
        st.rerun()

    # --- EXIBIÇÃO ---
    if st.session_state['impedimentos_done']:
        st.divider()
        resultados = st.session_state['impedimentos_results']
        encontrou_algum = any(r["status"] for r in resultados)

        # 1. CHECKLIST
        st.markdown("#### Checklist de Verificação")
        c1, c2, c3 = st.columns(3)
        cols_grid = [c1, c2, c3]
        
        for i, item in enumerate(resultados):
            col_atual = cols_grid[i % 3]
            if item["status"]:
                col_atual.markdown(
                    f"""<div style="background-color:#ffe6e6;padding:8px;border-radius:5px;border-left:4px solid #ff4b4b;margin-bottom:8px;font-size:14px;">
                    ❌ <b>{item['nome']}</b></div>""", unsafe_allow_html=True
                )
            else:
                col_atual.markdown(
                    f"""<div style="background-color:#e6ffec;padding:8px;border-radius:5px;border-left:4px solid #28a745;margin-bottom:8px;font-size:14px;color:#155724;">
                    ✅ <b>{item['nome']}</b></div>""", unsafe_allow_html=True
                )

        # 2. MAPA
        if encontrou_algum:
            st.markdown("---")
            st.markdown("#### Localização das Sobreposições")
            
            if gdf_alvo.crs != WFS_CRS: gdf_alvo = gdf_alvo.to_crs(WFS_CRS)
            centro = [gdf_alvo.unary_union.centroid.y, gdf_alvo.unary_union.centroid.x]
            
            m = folium.Map(location=centro, zoom_start=12, tiles="Esri World Imagery")
            
            folium.GeoJson(
                gdf_alvo, name="Imóvel",
                style_function=lambda x: {'color': '#00FFFF', 'fillColor': '#00FFFF', 'fillOpacity': 0.1, 'weight': 2}
            ).add_to(m)

            for item in resultados:
                if item["status"]:
                    gdf_draw = item["dados"].copy()
                    
                    # Identifica se é Ponto
                    is_point = gdf_draw.geometry.iloc[0].geom_type in ['Point', 'MultiPoint']
                    
                    # Sanitização de Dados para o Mapa
                    for col in gdf_draw.columns:
                        # Arredonda Área para o Tooltip (para não aparecer muitas casas)
                        if col == 'area_ha_sobreposta' and not is_point:
                            gdf_draw[col] = gdf_draw[col].round(4)
                        # Converte Datas e Objetos para String (Correção JSON)
                        elif pd.api.types.is_datetime64_any_dtype(gdf_draw[col]) or gdf_draw[col].dtype == 'object':
                            gdf_draw[col] = gdf_draw[col].astype(str)

                    # Renomeia colunas para o Tooltip (Alias)
                    # Cria um dict reverso temporário só com o que tem no gdf
                    aliases_map = {k: v for k, v in COLUMN_ALIASES.items() if k in gdf_draw.columns}
                    
                    # Remove Área se for Ponto
                    cols_tooltip = [c for c in gdf_draw.columns if c not in ['geometry', 'geom_original']]
                    if is_point and 'area_ha_sobreposta' in cols_tooltip:
                        cols_tooltip.remove('area_ha_sobreposta')
                    
                    # Aplica Alias no Tooltip
                    tooltips_aliased = [aliases_map.get(c, c) for c in cols_tooltip]
                    
                    folium.GeoJson(
                        gdf_draw, name=item["nome"],
                        style_function=lambda x, c=item["cor"]: {'color': c, 'fillColor': c, 'fillOpacity': 0.5, 'weight': 1},
                        tooltip=folium.GeoJsonTooltip(
                            fields=cols_tooltip[:5], 
                            aliases=tooltips_aliased[:5], 
                            sticky=True
                        ) if cols_tooltip else None
                    ).add_to(m)
            
            folium.LayerControl().add_to(m)
            st_folium(m, height=500, use_container_width=True)

            # 3. TABELAS DETALHADAS
            st.markdown("---")
            st.markdown("#### Detalhamento Técnico")
            for item in resultados:
                if item["status"]:
                    with st.expander(f"🔴 {item['nome']} (Ver Detalhes)", expanded=True):
                        df_show = pd.DataFrame(item["dados"].drop(columns=['geometry'], errors='ignore'))
                        
                        # Verifica se é ponto para remover coluna de área da Tabela também
                        is_point_table = item["dados"].geometry.iloc[0].geom_type in ['Point', 'MultiPoint']
                        if is_point_table and 'area_ha_sobreposta' in df_show.columns:
                            df_show = df_show.drop(columns=['area_ha_sobreposta'])
                        
                        # Renomeia Colunas usando o dicionário
                        df_show = df_show.rename(columns=COLUMN_ALIASES)
                        
                        st.dataframe(df_show, use_container_width=True, hide_index=True)
        
        else:
            st.markdown("---")
            st.success("Nada Consta: Nenhuma sobreposição encontrada nas bases consultadas.")