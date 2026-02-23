import streamlit as st
import ee
import geemap.foliumap as geemap
import io
import streamlit.components.v1 as components
import utils
from datetime import datetime
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

def render_tab():
    # 1. Verifica Geometria e Nome do Imóvel Atual
    geometry = st.session_state.get('current_geometry')
    source_name = st.session_state.get('source_name', 'Imovel')
    
    if not geometry:
        st.warning("⚠️ Por favor, selecione um imóvel na aba '🏠 Início' primeiro.")
        return

    # ==========================================
    # 🧹 FAXINA AUTOMÁTICA (O Segredo para não misturar fazendas)
    # ==========================================
    # Se o nome do imóvel mudou desde a última vez que abrimos essa aba,
    # apagamos todas as imagens e visualizações antigas da memória.
    if st.session_state.get('last_sentinel_source') != source_name:
        utils.reset_preview()
        st.session_state['camadas_fixas'] = []
        st.session_state['last_sentinel_source'] = source_name

    # ==========================================
    # 🧠 INTELIGÊNCIA: CLUSTERING DE GLEBAS 
    # ==========================================
    is_multipart = False
    opcoes = []
    geometrias_separadas = []
    area_total_ha = 0
    
    with st.spinner("Analisando a topologia e distribuição das áreas..."):
        try:
            geom_dict = geometry.getInfo()
            
            if geom_dict.get('type') == 'FeatureCollection':
                geoms_base = [shape(f['geometry']) for f in geom_dict.get('features', [])]
                shp_geom = unary_union(geoms_base)
            else:
                shp_geom = shape(geom_dict)
            
            if shp_geom.geom_type in ['MultiPolygon', 'GeometryCollection']:
                poligonos = list(shp_geom.geoms) if shp_geom.geom_type == 'MultiPolygon' else [g for g in shp_geom.geoms if g.geom_type == 'Polygon']
                
                if len(poligonos) > 1:
                    areas_infladas = unary_union([p.buffer(0.002) for p in poligonos])
                    blocos_fundidos = [areas_infladas] if areas_infladas.geom_type == 'Polygon' else list(areas_infladas.geoms)
                    
                    if len(blocos_fundidos) > 1:
                        is_multipart = True
                        
                        area_total_ha = geometry.area().divide(10000).getInfo()
                        opcoes.append(f"🟩 Visualizar Tudo Junto ({area_total_ha:.1f} ha)")
                        geometrias_separadas.append(geometry)
                        
                        for i, bloco_inflado in enumerate(blocos_fundidos):
                            pols_originais = [p for p in poligonos if p.intersects(bloco_inflado)]
                            bloco_limpo = unary_union(pols_originais)
                            
                            gee_bloco = ee.Geometry(mapping(bloco_limpo))
                            area_ha = gee_bloco.area().divide(10000).getInfo()
                            
                            opcoes.append(f"📍 Bloco Isolado {i+1} ({area_ha:.1f} ha)")
                            geometrias_separadas.append(gee_bloco)
                            
        except Exception as e:
            pass

    if is_multipart:
        st.info("🌍 Foram identificadas áreas geograficamente distantes. Escolha a porção para visualizar:")
        selecao = st.selectbox("Selecione o Bloco", opcoes, label_visibility="collapsed", on_change=utils.reset_preview)
        
        idx = opcoes.index(selecao)
        geom_alvo = geometrias_separadas[idx]
        
        if idx == 0:
            area_alvo_ha = area_total_ha
        else:
            area_alvo_ha = float(selecao.split('(')[1].replace(' ha)', ''))
    else:
        geom_alvo = geometry
        try: area_alvo_ha = geom_alvo.area().divide(10000).getInfo()
        except: area_alvo_ha = 100

    # ==========================================
    # 🛡️ TRAVA DE ESCALA E SEGURANÇA (DYNAMIC SCALE)
    # ==========================================
    escala_processamento = 10 
    
    if area_alvo_ha > 20000:
        escala_processamento = 30
    elif area_alvo_ha > 5000:
        escala_processamento = 20
        
    if escala_processamento > 10:
        st.caption(f"⚠️ *Devido à extensão ({area_alvo_ha:.1f} ha), a resolução foi ajustada para {escala_processamento}m para não travar o download.*")

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
            buffer_metros = st.slider("Buffer (m)", 0, 5000, 500, step=100, on_change=utils.reset_preview)
            max_nuvens = st.slider("Máx. Nuvens (%)", 0, 100, 30, on_change=utils.reset_preview)
    with c5:
        with st.popover("🎨", use_container_width=True):
            tipo_visualizacao = st.radio("Tipo:", ["RGB", "NDVI", "Falsa Cor"], label_visibility="collapsed", on_change=utils.reset_preview)
    with c6: 
        btn_visualizar = st.button("👁️ Visualizar", type="primary", use_container_width=True)
    with c7: 
        btn_adicionar = st.button("➕ Adicionar", use_container_width=True)

    if btn_adicionar and st.session_state['camada_preview']:
        st.session_state['camadas_fixas'].append(st.session_state['camada_preview'])
        st.toast("Camada fixada no mapa!")
        utils.reset_preview()

    # --- MAPA ---
    with st.container():
        m = geemap.Map(center=[-14, -50], zoom=4, draw_control=False, scale_control=True)
        m.add_basemap("HYBRID")
        
        try: m.centerObject(geom_alvo, 13)
        except: pass

        # PROCESSAMENTO (Visualizar)
        if btn_visualizar:
            utils.reset_preview()
            with st.spinner("Buscando imagens e filtrando nuvens..."):
                try:
                    region_viz = geom_alvo.bounds().buffer(buffer_metros, 100).bounds()
                    
                    coll = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                        .filterBounds(region_viz)
                        .filterDate(f'{ano}-{mes:02d}-01', f'{ano}-{mes:02d}-28')
                        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', max_nuvens)))

                    if coll.size().getInfo() > 0:
                        img = coll.median().clip(region_viz)
                        vis, nome_camada = {}, ""
                        download_bands = []

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
                            
                            stats = img.reduceRegion(ee.Reducer.mean(), geom_alvo, escala_processamento, crs='EPSG:4326', maxPixels=1e13).getInfo()
                            val = stats['NDVI'] if stats['NDVI'] else 0
                            cor = "#2ecc71" if val > 0.6 else "#f1c40f" if val > 0.3 else "#e74c3c"
                            st.session_state['ndvi_stats'] = f"""<div style="position: fixed; bottom: 30px; right: 10px; z-index:9999; background: white; padding: 10px 20px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.3); font-family: sans-serif; text-align: center;"><div style="font-size: 12px; color: #555;">Vigor M&eacute;dio ({mes}/{ano})</div><div style="font-size: 20px; font-weight: bold; color: {cor};">{val:.2f}</div></div>"""
                            grad = f"linear-gradient(to right, {', '.join(vis['palette'])})"
                            st.session_state['ndvi_colorbar'] = f"""<div style="position: fixed; bottom: 30px; left: 10px; z-index:9999; background: white; padding: 10px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.3); font-family: sans-serif;"><div style="font-size: 12px; color: #555; text-align: center; margin-bottom: 4px;">NDVI</div><div style="height: 12px; width: 150px; background: {grad}; border-radius: 4px;"></div><div style="display: flex; justify-content: space-between; font-size: 10px; color: #555; margin-top: 4px;"><span>Solo</span><span>Vigor</span></div></div>"""

                        raw_source = st.session_state.get('source_name', 'Imovel')
                        
                        if "CAR:" in raw_source: file_prefix = "CAR"
                        elif "KML:" in raw_source:
                            clean_name = raw_source.replace("KML: ", "").replace(".kml", "").replace(".kmz", "").strip()
                            file_prefix = clean_name.replace(" ", "_")
                        else: file_prefix = "Sentinel"
                            
                        if is_multipart and selecao != opcoes[0]:
                            gleba_num = selecao.split(" ")[2] 
                            filename_final = f"{file_prefix}_Bloco{gleba_num}_{type_suffix}_{mes}_{ano}"
                        else:
                            filename_final = f"{file_prefix}_AreaTotal_{type_suffix}_{mes}_{ano}"

                        img_download = img.select(download_bands)
                        
                        params_download = {
                            'name': filename_final, 
                            'scale': escala_processamento,
                            'crs': 'EPSG:4326',
                            'region': region_viz, 
                            'format': 'GEO_TIFF',
                            'maxPixels': 1e13
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
                        st.warning(f"☁️ Nenhuma imagem clara o suficiente encontrada (Máx. {max_nuvens}% nuvens).")
                except Exception as e: 
                    st.error(f"Erro GEE: {e}")

        # Renderização das Camadas
        for c in st.session_state['camadas_fixas']: 
            m.add_layer(c['ee_object'], c['vis_params'], c['name'])
            
        if st.session_state['camada_preview']:
            prev = st.session_state['camada_preview']
            m.add_layer(prev['ee_object'], prev['vis_params'], "* " + prev['name'])
            
            if prev.get('type') == "NDVI":
                if st.session_state['ndvi_colorbar']: m.add_html(st.session_state['ndvi_colorbar'])
                if st.session_state['ndvi_stats']: m.add_html(st.session_state['ndvi_stats'])

            if prev.get('download_url'):
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
                                📥 Baixar Imagem TIFF ({prev.get('filename', 'imagem')}.tif)
                            </button>
                        </a>
                    </div>
                """, unsafe_allow_html=True)

        # Desenho das Geometrias
        empty = ee.Image().byte()
        if is_multipart and selecao != opcoes[0]:
            try:
                outline_full = empty.paint(geometry, 1, 1)
                m.add_layer(outline_full, {'palette': 'gray'}, "Todas as Áreas", False)
            except: pass
            
        try:
            outline_alvo = empty.paint(ee.FeatureCollection(geom_alvo), 1, 3)
            m.add_layer(outline_alvo, {'palette': 'FF0000'}, "📍 Bloco Selecionado")
        except: pass
        
        m.add_layer_control()

        with io.BytesIO() as buffer:
            m.save(buffer, close_file=False)
            map_html = buffer.getvalue().decode('utf-8')
        st.components.v1.html(map_html, height=650, scrolling=False)
        
        if st.button("🗑️ Limpar Mapa", use_container_width=True):
            utils.limpar_analises()
            st.rerun()