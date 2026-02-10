import streamlit as st
import utils
import ee
import math
import requests

# --- FUNÇÃO LOCAL PARA CONSULTAR CAMADAS EXTRAS DO IBGE ---
def consultar_camadas_extras(lat, lon):
    """
    Consulta Bioma e Amazônia Legal via WFS do IBGE.
    Testa múltiplas colunas de geometria para evitar erros.
    """
    base_url = "https://geoservicos.ibge.gov.br/geoserver/ows"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    resultados = {
        "bioma": "Não identificado",
        "amazonia_legal": False
    }
    
    # Lista de possíveis nomes de coluna de geometria no GeoServer do IBGE
    colunas_geometria = ['geom', 'the_geom']

    # 1. BIOMA (Camada: CREN:bioma_vazado)
    for geom_col in colunas_geometria:
        try:
            params_bioma = {
                "service": "WFS",
                "version": "1.0.0",
                "request": "GetFeature",
                "typeName": "CREN:bioma_vazado",
                "outputFormat": "application/json",
                "cql_filter": f"INTERSECTS({geom_col}, POINT({lon} {lat}))"
            }
            resp = requests.get(base_url, params=params_bioma, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("features"):
                    resultados["bioma"] = data["features"][0]["properties"].get("bioma", "Não identificado")
                    break
        except: continue

    # 2. AMAZÔNIA LEGAL (Camada: CGMAT:lim_amazonia_legal_2022)
    for geom_col in colunas_geometria:
        try:
            params_amz = {
                "service": "WFS",
                "version": "1.0.0",
                "request": "GetFeature",
                "typeName": "CGMAT:lim_amazonia_legal_2022",
                "outputFormat": "application/json",
                "cql_filter": f"INTERSECTS({geom_col}, POINT({lon} {lat}))"
            }
            resp_amz = requests.get(base_url, params=params_amz, headers=headers, timeout=10)
            if resp_amz.status_code == 200:
                data_amz = resp_amz.json()
                # Se retornar features, significa que INTERSECTA a área da Amazônia Legal
                if data_amz.get("features"):
                    resultados["amazonia_legal"] = True
                    break
        except: continue

    return resultados

# --- RENDERIZAÇÃO DA ABA ---
def render_tab():
    st.markdown("###  Contexto Territorial")
    
    geometry = st.session_state.get('current_geometry')
    source_name = st.session_state.get('source_name', 'Desconhecido')
    
    if not geometry:
        st.warning("⚠️ Selecione um imóvel na aba 'Início' para carregar os dados de contexto.")
        return

    st.markdown(f"**Imóvel Analisado:** {source_name}")
    st.divider()

    centroide = geometry.centroid(1).coordinates().getInfo()
    lon_dec, lat_dec = centroide[0], centroide[1]

    def decimal_to_dms(deg, is_lat):
        direction = 'N' if is_lat and deg >= 0 else 'S' if is_lat else 'E' if deg >= 0 else 'O'
        deg = abs(deg)
        d = int(deg)
        m = int((deg - d) * 60)
        s = (deg - d - m/60) * 3600
        return f"{d}° {m}' {s:.2f}'' {direction}"

    lat_dms = decimal_to_dms(lat_dec, True)
    lon_dms = decimal_to_dms(lon_dec, False)
    
    # --- CONSULTAS ---
    with st.spinner("Consultando bases territoriais (IBGE)..."):
        dados_ibge = utils.get_ibge_context(lat_dec, lon_dec)
        dados_extras = consultar_camadas_extras(lat_dec, lon_dec)

    col1, col2 = st.columns(2, gap="medium")

    # ==========================================
    # COLUNA 1: DADOS POLÍTICOS
    # ==========================================
    with col1:
        st.subheader("🏛️ Dados Político-Administrativos")
        
        if "erro" in dados_ibge:
            st.error(f"{dados_ibge['erro']}")
        else:
            def fmt(num, dec=0):
                try:
                    val = float(num)
                    return f"{val:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
                except: return str(num)

            pop = fmt(dados_ibge['populacao'], 0)
            area = fmt(dados_ibge['area_km2'], 2)
            dens = fmt(dados_ibge['densidade'], 2)

            with st.container(border=True):
                st.markdown(f"### {dados_ibge['municipio']} - {dados_ibge['uf']}")
                st.caption(f"📍 {lat_dms}, {lon_dms}")
                st.caption(f"Código IBGE: {dados_ibge['codigo_ibge']}")
                
                st.divider()
                
                c_a, c_b = st.columns(2)
                c_a.metric("👥 População", f"{pop} hab.")
                c_b.metric("📏 Área Mun.", f"{area} km²")
                st.metric("🏙️ Densidade", f"{dens} hab/km²")
                
                st.divider()
                
                st.markdown("**Regionalização**")
                st.markdown(f"""
                * **Intermediária:** {dados_ibge['regiao_intermediaria']}
                * **Imediata:** {dados_ibge['regiao_imediata']}
                """)
                st.caption("Fonte: IBGE (Censo 2022)")

    # ==========================================
    # COLUNA 2: DADOS AMBIENTAIS E LEGAIS
    # ==========================================
    with col2:
        st.subheader("🌿 Enquadramento Ambiental")

        # --- BIOMA + AMAZÔNIA LEGAL ---
        with st.container(border=True):
            # Tratamento do nome do Bioma
            bioma_raw = dados_extras['bioma']
            bioma_display = bioma_raw.title() if bioma_raw else "Não Identificado"
            
            # Ícone do Bioma
            icone = "🌱"
            b_up = bioma_display.upper()
            if "AMAZÔNIA" in b_up: icone = "🌳"
            elif "CERRADO" in b_up: icone = "🌾"
            elif "CAATINGA" in b_up: icone = "🌵"
            elif "MATA" in b_up: icone = "🍂"
            elif "PANTANAL" in b_up: icone = "🐊"

            # Exibe Bioma
            st.metric("Bioma Predominante", bioma_display)
            
            st.write("") # Espaço visual
            
            # --- AMAZÔNIA LEGAL (TEXTO DIRETO) ---
            if dados_extras['amazonia_legal']:
                st.markdown("✅ **Pertence à Amazônia Legal**")
            else:
                st.markdown("🚫 **Fora da Amazônia Legal**")
            
            st.write("")
            st.caption(f"{icone} Fonte: IBGE (Biomas 2019 & Limites Legais)")

        # --- CLIMA ---
        st.write("")
        st.markdown("**🌦️ Clima**")
        dados_koppen = utils.get_koppen_class(lat_dec, lon_dec)
        
        if dados_koppen and "erro" not in dados_koppen:
            sigla = dados_koppen.get('Classificacao', 'N/A')
            desc = dados_koppen.get('Descricao', 'Sem descrição')
            
            with st.container(border=True):
                st.metric("Classificação Köppen", sigla)
                st.info(desc, icon="🌡️")
        else:
            st.warning("Clima não identificado.")

        # --- HIDROGRAFIA ---
        st.write("")
        st.markdown("**💧 Hidrografia**")
        with st.spinner("Identificando Bacia..."):
            dados_bacia = utils.get_bacia_info(lat_dec, lon_dec)
        
        if "erro" in dados_bacia:
            st.warning(f"{dados_bacia['erro']}")
        else:
            with st.container(border=True):
                st.markdown(f"**Bacia:** {dados_bacia['nome_bacia']}")
                st.markdown(f"*Suprabacia: {dados_bacia['suprabacia']}*")
                
                st.markdown("---")
                st.markdown(f"**Principal:** {dados_bacia['curso_prin']}")
                st.caption("Fonte: IBGE/CNRH - Bacias Nível 6")