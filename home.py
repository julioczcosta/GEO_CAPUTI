import streamlit as st
import utils
import geemap.foliumap as geemap
import ee
import streamlit.components.v1 as components
import io
import geopandas as gpd
import json
from shapely.geometry import shape
import time 

# --- FUNÇÃO DE LIMPEZA GERAL (Reseta Análises) ---
def resetar_analises_anteriores():
    """
    Limpa todas as variáveis de sessão relacionadas a análises anteriores.
    """
    chaves_para_limpar = [
        'camadas_fixas', 'camada_preview', 'ndvi_stats', 'ndvi_colorbar',
        'clim_temp', 'clim_rain', 'last_clim_source', 'ctx_dados', 'gdf_imovel'
    ]
    
    for chave in chaves_para_limpar:
        if chave in st.session_state:
            del st.session_state[chave]

def render_tab():
    st.markdown("###  Seleção do Imóvel")
    
    # --- CSS VISUAL ---
    st.markdown("""
        <style>
        [data-testid="stVerticalBlock"] > [style*="overflow"]::-webkit-scrollbar { display: none; }
        div.stButton > button { width: 100%; }
        div[data-testid="column"] { padding-top: 0px; }
        </style>
    """, unsafe_allow_html=True)

    # --- FUNÇÃO LIMPEZA PREVIEW ---
    def limpar_tela_preview():
        st.session_state['preview_geometry'] = None
        st.session_state['preview_data'] = None

    # =========================================================
    # LINHA 1: INPUTS
    # =========================================================
    row1_col1, row1_col2 = st.columns(2, gap="medium")
    
    with row1_col1:
        with st.container(border=True, height=220):
            st.markdown("##### 1. Buscar Perímetro")
            
            metodo = st.radio("Tipo", ["KML", "CAR"], horizontal=True, label_visibility="collapsed", on_change=limpar_tela_preview)
            
            c_input, c_btn = st.columns([0.80, 0.20], gap="small", vertical_alignment="bottom")
            
            with c_input:
                if metodo == "KML":
                    file_kml = st.file_uploader("KML", type=["kml", "kmz", "zip"], label_visibility="collapsed", key="uploader_kml_home")
                    input_car = None
                else:
                    input_car = st.text_input("CAR", placeholder="Ex: SP-35074...", label_visibility="collapsed")
                    file_kml = None
            
            with c_btn:
                if st.button("🔍", help="Localizar Imóvel", use_container_width=True):
                    limpar_tela_preview()
                    
                    if metodo == "KML":
                        if file_kml:
                            with st.spinner("Lendo KML..."):
                                geom, erro = utils.processar_kml_conteudo(file_kml.read())
                                if not erro and geom:
                                    st.session_state['preview_geometry'] = geom
                                    area_m2 = geom.area(1).getInfo()
                                    st.session_state['preview_data'] = {
                                        "tipo": "KML", "nome": file_kml.name, "area_ha": area_m2 / 10000
                                    }
                                    st.rerun()
                                else: st.error(f"Erro ao ler KML: {erro}")
                        else: st.warning("Anexe um arquivo.")
                    
                    else: # CAR
                        if input_car and len(input_car) > 5:
                            with st.spinner("Consultando SICAR..."):
                                geom, props, erro = utils.get_car_geometry(input_car.strip())
                                if not erro and geom:
                                    st.session_state['preview_geometry'] = geom
                                    props["tipo"] = "CAR"
                                    props["codigo_input"] = input_car.strip() 
                                    st.session_state['preview_data'] = props
                                    st.rerun()
                                else: st.error(erro if erro else "Código não encontrado.")
                        else: st.warning("Digite o código do CAR.")

    with row1_col2:
        with st.container(border=True, height=220):
            st.markdown("##### 2. Instruções")
            st.markdown("""
            <div style="font-size: 0.9rem; line-height: 1.5;">
            <b>1.</b> Selecione <b>KML</b> ou <b>CAR</b>.<br>
            <b>2.</b> Insira o dado e clique na Lupa 🔍.<br>
            <b>3.</b> Confirme os dados no quadro abaixo.<br>
            <b>4.</b> Clique em <b>'✅ Usar Este Perímetro'</b>.<br>
            <br>
            ⚠️ <i>A confirmação irá liberar as abas de análise.</i>
            </div>
            """, unsafe_allow_html=True)

    # =========================================================
    # LINHA 2: MAPA E CONFIRMAÇÃO
    # =========================================================
    row2_col1, row2_col2 = st.columns(2, gap="medium")
    
    with row2_col1:
        with st.container(border=True, height=360):
            st.markdown("##### 3. Visualização")
            
            if st.session_state.get('preview_geometry'):
                m = geemap.Map(
                    center=[-14, -50], zoom=4, height=270, 
                    draw_control=False, scale_control=False, 
                    fullscreen_control=False, attribution_control=False, toolbar_control=False,
                    lite_mode=True
                )
                m.add_basemap("HYBRID")
                m.centerObject(st.session_state['preview_geometry'], 13)
                
                empty = ee.Image().byte()
                outline = empty.paint(ee.FeatureCollection(st.session_state['preview_geometry']), 2, 3)
                m.add_layer(outline, {'palette': 'FF0000'}, "Preview")
                
                with io.BytesIO() as buffer:
                    m.save(buffer, close_file=False)
                    map_html = buffer.getvalue().decode('utf-8')
                st.components.v1.html(map_html, height=270)
            else:
                st.info("Aguardando localização do imóvel...")

    with row2_col2:
        with st.container(border=True, height=360):
            st.markdown("##### 4. Dados do Imóvel")
            
            data = st.session_state.get('preview_data')
            
            if data:
                with st.container(height=240, border=False):
                    if data.get("tipo") == "CAR":
                        # Dados CAR
                        data_lower = {k.lower(): v for k, v in data.items()}
                        def get_val(keys, default="--"):
                            for k in keys:
                                if k in data_lower and data_lower[k]: return data_lower[k]
                            return default

                        cod = get_val(['cod_imovel', 'codigo_imovel', 'codigo_input'])
                        mun = get_val(['nom_municipio', 'municipio'])
                        area_raw = get_val(['num_area_imovel', 'val_area', 'area_imovel', 'area_ir', 'num_area'])
                        
                        if area_raw == "--": 
                             for k, v in data_lower.items():
                                  if 'area' in k and isinstance(v, (int, float)):
                                      area_raw = v; break
                        try: area_fmt = f"{float(area_raw):.2f} ha"
                        except: area_fmt = str(area_raw)

                        st.markdown(f"""
                        <div style="font-size: 0.9rem; line-height: 1.6;">
                            <b>Código:</b> {cod}<br>
                            <b>Município:</b> {mun}<br>
                            <b>Área Declarada:</b> {area_fmt}
                        </div>
                        """, unsafe_allow_html=True)
                        
                    elif data.get("tipo") == "KML":
                        st.markdown(f"""
                        <div style="font-size: 0.9rem; margin-bottom: 20px;">
                            <b>Arquivo:</b> {data.get('nome')}<br>
                            <b>Área Calculada:</b> {data.get('area_ha'):.2f} ha
                        </div>
                        """, unsafe_allow_html=True)

                # --- BOTÃO DE CONFIRMAÇÃO ---
                if st.button("✅ Usar Este Perímetro", use_container_width=True):
                    
                    with st.spinner("Configurando ambiente de análise..."):
                        # 1. FAXINA
                        resetar_analises_anteriores()
                        
                        # 2. DEFINIR NOVO IMÓVEL OFICIAL
                        geom_gee = st.session_state['preview_geometry']
                        st.session_state['current_geometry'] = geom_gee
                        
                        # 3. NOME OFICIAL
                        if data.get("tipo") == "CAR":
                            nome_oficial = data.get('cod_imovel', data.get('codigo_input', 'CAR'))
                            st.session_state['source_name'] = f"CAR: {nome_oficial}"
                            st.session_state['last_code'] = nome_oficial
                        else:
                            nome_oficial = data.get('nome')
                            st.session_state['source_name'] = nome_oficial
                            st.session_state['last_code'] = nome_oficial

                        # 4. CONVERTER PARA GEOPANDAS
                        try:
                            geojson = geom_gee.getInfo()
                            shapely_geom = shape(geojson)
                            gdf_conv = gpd.GeoDataFrame(
                                {'geometry': [shapely_geom]},
                                crs="EPSG:4326"
                            )
                            st.session_state['gdf_imovel'] = gdf_conv
                            
                            # MENSAGEM DE SUCESSO COM DELAY
                            st.success(f"Perímetro definido! Carregando abas...")
                            
                            # Adicionei um Toast também (Notificação flutuante)
                            st.toast(f"Imóvel '{nome_oficial}' carregado com sucesso!", icon="✅")
                            
                            # PAUSA DE 1.5 SEGUNDOS PARA VOCÊ LER A MENSAGEM
                            time.sleep(1.5) 
                            
                            st.rerun()
                            
                        except Exception as e:
                            st.error(f"Erro ao converter geometria: {e}")
            else:
                st.info("Localize um imóvel para liberar a confirmação.")