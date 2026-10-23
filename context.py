import os
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
import utils
import ui
import ee
import requests
from shapely.geometry import shape as _shape, Point as _Point


@st.cache_resource(show_spinner=False)
def _biomas_poligonos():
    """Polígonos dos 6 biomas (IBGE, baixados do geoftp) para detecção LOCAL.

    O geoserver do IBGE (CREN:bioma_vazado) caiu atrás do Cloudflare (HTTP 403),
    então o bioma passa a ser resolvido por ponto-em-polígono num geojson embutido
    (dados/biomas_br.geojson). Fonte: IBGE, Biomas 1:5.000.000.
    """
    p = os.path.join(os.path.dirname(__file__), "dados", "biomas_br.geojson")
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            gj = json.load(f)
        for feat in gj.get("features", []):
            nome = (feat.get("properties") or {}).get("bioma")
            geom = _shape(feat["geometry"])
            if nome and geom is not None and not geom.is_empty:
                out.append((nome, geom))
    except Exception:
        pass
    return out


def _bioma_no_ponto(lat, lon):
    pt = _Point(lon, lat)
    for nome, geom in _biomas_poligonos():
        try:
            if geom.contains(pt) or geom.intersects(pt):
                return nome
        except Exception:
            continue
    return "Não identificado"


# --- FUNÇÃO LOCAL PARA CONSULTAR CAMADAS EXTRAS DO IBGE ---
def consultar_camadas_extras(lat, lon):
    """Bioma (geojson local) e Amazônia Legal (WFS do IBGE)."""
    base_url = "https://geoservicos.ibge.gov.br/geoserver/ows"
    headers = {"User-Agent": "Mozilla/5.0"}

    resultados = {
        "bioma": _bioma_no_ponto(lat, lon),
        "amazonia_legal": False
    }

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


# --- CONSULTA DAS BASES EM PARALELO, COM PRAZO ---
LIMITE_CONSULTAS_S = 45


def _consultar_bases(mun, lat, lon):
    """Roda as consultas externas do Contexto em paralelo, com prazo total.

    Rodando em sequência, os tempos se somavam: com o IBGE lento a partir do
    Streamlit Cloud, a aba passava de 2 minutos. Agora espera no máximo
    LIMITE_CONSULTAS_S; o que não responder vira "indisponível" e a página
    segue. O tempo de cada consulta vai para o log ([contexto] ...).
    """
    cod, uf, nome = mun['cod_ibge'], mun['uf'], mun['nome_puro']
    tarefas = {
        "ibge": (utils.get_ibge_context_by_code, (cod, nome, uf, lat, lon)),
        "altimetria": (utils.get_altimetria_municipio, (cod, lat, lon)),
        "rota": (utils.get_distancia_capital, (cod, uf, lat, lon)),
        "extras": (consultar_camadas_extras, (lat, lon)),
        "bacia": (utils.get_bacia_info, (lat, lon)),
        "armazens": (utils.get_armazens_proximos, (lat, lon, 100)),
    }
    esgotado = {"erro": f"Fonte não respondeu em {LIMITE_CONSULTAS_S}s."}
    padroes = {
        # Sem os dados do IBGE a aba ainda mostra o município já identificado.
        "ibge": {"municipio": nome, "uf": uf, "codigo_ibge": cod, "populacao": None,
                 "area_km2": None, "densidade": None,
                 "regiao_intermediaria": "---", "regiao_imediata": "---"},
        "extras": {"bioma": _bioma_no_ponto(lat, lon), "amazonia_legal": False},
    }

    ctx = get_script_run_ctx()

    def rodar(chave, fn, args):
        add_script_run_ctx(threading.current_thread(), ctx)  # p/ o st.cache_data
        t0 = time.time()
        try:
            return fn(*args)
        finally:
            print(f"[contexto] {chave}: {time.time() - t0:.1f}s", flush=True)

    ex = ThreadPoolExecutor(max_workers=len(tarefas))
    futuros = {k: ex.submit(rodar, k, fn, a) for k, (fn, a) in tarefas.items()}
    wait(futuros.values(), timeout=LIMITE_CONSULTAS_S)
    ex.shutdown(wait=False)  # não segura a página pelas que estouraram o prazo

    res = {}
    for k, f in futuros.items():
        if not f.done():
            print(f"[contexto] {k}: SEM RESPOSTA em {LIMITE_CONSULTAS_S}s", flush=True)
            res[k] = padroes.get(k, esgotado)
            continue
        try:
            res[k] = f.result()
        except Exception as e:
            res[k] = padroes.get(k, {"erro": str(e)})
    return res


# --- RENDERIZAÇÃO DA ABA ---
def render_tab():
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
            bases = _consultar_bases(mun_selecionado, lat_dec, lon_dec)
            dados_ibge = bases["ibge"]
            dados_altimetria = bases["altimetria"]
            dados_rota = bases["rota"]
            dados_extras = bases["extras"]
            dados_bacia = bases["bacia"]
            dados_arm = bases["armazens"]

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
                    st.markdown(f"**Região Hidrográfica:** {dados_bacia['regiao_hidrografica']}")
                    st.caption("Fonte: ANA/SNIRH - Regiões Hidrográficas")

            # --- ARMAZENAGEM ---
            st.write("")
            ui.secao("🏭 Armazenagem (raio de 100 km)")
            with st.container(border=True):
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