import streamlit as st
import ee
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import io
import utils
import ui

# ==========================================
# 0. CONFIGURAÇÕES E UTILITÁRIOS
# ==========================================

MESES_PT = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"
}

PERIODO_CLIMATOLOGIA = "1981-2025"


def to_excel_horizontal(df, index_col="Mês"):
    output = io.BytesIO()

    if "Mês_Num" in df.columns:
        df = df.drop(columns=["Mês_Num"])

    df_t = df.set_index(index_col).T

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df_t.to_excel(writer, sheet_name="Dados")
        worksheet = writer.sheets["Dados"]
        worksheet.set_column(0, 12, 15)

    return output.getvalue()


def formatar_decimal_br(valor, casas=1):
    """Formata número decimal no padrão brasileiro."""
    try:
        return f"{float(valor):.{casas}f}".replace(".", ",")
    except Exception:
        return "--"


def formatar_inteiro_br(valor):
    """Formata número inteiro no padrão brasileiro."""
    try:
        return f"{float(valor):,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "--"


# ==========================================
# 1. FUNÇÕES DE DADOS CLIMÁTICOS
# ==========================================


def get_worldclim_data(geometry_gee):
    """Calcula climatologia mensal de temperatura com ERA5/ECMWF."""
    try:
        geo_simple = geometry_gee.simplify(maxError=100)

        dataset = (
            ee.ImageCollection("ECMWF/ERA5/MONTHLY")
            .filterDate("1981-01-01", "2025-12-31")
            .select([
                "mean_2m_air_temperature",
                "maximum_2m_air_temperature",
                "minimum_2m_air_temperature"
            ])
        )

        def calc_monthly_temp(m):
            m = ee.Number(m)
            mean_month = dataset.filter(ee.Filter.calendarRange(m, m, "month")).mean()

            stats = mean_month.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=geo_simple,
                scale=11000,
                maxPixels=1e13,
                bestEffort=True,
                tileScale=16
            )

            return ee.Feature(None, {
                "month": m,
                "avg": ee.Number(stats.get("mean_2m_air_temperature")).subtract(273.15),
                "max": ee.Number(stats.get("maximum_2m_air_temperature")).subtract(273.15),
                "min": ee.Number(stats.get("minimum_2m_air_temperature")).subtract(273.15)
            })

        months = ee.List.sequence(1, 12)
        features = ee.FeatureCollection(months.map(calc_monthly_temp)).getInfo()["features"]

        data = []
        for f in features:
            p = f["properties"]
            m_num = int(p["month"])

            if p.get("avg") is not None:
                data.append({
                    "Mês_Num": m_num,
                    "Mês": MESES_PT[m_num],
                    "Média (°C)": round(float(p["avg"]), 2),
                    "Mínima (°C)": round(float(p["min"]), 2),
                    "Máxima (°C)": round(float(p["max"]), 2)
                })

        return pd.DataFrame(data).sort_values("Mês_Num")

    except Exception as e:
        st.session_state["erro_clima_temp"] = str(e)
        return pd.DataFrame()


def get_chirps_data(geometry_gee):
    """Calcula climatologia mensal de precipitação com CHIRPS Pentad."""
    try:
        geo_simple = geometry_gee.simplify(maxError=100)

        dataset = (
            ee.ImageCollection("UCSB-CHG/CHIRPS/PENTAD")
            .filterDate("1981-01-01", "2025-12-31")
            .select("precipitation")
        )

        def calc_monthly_climatology(m):
            m = ee.Number(m)
            mean_pentad = dataset.filter(ee.Filter.calendarRange(m, m, "month")).mean()

            val = mean_pentad.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=geo_simple,
                scale=5500,
                maxPixels=1e13,
                bestEffort=True,
                tileScale=16
            ).get("precipitation")

            monthly_total = ee.Number(val).multiply(6)

            return ee.Feature(None, {
                "month": m,
                "rain": monthly_total
            })

        months = ee.List.sequence(1, 12)
        features = ee.FeatureCollection(months.map(calc_monthly_climatology)).getInfo()["features"]

        data = []
        for f in features:
            p = f["properties"]
            m_num = int(p["month"])

            if p.get("rain") is not None:
                data.append({
                    "Mês_Num": m_num,
                    "Mês": MESES_PT[m_num],
                    "Chuva (mm)": round(float(p["rain"]), 2)
                })

        return pd.DataFrame(data).sort_values("Mês_Num")

    except Exception as e:
        st.session_state["erro_clima_rain"] = str(e)
        return pd.DataFrame()


# ==========================================
# 2. TEXTO AUTOMÁTICO PARA LAUDO
# ==========================================


def gerar_resumo_climatologico(area_label, sigla, desc, df_temp, df_rain):
    """Gera parágrafo técnico no padrão de laudo."""
    if df_temp is None or df_temp.empty or df_rain is None or df_rain.empty:
        return (
            "Gere a climatologia completa para visualizar o resumo climatológico "
            "no padrão de laudo técnico."
        )

    med = df_temp["Média (°C)"].mean()
    mini = df_temp["Mínima (°C)"].mean()
    maxi = df_temp["Máxima (°C)"].mean()
    total_anual = df_rain["Chuva (mm)"].sum()

    med_txt = formatar_decimal_br(med, 1)
    mini_txt = formatar_decimal_br(mini, 1)
    maxi_txt = formatar_decimal_br(maxi, 1)
    total_txt = formatar_inteiro_br(total_anual)

    sigla_txt = sigla if sigla else "N/A"
    desc_txt = desc if desc else "sem descrição disponível"

    return (
        f"O clima da região de {area_label} é classificado como {sigla_txt} — {desc_txt}, "
        f"segundo a classificação climática de Köppen-Geiger. "
        f"A temperatura média anual estimada é de {med_txt} °C, "
        f"com média das mínimas de {mini_txt} °C e média das máximas de {maxi_txt} °C. "
        f"A pluviosidade média anual é de aproximadamente {total_txt} mm. "
        f"Os dados de temperatura foram obtidos da base ERA5/ECMWF, "
        f"enquanto os dados de precipitação foram extraídos da base CHIRPS."
    )


# ==========================================
# 3. GRÁFICOS
# ==========================================


def plotar_temperatura(df):
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=df["Mês"],
        y=df["Máxima (°C)"],
        mode="lines",
        line=dict(width=0),
        showlegend=False,
        hoverinfo="skip"
    ))

    fig.add_trace(go.Scatter(
        x=df["Mês"],
        y=df["Mínima (°C)"],
        mode="lines",
        line=dict(width=0),
        fill="tonexty",
        fillcolor="rgba(230, 126, 34, 0.2)",
        showlegend=False,
        hoverinfo="skip"
    ))

    fig.add_trace(go.Scatter(
        x=df["Mês"],
        y=df["Máxima (°C)"],
        mode="lines+markers",
        name="Máxima",
        line=dict(color="#e74c3c", width=1, dash="dot"),
        hovertemplate="%{y:.2f} °C"
    ))

    fig.add_trace(go.Scatter(
        x=df["Mês"],
        y=df["Mínima (°C)"],
        mode="lines+markers",
        name="Mínima",
        line=dict(color="#3498db", width=1, dash="dot"),
        hovertemplate="%{y:.2f} °C"
    ))

    fig.add_trace(go.Scatter(
        x=df["Mês"],
        y=df["Média (°C)"],
        mode="lines+markers",
        name="Média",
        line=dict(color="#e67e22", width=3),
        hovertemplate="%{y:.2f} °C"
    ))

    fig.update_layout(
        height=355,
        margin=dict(l=20, r=20, t=20, b=20),
        yaxis_title="Temperatura (°C)",
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )

    return fig


def plotar_precipitacao(df):
    fig = px.bar(
        df,
        x="Mês",
        y="Chuva (mm)",
        text_auto=".2f",
        color="Chuva (mm)",
        color_continuous_scale="Blues"
    )

    fig.update_traces(
        textangle=0,
        textposition="outside",
        cliponaxis=False
    )

    fig.update_layout(
        height=355,
        margin=dict(l=20, r=20, t=20, b=20),
        yaxis_title="Precipitação (mm)",
        coloraxis_showscale=False
    )

    return fig


# ==========================================
# 4. RENDERIZAÇÃO DA PÁGINA
# ==========================================


def render_tab():
    geometry = st.session_state.get("current_geometry")
    source_name = st.session_state.get("source_name", "Desconhecido")

    if not geometry:
        ui.vazio()
        return

    ui.barra_imovel(nome=source_name)

    centroide = geometry.centroid(1).coordinates().getInfo()
    lon_dec, lat_dec = centroide[0], centroide[1]

    identificador_imovel = f"{source_name}_{lat_dec:.5f}_{lon_dec:.5f}"

    if st.session_state.get("last_clim_source") != identificador_imovel:
        chaves_para_deletar = [
            k for k in st.session_state.keys()
            if k.startswith("clim_") or k.startswith("erro_clima")
        ]

        for k in chaves_para_deletar:
            del st.session_state[k]

        st.session_state["last_clim_source"] = identificador_imovel

    # ----------------------------------------------------------
    # Classificação Köppen-Geiger
    # ----------------------------------------------------------
    sigla = "N/A"
    desc = "Sem descrição"

    dados_koppen = utils.get_koppen_class(lat_dec, lon_dec)
    if dados_koppen and "erro" not in dados_koppen:
        sigla = dados_koppen.get("Classificacao", "N/A")
        desc = dados_koppen.get("Descricao", "Sem descrição")
        st.success(
            f"**Classificação Climática (Köppen-Geiger):** {sigla} — {desc}",
            icon="🌡️"
        )

    # ----------------------------------------------------------
    # Área de análise: município (com seleção quando há vários)
    # ----------------------------------------------------------
    with st.spinner("Mapeando limites climáticos do município..."):
        dados_muns = utils.obter_municipios_interseccao(geometry, identificador_imovel)

    mun_validos = [m for m in dados_muns if "erro" not in m] if dados_muns else []

    target_geometry = None
    area_label = None
    cod_sel = None
    metodo = None

    if mun_validos:
        if len(mun_validos) >= 2:
            # Imóvel cruza mais de um município: deixa o usuário escolher a base
            # (mesma lógica da aba Contexto). Default = maior interseção.
            st.warning(
                f"O imóvel intercepta **{len(mun_validos)} municípios**. "
                "Selecione a base climatológica:"
            )
            nomes = [m["municipio"] for m in mun_validos]
            escolha = st.selectbox(
                "Município base para a climatologia:",
                nomes,
                key="clima_mun_escolha"
            )
            mun_sel = next(m for m in mun_validos if m["municipio"] == escolha)
            metodo = "seleção do usuário (MapBiomas municípios-2020)"
        else:
            mun_sel = mun_validos[0]
            metodo = "interseção do perímetro (MapBiomas municípios-2020)"

        cod_sel = mun_sel.get("cod_ibge")
        geojson_mun = mun_sel.get("geojson")
        if geojson_mun is not None:
            target_geometry = ee.Geometry(geojson_mun)
            area_label = mun_sel["municipio"]
        elif len(mun_validos) >= 2:
            st.info(
                f"Não foi possível carregar o limite de **{mun_sel['municipio']}**; "
                "usando o município de maior interseção como base."
            )

    # Fallback robusto: lista indisponível ou geometria do município não carregou.
    if target_geometry is None:
        mun_dados = utils.get_limites_municipio_clima(
            lon_dec,
            lat_dec,
            _geometry_gee=geometry,
            cache_id=identificador_imovel
        )

        if not mun_dados or "erro" in mun_dados:
            erro_real = mun_dados.get("erro", "sem retorno") if mun_dados else "sem retorno"
            st.error(
                "Não foi possível localizar o município para a análise climatológica. "
                "A tentativa foi feita por interseção do perímetro, centroide e WFS do IBGE. "
                "Verifique se o perímetro do imóvel foi carregado corretamente.\n\n"
                f"**Detalhe técnico:** `{erro_real}`"
            )
            return

        target_geometry = ee.Geometry(mun_dados["geojson"])
        area_label = f"{mun_dados['nome']} - {mun_dados['uf']}".strip(" -")
        cod_sel = mun_dados.get("cod_ibge") or "default"
        metodo = mun_dados.get("metodo")

    # A climatologia é guardada por município, para que trocar a seleção mostre
    # o dado correto (ou peça para gerar) em vez de um cache de outro município.
    escopo_id = f"mun_{cod_sel}"
    key_temp = f"clim_temp_{escopo_id}"
    key_rain = f"clim_rain_{escopo_id}"

    st.info(f"Área de análise climatológica: **{area_label}**")
    if metodo:
        st.caption(f"Município localizado por: {metodo}")

    # ----------------------------------------------------------
    # Botão único de processamento
    # ----------------------------------------------------------
    col_btn, col_hint = st.columns([0.32, 0.68], vertical_alignment="center")

    with col_btn:
        gerar = st.button(
            "🚀 Gerar Climatologia",
            type="primary",
            use_container_width=True
        )

    with col_hint:
        st.caption(
            f"Processamento municipal com ERA5/ECMWF e CHIRPS | Série climatológica: {PERIODO_CLIMATOLOGIA}"
        )

    if gerar:
        st.session_state.pop(key_temp, None)
        st.session_state.pop(key_rain, None)
        st.session_state.pop("erro_clima_temp", None)
        st.session_state.pop("erro_clima_rain", None)

        with st.spinner("Processando temperatura e precipitação para o município..."):
            df_temp = get_worldclim_data(target_geometry)
            df_rain = get_chirps_data(target_geometry)

            if not df_temp.empty:
                st.session_state[key_temp] = df_temp
            else:
                msg = st.session_state.get("erro_clima_temp", "Erro desconhecido.")
                st.error(f"Erro ao processar temperatura: {msg}")

            if not df_rain.empty:
                st.session_state[key_rain] = df_rain
            else:
                msg = st.session_state.get("erro_clima_rain", "Erro desconhecido.")
                st.error(f"Erro ao processar precipitação: {msg}")

        if key_temp in st.session_state or key_rain in st.session_state:
            st.rerun()

    st.write("")

    df_temp_resumo = st.session_state.get(key_temp)
    df_rain_resumo = st.session_state.get(key_rain)

    col_temp, col_rain = st.columns(2, gap="medium")

    # ----------------------------------------------------------
    # Coluna 1: temperatura
    # ----------------------------------------------------------
    with col_temp:
        with st.container(border=True, height=585):
            ui.secao("🌡️ Temperatura")
            st.markdown("**Médias históricas mensais — ERA5/ECMWF**")

            if df_temp_resumo is not None and not df_temp_resumo.empty:
                med = df_temp_resumo["Média (°C)"].mean()
                mini = df_temp_resumo["Mínima (°C)"].mean()
                maxi = df_temp_resumo["Máxima (°C)"].mean()

                c1, c2, c3 = st.columns(3)
                c1.metric("Média Anual", f"{formatar_decimal_br(med, 1)} °C")
                c2.metric("Mínima Média", f"{formatar_decimal_br(mini, 1)} °C")
                c3.metric("Máxima Média", f"{formatar_decimal_br(maxi, 1)} °C")

                fig_temp = plotar_temperatura(df_temp_resumo)
                st.plotly_chart(fig_temp, use_container_width=True)

                st.caption(f"Fonte: ERA5/ECMWF ({PERIODO_CLIMATOLOGIA}) | Escopo: Município")

                excel_temp = to_excel_horizontal(df_temp_resumo)
                st.download_button(
                    label="📥 Baixar Dados de Temperatura",
                    data=excel_temp,
                    file_name="temperatura_municipio.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key=f"dl_temp_{escopo_id}"
                )
            else:
                st.info("Clique em **Gerar Climatologia** para calcular a temperatura municipal.")

    # ----------------------------------------------------------
    # Coluna 2: precipitação
    # ----------------------------------------------------------
    with col_rain:
        with st.container(border=True, height=585):
            ui.secao("☔ Precipitação")
            st.markdown("**Médias históricas mensais — CHIRPS 0,05°**")

            if df_rain_resumo is not None and not df_rain_resumo.empty:
                total_anual = df_rain_resumo["Chuva (mm)"].sum()

                st.metric(
                    "Pluviosidade Média Anual",
                    f"{formatar_inteiro_br(total_anual)} mm"
                )

                fig_rain = plotar_precipitacao(df_rain_resumo)
                st.plotly_chart(fig_rain, use_container_width=True)

                st.caption(f"Fonte: CHIRPS ({PERIODO_CLIMATOLOGIA}) | Escopo: Município")

                excel_rain = to_excel_horizontal(df_rain_resumo)
                st.download_button(
                    label="📥 Baixar Dados de Precipitação",
                    data=excel_rain,
                    file_name="precipitacao_municipio.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key=f"dl_rain_{escopo_id}"
                )
            else:
                st.info("Clique em **Gerar Climatologia** para calcular a precipitação municipal.")

    # ----------------------------------------------------------
    # Resumo para laudo técnico
    # ----------------------------------------------------------
    st.divider()
    ui.secao("📝 Resumo Climatológico")

    texto_laudo = gerar_resumo_climatologico(
        area_label=area_label,
        sigla=sigla,
        desc=desc,
        df_temp=df_temp_resumo,
        df_rain=df_rain_resumo
    )

    st.info(texto_laudo)
