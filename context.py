import streamlit as st
import utils
import ui
import ee
import requests

# --- FUNÇÃO LOCAL PARA CONSULTAR CAMADAS EXTRAS DO IBGE ---
def consultar_camadas_extras(lat, lon):
    """Consulta Bioma e Amazônia Legal via WFS do IBGE."""
    base_url = "https://geoservicos.ibge.gov.br/geoserver/ows"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    resultados = {
        "bioma": "Não identificado",
        "amazonia_legal": False
    }
    colunas_geometria = ['geom', 'the_geom']

    # 1. BIOMA
    for geom_col in colunas_geometria:
        try:
            params_bioma = {
                "service": "WFS", "version": "1.0.0", "request": "GetFeature",
                "typeName": "CREN:bioma_vazado", "outputFormat": "application/json",
                "cql_filter": f"INTERSECTS({geom_col}, POINT({lon} {lat}))"
            }
            resp = requests.get(base_url, params=params_bioma, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("features"):
                    resultados["bioma"] = data["features"][0]["properties"].get("bioma", "Não identificado")
                    break
        except Exception: continue

    # 2. AMAZÔNIA LEGAL
    try:
        params_amz = {
            "service": "WFS", "version": "1.0.0", "request": "GetFeature",
            "typeName": "CGEO:AmazoniaLegalLimites", "outputFormat": "application/json",
            "cql_filter": f"INTERSECTS(the_geom, POINT({lon} {lat}))"
        }
        resp_amz = requests.get(base_url, params=params_amz, headers=headers, timeout=10)
        if resp_amz.status_code == 200:
            data_amz = resp_amz.json()
            if data_amz.get("features"):
                resultados["amazonia_legal"] = True
    except Exception:
        pass

    return resultados
    
# --- RENDERIZAÇÃO DA ABA ---
def render_tab():
    st.markdown("### 📚 Contexto Territorial")
    
    geometry = st.session_state.get('current_geometry')
    source_name = st.session_state.get('source_name', 'Desconhecido')
    
    if not geometry:
        ui.vazio()
        return

    # Extração de Coordenadas
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

    # ==========================================
    # BLOCO SUPERIOR: LOCALIZAÇÃO (Largura Total)
    # ==========================================
    ui.barra_imovel(nome=source_name)

    with st.spinner("Mapeando intersecção municipal..."):
        chave_unica = f"{source_name}_{lat_dec}_{lon_dec}"
        dados_muns = utils.obter_municipios_interseccao(geometry, chave_unica)

    mun_selecionado = None
    if not dados_muns or "erro" in dados_muns[0]:
        erro_real = dados_muns[0].get("erro", "lista vazia") if dados_muns else "sem retorno"
        ui.erro("Não foi possível identificar o município de intersecção.", erro_real)
    else:
        if len(dados_muns) == 1:
            mun = dados_muns[0]
            st.success(f"**Inteiramente localizado em:** {mun['municipio']} (Área Identificada: {mun['area_ha']:,.2f} ha)".replace(",", "X").replace(".", ",").replace("X", "."))
            mun_selecionado = mun
        else:
            st.warning(f"**Atenção:** O imóvel intercepta {len(dados_muns)} municípios diferentes.")
            for m in dados_muns:
                pct = m['porcentagem']
                area_fmt = f"{m['area_ha']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                st.markdown(f"**{m['municipio']}** ({pct:.1f}% | {area_fmt} ha)")
                st.progress(int(pct) / 100.0)
            
            nomes_opcoes = [m['municipio'] for m in dados_muns]
            escolha = st.selectbox("Selecione o município base para gerar o contexto:", nomes_opcoes)
            mun_selecionado = next(m for m in dados_muns if m['municipio'] == escolha)

    st.divider()

    # ==========================================
    # CONSULTA CENTRALIZADA DE DADOS
    # ==========================================
    if mun_selecionado:
        with st.spinner("Consultando bases oficiais (IBGE, ANA, OSRM)..."):
            dados_ibge = utils.get_ibge_context_by_code(
                mun_selecionado['cod_ibge'], mun_selecionado['nome_puro'], mun_selecionado['uf'], lat_dec, lon_dec
            )
            dados_altimetria = utils.get_altimetria_municipio(mun_selecionado['cod_ibge'], lat_dec, lon_dec)
            dados_rota = utils.get_distancia_capital(mun_selecionado['cod_ibge'], mun_selecionado['uf'], lat_dec, lon_dec)
            dados_extras = consultar_camadas_extras(lat_dec, lon_dec)
            dados_bacia = utils.get_bacia_info(lat_dec, lon_dec)

        # Divisão em duas colunas equilibradas
        col1, col2 = st.columns(2, gap="medium")

        # ==========================================
        # COLUNA 1: GEOGRAFIA HUMANA E INFRAESTRUTURA
        # ==========================================
        with col1:
            # --- DADOS POLÍTICOS ---
            ui.secao("🏛️ Dados Político-Administrativos")
            if "erro" in dados_ibge:
                st.error(f"{dados_ibge['erro']}")
            else:
                def fmt(num, dec=0):
                    try:
                        val = float(num)
                        return f"{val:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    except Exception: return str(num)

                pop = fmt(dados_ibge['populacao'], 0)
                area = fmt(dados_ibge['area_km2'], 0)
                dens = fmt(dados_ibge['densidade'], 2)
                alt_media = f"{dados_altimetria['media']:.0f}" if "erro" not in dados_altimetria else "N/A"

                with st.container(border=True):
                    st.markdown(f"### {dados_ibge['municipio']} - {dados_ibge['uf']}")
                    st.caption(f"📍 Centroide: {lat_dms}, {lon_dms} | Código IBGE: {dados_ibge['codigo_ibge']}")
                    st.divider()
                    
                    c_a, c_b, c_c = st.columns(3)
                    c_a.metric("👥 População", pop)
                    c_b.metric("📏 Área (km²)", area)
                    c_c.metric("⛰️ Alt. Média", f"{alt_media}m")
                    
                    st.metric("🏙️ Densidade Demográfica", f"{dens} hab/km²")
                    st.divider()
                    
                    st.markdown("**Regionalização**")
                    st.markdown(f"""
                    * **Intermediária:** {dados_ibge['regiao_intermediaria']}
                    * **Imediata:** {dados_ibge['regiao_imediata']}
                    """)
                    st.caption("Fonte: IBGE (Censo 2022) | NASA SRTM")

            # --- LOGÍSTICA ---
            st.write("")
            ui.secao("🛣️ Logística (Sede ➔ Capital)")
            with st.container(border=True):
                if "erro" in dados_rota:
                    st.warning(f"{dados_rota['erro']}")
                else:
                    dist_km = f"{dados_rota['distancia_km']:.1f}".replace(".", ",")
                    c_rota1, c_rota2 = st.columns(2)
                    c_rota1.metric("Distância (Rodovia)", f"{dist_km} km")
                    c_rota2.metric("Tempo Estimado", dados_rota['tempo_estimado'])
                    if dados_rota.get("origem_aprox"):
                        st.caption(
                            "Fonte: Rotas OSRM | ⚠️ Sede do IBGE indisponível — "
                            "origem aproximada pelo centroide do imóvel."
                        )
                    else:
                        st.caption("Fonte: Coordenadas IBGE / Rotas OSRM")


        # ==========================================
        # COLUNA 2: GEOGRAFIA FÍSICA
        # ==========================================
        with col2:
            # --- MEIO AMBIENTE ---
            ui.secao("🌿 Enquadramento Ambiental")
            with st.container(border=True):
                bioma_raw = dados_extras['bioma']
                bioma_display = bioma_raw.title() if bioma_raw else "Não Identificado"
                
                icone = "🌱"
                b_up = bioma_display.upper()
                if "AMAZÔNIA" in b_up: icone = "🌳"
                elif "CERRADO" in b_up: icone = "🌾"
                elif "CAATINGA" in b_up: icone = "🌵"
                elif "MATA" in b_up: icone = "🍂"
                elif "PANTANAL" in b_up: icone = "🐊"

                st.metric("Bioma Predominante", bioma_display)
                st.write("") 
                
                if dados_extras['amazonia_legal']:
                    st.markdown("✅ **Pertence à Amazônia Legal**")
                else:
                    st.markdown("🚫 **Fora da Amazônia Legal**")
                
                st.write("")
                st.caption(f"{icone} Fonte: IBGE (Biomas 2019 & Limites Legais)")

            # --- HIDROGRAFIA ---
            st.write("")
            ui.secao("💧 Hidrografia")
            with st.container(border=True):
                if "erro" in dados_bacia:
                    st.warning(f"{dados_bacia['erro']}")
                else:
                    st.markdown(f"**Bacia:** {dados_bacia['nome_bacia']}")
                    st.markdown(f"*Suprabacia: {dados_bacia['suprabacia']}*")
                    st.markdown("---")
                    st.markdown(f"**Curso Principal:** {dados_bacia['curso_prin']}")
                    st.caption("Fonte: IBGE/CNRH - Bacias Nível 6")

            # --- ARMAZENAGEM ---
            st.write("")
            ui.secao("🏭 Armazenagem (raio de 100 km)")
            with st.container(border=True):
                dados_arm = utils.get_armazens_proximos(lat_dec, lon_dec, raio_km=100)
                if "erro" in dados_arm:
                    st.warning(dados_arm["erro"])
                else:
                    cap_fmt = f"{dados_arm['capacidade_t']:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    c_arm1, c_arm2 = st.columns(2)
                    c_arm1.metric("Unidades Próximas", dados_arm["quantidade"])
                    c_arm2.metric("Capacidade Estática", f"{cap_fmt} t")
                    st.caption("Fonte: CONAB — Armazéns Cadastrados")

        # ==========================================
        # BLOCO INFERIOR: REDAÇÃO DO LAUDO (Largura Total)
        # ==========================================
        st.divider()
        ui.secao("📝 Resumo")
        
        distancia_real = dados_rota.get('distancia_km', 0) if "erro" not in dados_rota else 0
        altitude_real = dados_altimetria.get('media', 0) if "erro" not in dados_altimetria else "N/A"
        
        texto_laudo = utils.gerar_texto_resumo_laudo(
            uf=mun_selecionado['uf'], 
            mun_nome=dados_ibge['municipio'], 
            pop=dados_ibge['populacao'], 
            area_km2=dados_ibge['area_km2'], 
            lat_dec=lat_dec,
            lon_dec=lon_dec,
            lat_dms=lat_dms, 
            lon_dms=lon_dms, 
            altitude=altitude_real, 
            dist_km=distancia_real
        )
        
        st.info(texto_laudo)