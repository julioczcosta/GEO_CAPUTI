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
        st.warning("⚠️ Por favor, selecione um imóvel na aba '🏠 Home' primeiro.")
        return

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
            buffer_metros = st.slider("Buffer (m)", 0, 2000, 500, step=100, on_change=utils.reset_preview)
            max_nuvens = st.slider("Máx. Nuvens (%)", 0, 100, 30, on_change=utils.reset_preview)
    with c5:
        with st.popover("🎨", use_container_width=True):
            tipo_visualizacao = st.radio("Tipo:", ["RGB", "NDVI", "Falsa Cor"], label_visibility="collapsed", on_change=utils.reset_preview)
    with c6: 
        btn_visualizar = st.button("👁️ Visualizar", use_container_width=True)
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
        m.centerObject(geometry, 13)

        # PROCESSAMENTO (Visualizar)
        if btn_visualizar:
            utils.reset_preview()
            with st.spinner("Processando Sentinel-2..."):
                try:
                    # Define região de visualização (Box)
                    region_viz = geometry.bounds().buffer(buffer_metros)
                    
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
                            
                            # Stats
                            stats = img.reduceRegion(ee.Reducer.mean(), geometry, 30, crs='EPSG:4326', maxPixels=1e9).getInfo()
                            val = stats['NDVI'] if stats['NDVI'] else 0
                            cor = "#2ecc71" if val > 0.6 else "#f1c40f" if val > 0.3 else "#e74c3c"
                            st.session_state['ndvi_stats'] = f"""<div style="position: fixed; bottom: 30px; right: 10px; z-index:9999; background: white; padding: 10px 20px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.3); font-family: sans-serif; text-align: center;"><div style="font-size: 12px; color: #555;">Vigor M&eacute;dio ({mes}/{ano})</div><div style="font-size: 20px; font-weight: bold; color: {cor};">{val:.2f}</div></div>"""
                            grad = f"linear-gradient(to right, {', '.join(vis['palette'])})"
                            st.session_state['ndvi_colorbar'] = f"""<div style="position: fixed; bottom: 30px; left: 10px; z-index:9999; background: white; padding: 10px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.3); font-family: sans-serif;"><div style="font-size: 12px; color: #555; text-align: center; margin-bottom: 4px;">NDVI</div><div style="height: 12px; width: 150px; background: {grad}; border-radius: 4px;"></div><div style="display: flex; justify-content: space-between; font-size: 10px; color: #555; margin-top: 4px;"><span>Solo</span><span>Vigor</span></div></div>"""

                        # --- CONSTRUÇÃO DO NOME DO ARQUIVO ---
                        raw_source = st.session_state.get('source_name', 'Imovel')
                        
                        if "CAR:" in raw_source:
                            # Se for CAR, usa apenas "CAR" como prefixo
                            file_prefix = "CAR"
                        elif "KML:" in raw_source:
                            # Se for KML, usa o nome do arquivo limpo (sem extensão e espaços)
                            # Ex: "KML: Minha Fazenda.kml" -> "Minha_Fazenda"
                            clean_name = raw_source.replace("KML: ", "").replace(".kml", "").replace(".kmz", "").strip()
                            file_prefix = clean_name.replace(" ", "_")
                        else:
                            file_prefix = "Sentinel"
                            
                        # Nome Final: {Prefixo}_{Tipo}_{Mes}_{Ano}
                        filename_final = f"{file_prefix}_{type_suffix}_{mes}_{ano}"

                        # --- GERAÇÃO DO LINK DE DOWNLOAD ---
                        img_download = img.select(download_bands)
                        
                        params_download = {
                            'name': filename_final, # Nome que aparecerá no download
                            'scale': 10,
                            'crs': 'EPSG:4326',
                            'region': region_viz, 
                            'format': 'GEO_TIFF',
                            'maxPixels': 1e9
                        }
                        
                        url = img_download.getDownloadURL(params_download)
                        
                        st.session_state['camada_preview'] = {
                            'ee_object': img, 
                            'vis_params': vis, 
                            'name': nome_camada, 
                            'type': tipo_visualizacao,
                            'download_url': url,
                            'filename': filename_final # Salva nome para usar no botão
                        }
                    else: 
                        st.warning(f"☁️ Nenhuma imagem encontrada em {mes}/{ano} com menos de {max_nuvens}% de nuvens.")
                except Exception as e: 
                    st.error(f"Erro GEE: {e}")

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
                # Botão de Download com o nome correto
                st.markdown(f"""
                    <div style="text-align: center; margin-bottom: 10px;">
                        <a href="{prev['download_url']}" target="_blank" style="text-decoration: none;">
                            <button style="
                                background-color: #2c3e50; 
                                color: white; 
                                border: none; 
                                padding: 10px 20px; 
                                border-radius: 6px; 
                                cursor: pointer; 
                                font-weight: 600;
                                box-shadow: 0 2px 4px rgba(0,0,0,0.2);
                                transition: background-color 0.2s;">
                                📥 Baixar TIFF ({prev.get('filename', 'imagem')}.tif)
                            </button>
                        </a>
                    </div>
                """, unsafe_allow_html=True)

        empty = ee.Image().byte()
        outline = empty.paint(ee.FeatureCollection(geometry), 1, 2)
        m.add_layer(outline, {'palette': 'FF0000'}, "📍 Limite Oficial")
        m.add_layer_control()

        with io.BytesIO() as buffer:
            m.save(buffer, close_file=False)
            map_html = buffer.getvalue().decode('utf-8')
        st.components.v1.html(map_html, height=650, scrolling=False)
        
        if st.button("🗑️ Limpar Mapa"):
            utils.limpar_tudo()
            st.rerun()