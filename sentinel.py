import streamlit as st
import ee
import geemap.foliumap as geemap
import io
import streamlit.components.v1 as components
import utils
import ui
from datetime import datetime
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

def render_tab():
    # 1. Verifica Geometria e Nome do Imóvel Atual
    geometry = st.session_state.get('current_geometry')
    source_name = st.session_state.get('source_name', 'Imovel')
    
    if not geometry:
        ui.vazio()
        return

    ui.barra_imovel(nome=source_name)

    # ==========================================
    # 🧹 FAXINA AUTOMÁTICA
    # ==========================================
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
        except Exception: area_alvo_ha = 100

    # ==========================================
    # 🛡️ TRAVA DE ESCALA E SEGURANÇA
    # ==========================================
    escala_processamento = 10 
    
    if area_alvo_ha > 20000:
        escala_processamento = 30
    elif area_alvo_ha > 5000:
        escala_processamento = 20
        
    if escala_processamento > 10:
        st.caption(f"⚠️ *Devido à extensão ({area_alvo_ha:.1f} ha), a resolução foi ajustada para {escala_processamento}m para não travar o download.*")

    st.divider()

    # --- Lógica de Data Dinâmica (DESDE 2000) ---
    agora = datetime.now()
    mes_atual = agora.month
    ano_atual = agora.year
    
    # MÁQUINA DO TEMPO: Do ano 2000 até o ano atual
    lista_anos = list(range(2000, ano_atual + 1)) 
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
            buffer_metros = st.slider("Buffer (m)", 0, 3000, 300, step=100, on_change=utils.reset_preview)
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
        except Exception: pass

        # ==========================================
        # 🚀 O MOTOR MULTI-SATÉLITE
        # ==========================================
        if btn_visualizar:
            utils.reset_preview()
            with st.spinner(f"Buscando imagens de {mes}/{ano} e filtrando nuvens..."):
                try:
                    region_viz = geom_alvo.bounds().buffer(buffer_metros, 100).bounds()

                    # 1. SELEÇÃO INTELIGENTE DA CONSTELAÇÃO E FONTE
                    if ano >= 2016:
                        # SENTINEL-2 (Alta Resolução 10m)
                        sat_name = "Sentinel-2"
                        atribuicao_fonte = "Sentinel-2/ESA"
                        col_id = 'COPERNICUS/S2_SR_HARMONIZED'
                        cloud_pct = 'CLOUDY_PIXEL_PERCENTAGE'
                        b_red, b_green, b_blue, b_nir = 'B4', 'B3', 'B2', 'B8'
                        vis_min, vis_max = 0, 3000
                        
                        coll = (ee.ImageCollection(col_id)
                            .filterBounds(region_viz)
                            .filterDate(f'{ano}-{mes:02d}-01', f'{ano}-{mes:02d}-28')
                            .filter(ee.Filter.lt(cloud_pct, max_nuvens)))
                            
                        if coll.size().getInfo() > 0:
                            img = coll.median().clip(region_viz)
                        else:
                            img = None

                    else:
                        # FAMÍLIA LANDSAT (Resolução 30m - Máquina do Tempo)
                        if ano >= 2013:
                            sat_name = "Landsat 8"
                            atribuicao_fonte = "Landsat 8/USGS"
                            col_id = 'LANDSAT/LC08/C02/T1_L2'
                            b_red, b_green, b_blue, b_nir = 'SR_B4', 'SR_B3', 'SR_B2', 'SR_B5'
                        elif ano == 2012:
                            sat_name = "Landsat 7"
                            atribuicao_fonte = "Landsat 7/USGS"
                            col_id = 'LANDSAT/LE07/C02/T1_L2'
                            b_red, b_green, b_blue, b_nir = 'SR_B3', 'SR_B2', 'SR_B1', 'SR_B4'
                        else:
                            sat_name = "Landsat 5"
                            atribuicao_fonte = "Landsat 5/USGS"
                            col_id = 'LANDSAT/LT05/C02/T1_L2'
                            b_red, b_green, b_blue, b_nir = 'SR_B3', 'SR_B2', 'SR_B1', 'SR_B4'
                            
                        cloud_pct = 'CLOUD_COVER'
                        vis_min, vis_max = 0.0, 0.3 
                        
                        def scale_landsat(image):
                            optical = image.select('SR_B.').multiply(0.0000275).add(-0.2)
                            return image.addBands(optical, None, True)

                        coll = (ee.ImageCollection(col_id)
                            .filterBounds(region_viz)
                            .filterDate(f'{ano}-{mes:02d}-01', f'{ano}-{mes:02d}-28')
                            .filter(ee.Filter.lt(cloud_pct, max_nuvens)))
                            
                        if coll.size().getInfo() > 0:
                            img = coll.map(scale_landsat).median().clip(region_viz)
                        else:
                            img = None

                    # 2. RENDERIZAÇÃO E CÁLCULOS
                    if img is not None:
                        vis, nome_camada = {}, ""
                        download_bands = []

                        if tipo_visualizacao == "RGB":
                            vis = {'min': vis_min, 'max': vis_max, 'bands': [b_red, b_green, b_blue]}
                            nome_camada = f"RGB {sat_name} {mes}/{ano}"
                            download_bands = [b_red, b_green, b_blue]
                            type_suffix = "RGB"
                            
                        elif tipo_visualizacao == "Falsa Cor":
                            vis = {'min': vis_min, 'max': vis_max, 'bands': [b_nir, b_red, b_green]}
                            nome_camada = f"Falsa Cor {sat_name} {mes}/{ano}"
                            download_bands = [b_nir, b_red, b_green]
                            type_suffix = "FalsaCor"
                            
                        elif tipo_visualizacao == "NDVI":
                            img = img.normalizedDifference([b_nir, b_red]).rename('NDVI')
                            vis = {'min': 0, 'max': 0.8, 'palette': ['#d73027', '#f46d43', '#fdae61', '#fee08b', '#d9ef8b', '#a6d96a', '#66bd63', '#1a9850']}
                            nome_camada = f"NDVI {sat_name} {mes}/{ano}"
                            download_bands = ['NDVI']
                            type_suffix = "NDVI"
                            
                            stats = img.reduceRegion(ee.Reducer.mean(), geom_alvo, escala_processamento, crs='EPSG:4326', maxPixels=1e13).getInfo()
                            val = stats['NDVI'] if stats['NDVI'] else 0
                            cor = "#2ecc71" if val > 0.6 else "#f1c40f" if val > 0.3 else "#e74c3c"
                            st.session_state['ndvi_stats'] = f"""<div style="position: fixed; bottom: 30px; right: 10px; z-index:9999; background: white; padding: 10px 20px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.3); font-family: sans-serif; text-align: center;"><div style="font-size: 12px; color: #555;">Vigor M&eacute;dio ({mes}/{ano})</div><div style="font-size: 20px; font-weight: bold; color: {cor};">{val:.2f}</div></div>"""
                            grad = f"linear-gradient(to right, {', '.join(vis['palette'])})"
                            st.session_state['ndvi_colorbar'] = f"""<div style="position: fixed; bottom: 30px; left: 10px; z-index:9999; background: white; padding: 10px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.3); font-family: sans-serif;"><div style="font-size: 12px; color: #555; text-align: center; margin-bottom: 4px;">NDVI</div><div style="height: 12px; width: 150px; background: {grad}; border-radius: 4px;"></div><div style="display: flex; justify-content: space-between; font-size: 10px; color: #555; margin-top: 4px;"><span>Solo</span><span>Vigor</span></div></div>"""

                        # --- LÓGICA DE NOMEAÇÃO SIMPLIFICADA SOLICITADA ---
                        filename_final = f"{type_suffix}_{mes:02d}_{ano}"
                        # --------------------------------------------------

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
                        
                        # CAIXA FLUTUANTE DE ATRIBUIÇÃO DA FONTE SOLICITADA
                        atribuicao_html = f"""<div style="position: fixed; bottom: 10px; right: 10px; z-index:9999; background: rgba(255, 255, 255, 0.7); padding: 2px 6px; border-radius: 4px; font-family: sans-serif; font-size: 10px; color: #555; pointer-events: none;">{atribuicao_fonte}</div>"""

                        st.session_state['camada_preview'] = {
                            'ee_object': img, 
                            'vis_params': vis, 
                            'name': nome_camada, 
                            'type': tipo_visualizacao,
                            'download_url': url,
                            'filename': filename_final,
                            'fonte_atribuicao': atribuicao_html # Guardamos o HTML da fonte
                        }
                    else: 
                        st.warning(f"☁️ Nenhuma imagem clara o suficiente encontrada (Máx. {max_nuvens}% nuvens) para este período.")
                except Exception as e: 
                    st.error(f"Erro GEE: {e}")

        # Renderização das Camadas
        for c in st.session_state['camadas_fixas']: 
            m.add_layer(c['ee_object'], c['vis_params'], c['name'])
            
        if st.session_state['camada_preview']:
            prev = st.session_state['camada_preview']
            m.add_layer(prev['ee_object'], prev['vis_params'], "* " + prev['name'])
            
            # Adiciona os elementos flutuantes no mapa
            if prev.get('fonte_atribuicao'): m.add_html(prev['fonte_atribuicao']) # FONTE SOLICITADA

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
            except Exception: pass
            
        try:
            outline_alvo = empty.paint(ee.FeatureCollection(geom_alvo), 1, 3)
            m.add_layer(outline_alvo, {'palette': 'FF0000'}, "📍 Bloco Selecionado")
        except Exception: pass
        
        m.add_layer_control()

        with io.BytesIO() as buffer:
            m.save(buffer, close_file=False)
            map_html = buffer.getvalue().decode('utf-8')
        st.components.v1.html(map_html, height=650, scrolling=False)
        
        if st.button("🗑️ Limpar Mapa", use_container_width=True):
            utils.limpar_analises()
            st.rerun()