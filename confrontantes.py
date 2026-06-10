import streamlit as st
import geopandas as gpd
import pandas as pd
import requests
import folium
from streamlit_folium import st_folium
from shapely.geometry import shape
import ssl
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.poolmanager import PoolManager

# ==========================================
# 1. CONFIGURAÇÃO DE REDE
# ==========================================

class LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False):
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.poolmanager = PoolManager(
            num_pools=connections, maxsize=maxsize,
            block=block, ssl_context=ctx
        )

def get_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    adapter = LegacySSLAdapter(max_retries=retries)
    session.mount('https://', adapter)
    return session

# ==========================================
# 2. CSS LOCAL DA ABA
# ==========================================

CSS_CONFRONTANTES = """
<style>
/* Cards de métrica customizados */
.metric-card {
    background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
    border-radius: 12px;
    padding: 18px 20px;
    border-left: 4px solid #009e60;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    margin-bottom: 8px;
}
.metric-card.danger { border-left-color: #c0392b; }
.metric-card.warning { border-left-color: #d4a017; }
.metric-card.info { border-left-color: #2C3E50; }

.metric-label {
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #6c757d;
    margin: 0;
}
.metric-value {
    font-size: 2rem;
    font-weight: 700;
    color: #2C3E50;
    margin: 4px 0 0 0;
    line-height: 1;
}
.metric-card.danger .metric-value { color: #c0392b; }
.metric-card.warning .metric-value { color: #d4a017; }

/* Badge de classificação */
.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.4px;
}
.badge-danger {
    background-color: #fbe9e7;
    color: #c0392b;
    border: 1px solid #f5c2bb;
}
.badge-warning {
    background-color: #fff8e1;
    color: #b8860b;
    border: 1px solid #fde7a0;
}

/* Legenda do mapa */
.legenda-mapa {
    background: white;
    padding: 12px 16px;
    border-radius: 8px;
    border: 1px solid #e0e0e0;
    margin-bottom: 12px;
    display: flex;
    gap: 24px;
    align-items: center;
    font-size: 0.85rem;
}
.legenda-item {
    display: flex;
    align-items: center;
    gap: 8px;
}
.legenda-cor {
    width: 16px;
    height: 16px;
    border-radius: 3px;
    border: 2px solid;
}

/* Cabeçalho da aba */
.confront-header {
    background: linear-gradient(135deg, #2C3E50 0%, #1a252f 100%);
    border-radius: 14px;
    padding: 24px 28px;
    color: white;
    margin-bottom: 20px;
    box-shadow: 0 4px 12px rgba(44,62,80,0.15);
}
.confront-header h2 {
    color: white !important;
    margin: 0 0 6px 0 !important;
    font-size: 1.4rem !important;
}
.confront-header p {
    margin: 0;
    opacity: 0.85;
    font-size: 0.92rem;
}

/* Card de detalhes */
.detalhe-card {
    background: white;
    border-radius: 12px;
    padding: 20px 24px;
    border: 1px solid #e9ecef;
    box-shadow: 0 2px 8px rgba(0,0,0,0.03);
}
.detalhe-titulo {
    font-size: 1.1rem;
    font-weight: 700;
    color: #2C3E50;
    margin-bottom: 4px;
}
.detalhe-codigo {
    font-family: 'SF Mono', Monaco, 'Courier New', monospace;
    font-size: 0.85rem;
    color: #009e60;
    background: #e8f5e9;
    padding: 2px 8px;
    border-radius: 4px;
    display: inline-block;
}
.detalhe-linha {
    display: flex;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid #f0f0f0;
}
.detalhe-linha:last-child { border-bottom: none; }
.detalhe-chave {
    color: #6c757d;
    font-size: 0.88rem;
}
.detalhe-valor {
    color: #2C3E50;
    font-weight: 600;
    font-size: 0.88rem;
}
</style>
"""

# ==========================================
# 3. FUNÇÕES DE DADOS
# ==========================================

def detectar_ufs(gdf):
    """Detecta UFs que o bbox do imóvel toca, via WFS do IBGE."""
    try:
        bounds = gdf.total_bounds
        bbox = f"{bounds[0]},{bounds[1]},{bounds[2]},{bounds[3]}"

        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeName": "CGEO:Brasil_UF_2022", "outputFormat": "application/json",
            "srsName": "EPSG:4674",
            "BBOX": f"{bbox},EPSG:4674"
        }
        resp = requests.get(
            "https://geoservicos.ibge.gov.br/geoserver/ows",
            params=params, timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if resp.status_code == 200:
            data = resp.json()
            ufs = []
            for f in data.get("features", []):
                props = f["properties"]
                sigla = (props.get("SIGLA_UF") or props.get("sigla")
                         or props.get("uf") or props.get("SIGLA"))
                if sigla:
                    ufs.append(sigla.lower())
            if ufs:
                return list(set(ufs))
    except:
        pass

    # Fallback: nominatim
    try:
        centroid = gdf.to_crs("EPSG:4326").unary_union.centroid
        url = f"https://nominatim.openstreetmap.org/reverse?lat={centroid.y}&lon={centroid.x}&format=json"
        r = requests.get(url, headers={"User-Agent": "GEOCAPUTI/1.0"}, timeout=8)
        if r.status_code == 200:
            estado = r.json().get("address", {}).get("ISO3166-2-lvl4", "").replace("BR-", "").lower()
            if estado:
                return [estado]
    except:
        pass

    return []


def buscar_cars_por_bbox(gdf_imovel, ufs):
    """Busca CARs no SICAR pelo bounding box, com buffer de 500m de segurança."""
    base_url = "https://geoserver.car.gov.br/geoserver/sicar/wfs"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
    session = get_session()

    gdf_proj = gdf_imovel.to_crs(gdf_imovel.estimate_utm_crs())
    gdf_buffer = gdf_proj.buffer(500).to_crs("EPSG:4674")
    bounds = gdf_buffer.total_bounds
    bbox = f"{bounds[0]},{bounds[1]},{bounds[2]},{bounds[3]}"

    gdfs = []
    for uf in ufs:
        try:
            layer = f"sicar:sicar_imoveis_{uf}"
            params = {
                "service": "WFS", "version": "1.0.0", "request": "GetFeature",
                "typeName": layer, "outputFormat": "application/json",
                "BBOX": f"{bbox},EPSG:4674"
            }
            resp = session.get(base_url, params=params, headers=headers, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("features"):
                    gdf_uf = gpd.GeoDataFrame.from_features(data["features"], crs="EPSG:4674")
                    gdf_uf["_uf_fonte"] = uf.upper()
                    gdfs.append(gdf_uf)
        except:
            continue

    if not gdfs:
        return gpd.GeoDataFrame()

    return pd.concat(gdfs, ignore_index=True)


def classificar_imoveis(gdf_cars, gdf_imovel):
    """Sobreposição = intersecta interior. Confrontante = toca borda."""
    if gdf_cars.empty:
        return gdf_cars

    if gdf_cars.crs != gdf_imovel.crs:
        gdf_cars = gdf_cars.to_crs(gdf_imovel.crs)

    perimetro_real = gdf_imovel.unary_union
    gdf_proj = gdf_imovel.to_crs(gdf_imovel.estimate_utm_crs())
    buffer_toque = gdf_proj.buffer(1).to_crs(gdf_imovel.crs).unary_union

    classificacoes = []
    for _, row in gdf_cars.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            classificacoes.append("Outro")
            continue

        if geom.intersects(perimetro_real):
            inter = geom.intersection(perimetro_real)
            if not inter.is_empty and inter.area > 1e-10:
                classificacoes.append("Sobreposição")
            else:
                classificacoes.append("Confrontante")
        elif geom.intersects(buffer_toque):
            classificacoes.append("Confrontante")
        else:
            classificacoes.append("Outro")

    gdf_cars["_classificacao"] = classificacoes
    gdf_resultado = gdf_cars[gdf_cars["_classificacao"].isin(["Sobreposição", "Confrontante"])].copy()
    return gdf_resultado.reset_index(drop=True)


def calcular_area_sobreposicao_ha(geom_car, geom_imovel, utm_crs):
    """Área de sobreposição em hectares, projetando para UTM."""
    try:
        inter = geom_car.intersection(geom_imovel)
        if inter.is_empty:
            return 0.0
        gdf_inter = gpd.GeoDataFrame({'geometry': [inter]}, crs="EPSG:4674")
        gdf_proj = gdf_inter.to_crs(utm_crs)
        return round(gdf_proj.geometry.area.iloc[0] / 10000, 4)
    except:
        return 0.0


# --- Extratores robustos ---
def extrair_codigo_car(row):
    for col in ['cod_imovel', 'codigo_imovel', 'cod_car', 'codimovel', 'COD_IMOVEL']:
        val = row.get(col)
        if val and str(val).strip() not in ['nan', 'None', '']:
            return str(val).strip()
    return "—"

def extrair_municipio(row):
    for col in ['nom_municipio', 'municipio', 'cidade', 'nome_municipio', 'NOM_MUNICIPIO']:
        val = row.get(col)
        if val and str(val).strip() not in ['nan', 'None', '']:
            return str(val).strip()
    return "—"

def extrair_status(row):
    for col in ['ind_status_imovel', 'situacao', 'status', 'des_condicao_aguardando_analise']:
        val = row.get(col)
        if val and str(val).strip() not in ['nan', 'None', '']:
            return str(val).strip()
    return "—"

def extrair_area(row):
    for col in ['num_area_imovel', 'val_area_imovel', 'area_imovel', 'area_ha', 'nu_area_imovel']:
        val = row.get(col)
        if val and str(val).strip() not in ['nan', 'None', '']:
            try:
                return round(float(str(val).replace(',', '.')), 2)
            except:
                continue
    return None

# ==========================================
# 4. RENDERIZAÇÃO
# ==========================================

def render_tab():
    st.markdown(CSS_CONFRONTANTES, unsafe_allow_html=True)

    # --- Cabeçalho da aba ---
    st.markdown("""
        <div class="confront-header">
            <h2>🗺️ Confrontantes e Sobreposições</h2>
            <p>Identifica imóveis cadastrados no SICAR que tocam ou se sobrepõem ao perímetro do imóvel analisado.</p>
        </div>
    """, unsafe_allow_html=True)

    # --- Verifica imóvel carregado ---
    gdf_imovel = st.session_state.get('gdf_imovel')
    if gdf_imovel is None or not isinstance(gdf_imovel, gpd.GeoDataFrame) or gdf_imovel.empty:
        st.warning("⚠️ Nenhum imóvel carregado. Vá para a aba **Início**, faça o upload e clique em 'Usar Este Perímetro'.")
        return

    codigo_display = st.session_state.get('last_code', 'Imóvel Carregado')

    # --- Painel de ação ---
    col_info, col_btn = st.columns([2.2, 1], vertical_alignment="center")
    with col_info:
        st.markdown(f"""
            <div style="background:#f8f9fa;padding:12px 16px;border-radius:8px;border:1px solid #e9ecef;">
                <div style="font-size:0.78rem;color:#6c757d;text-transform:uppercase;letter-spacing:0.5px;font-weight:600;">
                    Imóvel em Análise
                </div>
                <div style="font-size:0.95rem;color:#2C3E50;font-weight:600;margin-top:4px;">
                    {codigo_display}
                </div>
            </div>
        """, unsafe_allow_html=True)
    with col_btn:
        rodar = st.button("🔍 Buscar Confrontantes", type="primary", use_container_width=True)

    if rodar:
        st.session_state['confrontantes_resultado'] = None
        st.session_state['confrontantes_done'] = False

        with st.spinner("Detectando UFs do imóvel..."):
            gdf_wgs = gdf_imovel.to_crs("EPSG:4674") if gdf_imovel.crs.to_epsg() != 4674 else gdf_imovel.copy()
            ufs = detectar_ufs(gdf_wgs)

        if not ufs:
            st.error("Não foi possível identificar a UF do imóvel. Verifique o KML carregado.")
            return

        st.caption(f"UFs detectadas: **{', '.join([u.upper() for u in ufs])}**")

        with st.spinner(f"Consultando SICAR ({', '.join([u.upper() for u in ufs])})..."):
            gdf_cars = buscar_cars_por_bbox(gdf_wgs, ufs)

        if gdf_cars.empty:
            st.warning("Nenhum imóvel CAR encontrado na área. O SICAR pode estar indisponível ou a área não possui registros.")
            return

        with st.spinner(f"Classificando {len(gdf_cars)} imóveis encontrados..."):
            gdf_resultado = classificar_imoveis(gdf_cars, gdf_wgs)

        if gdf_resultado.empty:
            st.success("Nenhum confrontante ou sobreposição encontrado na área consultada.")
            return

        # Calcula área de sobreposição
        perimetro_real = gdf_wgs.unary_union
        utm_crs = gdf_wgs.estimate_utm_crs()
        areas_sob = []
        for _, row in gdf_resultado.iterrows():
            if row["_classificacao"] == "Sobreposição":
                areas_sob.append(calcular_area_sobreposicao_ha(row.geometry, perimetro_real, utm_crs))
            else:
                areas_sob.append(0.0)
        gdf_resultado["_area_sobreposicao_ha"] = areas_sob

        st.session_state['confrontantes_resultado'] = gdf_resultado
        st.session_state['confrontantes_imovel_wgs'] = gdf_wgs
        st.session_state['confrontantes_done'] = True
        st.rerun()

    # --- Resultados ---
    if not (st.session_state.get('confrontantes_done') and st.session_state.get('confrontantes_resultado') is not None):
        st.info("👆 Clique em **Buscar Confrontantes** para iniciar a análise.")
        return

    gdf_res = st.session_state['confrontantes_resultado']
    gdf_wgs = st.session_state['confrontantes_imovel_wgs']

    n_sob = len(gdf_res[gdf_res["_classificacao"] == "Sobreposição"])
    n_conf = len(gdf_res[gdf_res["_classificacao"] == "Confrontante"])
    area_total_sob = gdf_res["_area_sobreposicao_ha"].sum()

    st.markdown("---")

    # --- Cards de métricas ---
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"""
            <div class="metric-card info">
                <p class="metric-label">Total Encontrados</p>
                <p class="metric-value">{len(gdf_res)}</p>
            </div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
            <div class="metric-card danger">
                <p class="metric-label">Sobreposições</p>
                <p class="metric-value">{n_sob}</p>
            </div>
        """, unsafe_allow_html=True)
    with c3:
        st.markdown(f"""
            <div class="metric-card warning">
                <p class="metric-label">Confrontantes</p>
                <p class="metric-value">{n_conf}</p>
            </div>
        """, unsafe_allow_html=True)
    with c4:
        area_fmt = f"{area_total_sob:.2f}".replace(".", ",") if area_total_sob > 0 else "0,00"
        st.markdown(f"""
            <div class="metric-card danger">
                <p class="metric-label">Área Sobreposta (ha)</p>
                <p class="metric-value">{area_fmt}</p>
            </div>
        """, unsafe_allow_html=True)

    st.write("")

    # --- Legenda ---
    st.markdown("""
        <div class="legenda-mapa">
            <div class="legenda-item">
                <div class="legenda-cor" style="background:#00FFFF33;border-color:#00FFFF;"></div>
                <span><b>Imóvel Analisado</b> (KML)</span>
            </div>
            <div class="legenda-item">
                <div class="legenda-cor" style="background:#c0392b66;border-color:#c0392b;"></div>
                <span><b>Sobreposição</b> — invade o perímetro</span>
            </div>
            <div class="legenda-item">
                <div class="legenda-cor" style="background:#d4a01766;border-color:#d4a017;"></div>
                <span><b>Confrontante</b> — toca a borda</span>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # --- Layout: Mapa + Tabela ---
    col_mapa, col_tabela = st.columns([1.7, 1], gap="medium")

    with col_mapa:
        centro = [gdf_wgs.unary_union.centroid.y, gdf_wgs.unary_union.centroid.x]
        m = folium.Map(location=centro, zoom_start=13, tiles=None)

        folium.TileLayer(
            tiles="Esri World Imagery",
            name="Satélite",
            attr="Esri"
        ).add_to(m)
        folium.TileLayer("OpenStreetMap", name="Mapa").add_to(m)

        # Imóvel principal
        folium.GeoJson(
            gdf_wgs,
            name="Imóvel (KML)",
            style_function=lambda x: {
                'color': '#00FFFF', 'weight': 3,
                'fillColor': '#00FFFF', 'fillOpacity': 0.08
            },
            tooltip=folium.Tooltip(f"<b>Imóvel:</b> {codigo_display}")
        ).add_to(m)

        # CARs classificados
        for _, row in gdf_res.iterrows():
            classificacao = row["_classificacao"]
            if classificacao == "Sobreposição":
                cor = "#c0392b"
                peso = 2.5
                fill_opacity = 0.4
            else:
                cor = "#d4a017"
                peso = 2
                fill_opacity = 0.2

            cod = extrair_codigo_car(row)
            mun = extrair_municipio(row)
            status = extrair_status(row)
            area = extrair_area(row)
            area_str = f"{area:.2f} ha" if area else "—"
            area_sob = row.get("_area_sobreposicao_ha", 0)
            sob_str = f"<br><b>Área Sobreposta:</b> {area_sob:.4f} ha" if classificacao == "Sobreposição" else ""

            tooltip_html = f"""
                <div style="font-family:sans-serif;font-size:12px;line-height:1.5;min-width:220px;">
                    <div style="font-weight:700;color:{cor};margin-bottom:4px;text-transform:uppercase;">{classificacao}</div>
                    <b>Código:</b> <span style="font-family:monospace;">{cod}</span><br>
                    <b>Município:</b> {mun}<br>
                    <b>Área:</b> {area_str}<br>
                    <b>Status:</b> {status}
                    {sob_str}
                </div>
            """

            try:
                folium.GeoJson(
                    row.geometry.__geo_interface__,
                    style_function=lambda x, c=cor, p=peso, fo=fill_opacity: {
                        'color': c, 'weight': p,
                        'fillColor': c, 'fillOpacity': fo
                    },
                    tooltip=folium.Tooltip(tooltip_html)
                ).add_to(m)
            except:
                continue

        folium.LayerControl(collapsed=True).add_to(m)
        st_folium(m, height=540, use_container_width=True, key="mapa_confrontantes")

    with col_tabela:
        st.markdown("##### Registros Encontrados")
        st.caption("Selecione uma linha para ver detalhes abaixo")

        linhas = []
        for idx, row in gdf_res.iterrows():
            cod = extrair_codigo_car(row)
            mun = extrair_municipio(row)
            area = extrair_area(row)
            classificacao = row["_classificacao"]
            area_sob = row.get("_area_sobreposicao_ha", 0)

            icone = "🔴" if classificacao == "Sobreposição" else "🟡"

            linhas.append({
                "_idx": idx,
                "Tipo": f"{icone} {classificacao}",
                "Código CAR": cod[:30] + "..." if len(cod) > 30 else cod,
                "Município": mun,
                "Área (ha)": area if area else "—",
                "Sob. (ha)": round(area_sob, 4) if classificacao == "Sobreposição" else "—"
            })

        df_display = pd.DataFrame(linhas)
        # Ordena: Sobreposição primeiro
        df_display["_ordem"] = df_display["Tipo"].apply(lambda x: 0 if "🔴" in x else 1)
        df_display = df_display.sort_values("_ordem").drop(columns=["_ordem", "_idx"]).reset_index(drop=True)

        event = st.dataframe(
            df_display,
            use_container_width=True,
            selection_mode="single-row",
            on_select="rerun",
            height=500,
            hide_index=True,
            key="tabela_confrontantes"
        )

    # --- Detalhe do registro selecionado ---
    if len(event.selection.rows) > 0:
        idx_sel = event.selection.rows[0]

        gdf_ordenado = gdf_res.copy()
        gdf_ordenado["_ordem"] = gdf_ordenado["_classificacao"].apply(
            lambda x: 0 if x == "Sobreposição" else 1
        )
        gdf_ordenado = gdf_ordenado.sort_values("_ordem").reset_index(drop=True)

        if idx_sel < len(gdf_ordenado):
            row_sel = gdf_ordenado.iloc[idx_sel]

            st.markdown("---")
            st.markdown("##### 📋 Detalhes do Registro")

            cod = extrair_codigo_car(row_sel)
            mun = extrair_municipio(row_sel)
            status = extrair_status(row_sel)
            area = extrair_area(row_sel)
            classificacao = row_sel["_classificacao"]
            area_sob = row_sel.get("_area_sobreposicao_ha", 0)

            badge_class = "badge-danger" if classificacao == "Sobreposição" else "badge-warning"

            col_det_info, col_det_mapa = st.columns([1, 1.3], gap="medium")

            with col_det_info:
                area_fmt = f"{area:.2f} ha" if area else "—"
                sob_html = ""
                if classificacao == "Sobreposição":
                    sob_html = f"""
                        <div class="detalhe-linha">
                            <span class="detalhe-chave">Área Sobreposta</span>
                            <span class="detalhe-valor" style="color:#c0392b;">{area_sob:.4f} ha</span>
                        </div>
                    """

                uf_fonte = row_sel.get('_uf_fonte', '—')

                st.markdown(f"""
                    <div class="detalhe-card">
                        <span class="badge {badge_class}">{classificacao}</span>
                        <div class="detalhe-titulo" style="margin-top:10px;">{mun}</div>
                        <div style="margin:8px 0 14px 0;">
                            <span class="detalhe-codigo">{cod}</span>
                        </div>
                        <div class="detalhe-linha">
                            <span class="detalhe-chave">UF</span>
                            <span class="detalhe-valor">{uf_fonte}</span>
                        </div>
                        <div class="detalhe-linha">
                            <span class="detalhe-chave">Área Declarada</span>
                            <span class="detalhe-valor">{area_fmt}</span>
                        </div>
                        <div class="detalhe-linha">
                            <span class="detalhe-chave">Status</span>
                            <span class="detalhe-valor">{status}</span>
                        </div>
                        {sob_html}
                    </div>
                """, unsafe_allow_html=True)

                with st.expander("Ver todos os atributos disponíveis"):
                    attrs = {
                        k: v for k, v in row_sel.items()
                        if k not in ['geometry', '_classificacao', '_area_sobreposicao_ha', '_uf_fonte', '_ordem']
                        and str(v).strip() not in ['nan', 'None', 'NaT', '']
                    }
                    if attrs:
                        df_attrs = pd.DataFrame(
                            [{"Atributo": k, "Valor": str(v)} for k, v in attrs.items()]
                        )
                        st.dataframe(df_attrs, use_container_width=True, hide_index=True)
                    else:
                        st.caption("Nenhum atributo adicional disponível.")

            with col_det_mapa:
                try:
                    centro_sel = [row_sel.geometry.centroid.y, row_sel.geometry.centroid.x]
                    m2 = folium.Map(location=centro_sel, zoom_start=15, tiles="Esri World Imagery")

                    folium.GeoJson(
                        gdf_wgs,
                        style_function=lambda x: {
                            'color': '#00FFFF', 'weight': 2,
                            'fillColor': '#00FFFF', 'fillOpacity': 0.1
                        }
                    ).add_to(m2)

                    cor_sel = "#c0392b" if classificacao == "Sobreposição" else "#d4a017"
                    folium.GeoJson(
                        row_sel.geometry.__geo_interface__,
                        style_function=lambda x, c=cor_sel: {
                            'color': c, 'weight': 3,
                            'fillColor': c, 'fillOpacity': 0.45
                        },
                        tooltip=folium.Tooltip(f"<b>{cod}</b>")
                    ).add_to(m2)

                    st_folium(m2, height=380, use_container_width=True, key="mapa_detalhe_confrontante")
                except:
                    st.caption("Não foi possível renderizar o mapa.")
