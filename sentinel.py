import streamlit as st
import ee
import geemap.foliumap as geemap
import io
import streamlit.components.v1 as components
import utils
from datetime import datetime

def render_tab():
    # 1. Verifica Geometria
    geometry = st.session_state.get('current_geometry')
    
    if not geometry:
        st.warning("⚠️ Por favor, selecione um imóvel na aba '🏠 Início' primeiro.")
        return

    # ==========================================
    # 🧠 INTELIGÊNCIA: SELETOR DE GLEBAS
    # ==========================================
    
    # Descobre o tipo de geometria e separa se for múltipla
    geom_type = geometry.type().getInfo()
    is_multipart = False
    
    if geom_type in ['MultiPolygon', 'GeometryCollection']:
        parts = geometry.geometries().getInfo()
        if len(parts) > 1:
            is_multipart = True
            opcoes = []
            geometrias_separadas = []
            
            # Analisa cada fragmento separadamente
            for i, part in enumerate(parts):
                part_ee = ee.Geometry(part)
                area_ha = part_ee.area().divide(10000).getInfo()
                opcoes.append(f"Gleba {i+1} ({area_ha:.1f} ha)")
                geometrias_separadas.append(part_ee)
            
            st.info("🌍 O perímetro contém múltiplas áreas separadas. Selecione a gleba para gerar a imagem:")
            selecao = st.selectbox("Selecione a Gleba", opcoes, label_visibility="collapsed", on_change=utils.reset_preview)
            
            # Pega apenas a geometria que o usuário escolheu
            idx = opcoes.index(selecao)
            geom_alvo = geometrias_separadas[idx]
            area_alvo_ha = float(selecao.split('(')[1].replace(' ha)', ''))
        else:
            geom_alvo = geometry
            area_alvo_ha = geom_alvo.area().divide(10000).getInfo()
    else:
        geom_alvo = geometry
        area_alvo_ha = geom_alvo.area().divide(10000).getInfo()

    # ==========================================
    # 🛡️ TRAVA DE ESCALA (DYNAMIC SCALE)
    # ==========================================
    escala_processamento = 10 # Padrão máximo do Sentinel (10m)
    
    if area_alvo_ha > 20000:
        escala_processamento = 30
    elif area_alvo_ha > 5000:
        escala_processamento = 20
        
    if escala_processamento > 10:
        st.caption(f"⚠️ *Devido à extensão da gleba ({area_alvo_ha:.1f} ha), a resolução do cálculo/download foi ajustada automaticamente para {escala_processamento}m.*")

    st.divider()

    # --- Lógica de Data Dinâmica ---
    agora = datetime.now()
    mes_atual = agora.month
    ano_atual = agora.year
    lista_anos = list(range(2020, ano_atual + 2))
    try: idx_ano_atual = lista_anos.index(ano_atual)
    except ValueError: idx_ano_atual = len(lista_anos) - 1

    # --- Layout da Barra de Ferramentas ---
    c2, c3, c4, c5, c6, c7 = st.columns([0.8, 0.8, 0.5, 0.5, 0.8, 0.8])
    
    with c2: 
        mes = st.selectbox("Mês", range(1, 13), index=mes_atual - 1, label_visibility="collapsed", on_change=utils.reset_preview)
    with c3: 
        ano = st.selectbox("Ano", lista_anos, index=idx_ano_atual, label_visibility="collapsed", on_change=utils.reset_preview)
    with c4:
        with st.popover("⚙️", use_container_width=True):
            # AUMENTADO O BUFFER PARA 5.000 METROS
            buffer_metros = st.slider("Buffer (m)", 0, 5000, 500, step=100, on_change=utils.reset_preview)
            max_nuvens = st.slider("Máx. Nuvens (%)", 0, 100, 30, on_change=utils.reset_preview)
    with c5:
        with st.popover("🎨", use_container_width=True):
            tipo_visualizacao = st.radio("Tipo:", ["RGB", "NDVI", "Falsa Cor"], label_visibility="collapsed", on_change=utils.reset_preview)
    with c6: 
        btn_visualizar = st.button("👁️ Visualizar", type="primary", use_container_width=True)
    with c7: 
        btn_adicionar = st.button("➕ Adicionar", use_container_width=True)

    # Adicionar Camada Fixa
    if btn_adicionar and st.session_state['camada_preview']:
        st.session_state['camadas_fixas'].append(st.session_state['camada_preview'])
        st.toast("Camada fixada no mapa!")
        utils.reset_preview()

    # --- MAPA ---
    with st.container():
        m = geemap.Map(center=[-14, -50], zoom=4, draw_control=False, scale_control=True)
        m.add_basemap("HYBRID")
        
        # Centraliza na gleba selecionada
        m.centerObject(geom_alvo, 13)

        # PROCESSAMENTO (Visualizar)
        if btn_visualizar:
            utils.reset_preview()
            with st.spinner("Processando Sentinel-2 (Isso pode levar alguns segundos)..."):
                try:
                    # Define região de visualização (Box da Gleba + Buffer)
                    region_viz = geom_alvo.bounds().buffer(buffer_metros)
                    
                    coll = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                        .filterBounds(region_viz)
                        .filterDate(f'{ano}-{mes:02d}-01', f'{ano}-{mes:02d}-28')
                        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', max_nuvens)))

                    if coll.size().getInfo() > 0:
                        img = coll.median().clip(region_viz)
                        vis, nome_camada = {}, ""
                        download_bands = []

                        # Configuração das Bandas
                        if tipo_visualizacao == "RGB":
                            vis = {'min': 0, 'max': 3000, 'bands': ['B4', 'B3', 'B2']}
                            nome_camada = f"RGB {mes}/{ano}"
                            download_bands = ['B4', 'B3', 'B2']
                            type_suffix = "RGB"
                            
                        elif tipo_visualizacao == "Falsa Cor":
                            vis = {'min': 0, 'max': 3000, 'bands': ['B8', 'B4', 'B3']}
                            nome_camada = f"Falsa Cor {mes}/{ano}"
                            download_bands = ['B8', 'B4', 'B3']
                            type_suffix = "FalsaCor"
                            
                        elif tipo_visualizacao == "NDVI":
                            img = img.normalizedDifference(['B8', 'B4']).rename('NDVI')
                            vis = {'min': 0, 'max': 0.8, 'palette': ['#d73027', '#f46d43', '#fdae61', '#fee08b', '#d9ef8b', '#a6d96a', '#66bd63', '#1a9850']}
                            nome_camada = f"NDVI {mes}/{ano}"
                            download_bands = ['NDVI']
                            type_suffix = "NDVI"
                            
                            # Stats (Usa a escala inteligente para não travar)
                            stats = img.reduceRegion(ee.Reducer.mean(), geom_alvo, escala_processamento, crs='EPSG:4326', maxPixels=1e10).getInfo()
                            val = stats['NDVI'] if stats['NDVI'] else 0
                            cor = "#2ecc71" if val > 0.6 else "#f1c40f" if val > 0.3 else "#e74c3c"
                            st.session_state['ndvi_stats'] = f"""<div style="position: fixed; bottom: 30px; right: 10px; z-index:9999; background: white; padding: 10px 20px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.3); font-family: sans-serif; text-align: center;"><div style="font-size: 12px; color: #555;">Vigor M&eacute;dio ({mes}/{ano})</div><div style="font-size: 20px; font-weight: bold; color: {cor};">{val:.2f}</div></div>"""
                            grad = f"linear-gradient(to right, {', '.join(vis['palette'])})"
                            st.session_state['ndvi_colorbar'] = f"""<div style="position: fixed; bottom: 30px; left: 10px; z-index:9999; background: white; padding: 10px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.3); font-family: sans-serif;"><div style="font-size: 12px; color: #555; text-align: center; margin-bottom: 4px;">NDVI</div><div style="height: 12px; width: 150px; background: {grad}; border-radius: 4px;"></div><div style="display: flex; justify-content: space-between; font-size: 10px; color: #555; margin-top: 4px;"><span>Solo</span><span>Vigor</span></div></div>"""

                        # --- CONSTRUÇÃO DO NOME DO ARQUIVO ---
                        raw_source = st.session_state.get('source_name', 'Imovel')
                        
                        if "CAR:" in raw_source:
                            file_prefix = "CAR"
                        elif "KML:" in raw_source:
                            clean_name = raw_source.replace("KML: ", "").replace(".kml", "").replace(".kmz", "").strip()
                            file_prefix = clean_name.replace(" ", "_")
                        else:
                            file_prefix = "Sentinel"
                            
                        # Se tiver múltiplas glebas, adiciona no nome do arquivo
                        if is_multipart:
                            gleba_num = selecao.split(" ")[1]
                            filename_final = f"{file_prefix}_Gleba{gleba_num}_{type_suffix}_{mes}_{ano}"
                        else:
                            filename_final = f"{file_prefix}_{type_suffix}_{mes}_{ano}"

                        # --- GERAÇÃO DO LINK DE DOWNLOAD ---
                        img_download = img.select(download_bands)
                        
                        params_download = {
                            'name': filename_final, 
                            'scale': escala_processamento, # Usa a escala inteligente
                            'crs': 'EPSG:4326',
                            'region': region_viz, 
                            'format': 'GEO_TIFF',
                            'maxPixels': 1e13 # Aumentado o teto para não quebrar
                        }
                        
                        url = img_download.getDownloadURL(params_download)
                        
                        st.session_state['camada_preview'] = {
                            'ee_object': img, 
                            'vis_params': vis, 
                            'name': nome_camada, 
                            'type': tipo_visualizacao,
                            'download_url': url,
                            'filename': filename_final 
                        }
                    else: 
                        st.warning(f"☁️ Nenhuma imagem encontrada em {mes}/{ano} com menos de {max_nuvens}% de nuvens.")
                except Exception as e: 
                    st.error(f"Erro no Google Earth Engine: {e}")

        # RENDER LAYERS
        for c in st.session_state['camadas_fixas']: 
            m.add_layer(c['ee_object'], c['vis_params'], c['name'])
            
        if st.session_state['camada_preview']:
            prev = st.session_state['camada_preview']
            m.add_layer(prev['ee_object'], prev['vis_params'], "* " + prev['name'])
            
            if prev.get('type') == "NDVI":
                if st.session_state['ndvi_colorbar']: m.add_html(st.session_state['ndvi_colorbar'])
                if st.session_state['ndvi_stats']: m.add_html(st.session_state['ndvi_stats'])

            if prev.get('download_url'):
                # Botão Secundário (Cinza Outline) conforme nosso CSS
                st.markdown(f"""
                    <div style="text-align: center; margin-bottom: 10px;">
                        <a href="{prev['download_url']}" target="_blank" style="text-decoration: none;">
                            <button style="
                                background-color: transparent; 
                                color: #2C3E50; 
                                border: 1px solid #2C3E50; 
                                padding: 10px 20px; 
                                border-radius: 6px; 
                                cursor: pointer; 
                                font-weight: 600;
                                transition: all 0.3s ease;"
                                onmouseover="this.style.backgroundColor='#2C3E50'; this.style.color='white';"
                                onmouseout="this.style.backgroundColor='transparent'; this.style.color='#2C3E50';">
                                📥 Baixar TIFF ({prev.get('filename', 'imagem')}.tif)
                            </button>
                        </a>
                    </div>
                """, unsafe_allow_html=True)

        # Desenha a geometria total (em cinza claro/fino) e a Gleba Alvo (em Vermelho forte)
        empty = ee.Image().byte()
        if is_multipart:
            outline_full = empty.paint(geometry, 1, 1)
            m.add_layer(outline_full, {'palette': 'gray'}, "Todas as Áreas", False)
            
        outline_alvo = empty.paint(ee.FeatureCollection(geom_alvo), 1, 3)
        m.add_layer(outline_alvo, {'palette': 'FF0000'}, "📍 Área Selecionada")
        
        m.add_layer_control()

        with io.BytesIO() as buffer:
            m.save(buffer, close_file=False)
            map_html = buffer.getvalue().decode('utf-8')
        st.components.v1.html(map_html, height=650, scrolling=False)
        
        # O botão Limpar fica Cinza conforme CSS
        if st.button("🗑️ Limpar Mapa", use_container_width=True):
            utils.limpar_analises()
            st.rerun()