import streamlit as st
import ee
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import io 
import utils

# ==========================================
# 0. CONFIGURAÇÕES E UTILITÁRIOS
# ==========================================

MESES_PT = {
    1: 'Jan', 2: 'Fev', 3: 'Mar', 4: 'Abr', 5: 'Mai', 6: 'Jun',
    7: 'Jul', 8: 'Ago', 9: 'Set', 10: 'Out', 11: 'Nov', 12: 'Dez'
}

def to_excel_horizontal(df, index_col='Mês'):
    output = io.BytesIO()
    if 'Mês_Num' in df.columns:
        df = df.drop(columns=['Mês_Num'])
    
    df_t = df.set_index(index_col).T 
    
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_t.to_excel(writer, sheet_name='Dados')
        worksheet = writer.sheets['Dados']
        worksheet.set_column(0, 12, 15) 
    
    return output.getvalue()

# ==========================================
# 1. FUNÇÕES DE DADOS CLIMÁTICOS
# ==========================================

@st.cache_data(show_spinner=False)
def get_worldclim_data(_geometry, cache_id):
    try:
        geo_simple = _geometry.simplify(maxError=100)
        wc = ee.ImageCollection("WORLDCLIM/V1/MONTHLY")
        
        def get_stats(img):
            month = img.get('month')
            stats = img.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=geo_simple,
                scale=2000, 
                maxPixels=1e9,
                bestEffort=True,
                tileScale=16
            )
            return ee.Feature(None, {
                'month': month,
                'avg': ee.Number(stats.get('tavg')).divide(10),
                'min': ee.Number(stats.get('tmin')).divide(10),
                'max': ee.Number(stats.get('tmax')).divide(10)
            })

        features = wc.map(get_stats).getInfo()['features']
        
        data = []
        for f in features:
            p = f['properties']
            m_num = int(p['month'])
            if p.get('avg') is not None:
                data.append({
                    "Mês_Num": m_num,
                    "Mês": MESES_PT[m_num], 
                    "Média (°C)": float(p['avg']),
                    "Mínima (°C)": float(p['min']),
                    "Máxima (°C)": float(p['max'])
                })
        
        return pd.DataFrame(data).sort_values('Mês_Num')
        
    except Exception as e:
        st.session_state['erro_clima_temp'] = str(e)
        return pd.DataFrame()

@st.cache_data(show_spinner=False)
def get_chirps_data(_geometry, cache_id):
    try:
        geo_simple = _geometry.simplify(maxError=100)
        dataset = ee.ImageCollection("UCSB-CHG/CHIRPS/PENTAD")\
            .filterDate('2000-01-01', '2025-12-31')\
            .select('precipitation')

        def calc_monthly_climatology(m):
            m = ee.Number(m)
            mean_pentad = dataset.filter(ee.Filter.calendarRange(m, m, 'month')).mean()
            val = mean_pentad.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=geo_simple,
                scale=5500,
                maxPixels=1e9,
                bestEffort=True,
                tileScale=16
            ).get('precipitation')
            monthly_total = ee.Number(val).multiply(6)
            return ee.Feature(None, {'month': m, 'rain': monthly_total})

        months = ee.List.sequence(1, 12)
        features = ee.FeatureCollection(months.map(calc_monthly_climatology)).getInfo()['features']
        
        data = []
        for f in features:
            p = f['properties']
            m_num = int(p['month'])
            if p.get('rain') is not None:
                data.append({
                    "Mês_Num": m_num,
                    "Mês": MESES_PT[m_num],
                    "Chuva (mm)": float(p['rain'])
                })
                
        return pd.DataFrame(data).sort_values('Mês_Num')

    except Exception as e:
        st.session_state['erro_clima_rain'] = str(e)
        return pd.DataFrame()

# ==========================================
# 2. RENDERIZAÇÃO DA PÁGINA
# ==========================================

def render_tab():
    st.markdown("### 🌦️ Climatologia")

    geometry = st.session_state.get('current_geometry')
    source_name = st.session_state.get('source_name', 'Desconhecido')
    
    if not geometry:
        st.warning("⚠️ Selecione um imóvel na aba 'Início' primeiro.")
        return

    # Extrai o centroide para consultas de Município e Köppen
    centroide = geometry.centroid(1).coordinates().getInfo()
    lon_dec, lat_dec = centroide[0], centroide[1]

    # Limpeza automática ao mudar de imóvel
    if st.session_state.get('last_clim_source') != source_name:
        chaves_para_deletar = [k for k in st.session_state.keys() if k.startswith('clim_') or k.startswith('erro_clima')]
        for k in chaves_para_deletar:
            del st.session_state[k]
        st.session_state['last_clim_source'] = source_name

    # --- CLASSIFICAÇÃO KÖPPEN ---
    dados_koppen = utils.get_koppen_class(lat_dec, lon_dec)
    if dados_koppen and "erro" not in dados_koppen:
        sigla = dados_koppen.get('Classificacao', 'N/A')
        desc = dados_koppen.get('Descricao', 'Sem descrição')
        st.success(f"**Classificação Climática (Köppen):** {sigla} - {desc}", icon="🌡️")

    st.write("---")

    # --- ESCOLHA DA ABRANGÊNCIA ---
    st.write("**Selecione a área de análise para os gráficos:**")
    abrangencia = st.radio(
        "Abrangência:", 
        options=["Município", "Perímetro do Imóvel"], 
        index=0, 
        horizontal=True,
        label_visibility="collapsed"
    )

    # --- DEFINIR A GEOMETRIA ALVO ---
    target_geometry = geometry
    area_label = source_name
    
    escopo_id = "mun" if abrangencia == "Município" else "perim"
    chave_cache = f"{source_name}_{escopo_id}"

    if abrangencia == "Município":
        with st.spinner("Mapeando limites do município..."):
            mun_dados = utils.get_limites_municipio_clima(lon_dec, lat_dec)
            if mun_dados:
                target_geometry = ee.Geometry(mun_dados["geojson"])
                area_label = f"{mun_dados['nome']} - {mun_dados['uf']}"
            else:
                st.warning("Município não localizado. Utilizando o perímetro do imóvel.")
                escopo_id = "perim"
                chave_cache = f"{source_name}_perim"

    st.info(f"Gerando dados climáticos para: **{area_label}**")
    
    col_temp, col_rain = st.columns(2, gap="medium")

    # --- COLUNA 1: TEMPERATURA ---
    with col_temp:
        st.subheader("🌡️ Temperatura")
        with st.container(border=True):
            st.markdown("**Médias Históricas (WorldClim)**")
            
            state_key_temp = f'clim_temp_{escopo_id}'
            
            if st.button("📉 Gerar Gráfico de Temperatura", key=f"btn_temp_{escopo_id}", use_container_width=True):
                with st.spinner("Processando WorldClim..."):
                    df_temp = get_worldclim_data(target_geometry, chave_cache)
                    if not df_temp.empty:
                        st.session_state[state_key_temp] = df_temp
                    elif 'erro_clima_temp' in st.session_state:
                        st.error(f"Erro: {st.session_state['erro_clima_temp']}")
            
            if state_key_temp in st.session_state:
                df = st.session_state[state_key_temp]
                
                med = df['Média (°C)'].mean()
                mini = df['Mínima (°C)'].mean()
                maxi = df['Máxima (°C)'].mean()
                
                c1, c2, c3 = st.columns(3)
                c1.metric("Média Anual", f"{med:.1f}".replace(".", ",") + " °C")
                c2.metric("Mínima Média", f"{mini:.1f}".replace(".", ",") + " °C")
                c3.metric("Máxima Média", f"{maxi:.1f}".replace(".", ",") + " °C")

                fig = go.Figure()
                fig.add_trace(go.Scatter(x=df['Mês'], y=df['Máxima (°C)'], mode='lines', line=dict(width=0), showlegend=False, hoverinfo='skip'))
                fig.add_trace(go.Scatter(x=df['Mês'], y=df['Mínima (°C)'], mode='lines', line=dict(width=0), fill='tonexty', fillcolor='rgba(230, 126, 34, 0.2)', showlegend=False, hoverinfo='skip'))
                fig.add_trace(go.Scatter(x=df['Mês'], y=df['Máxima (°C)'], mode='lines+markers', name='Máxima', line=dict(color='#e74c3c', width=1, dash='dot')))
                fig.add_trace(go.Scatter(x=df['Mês'], y=df['Mínima (°C)'], mode='lines+markers', name='Mínima', line=dict(color='#3498db', width=1, dash='dot')))
                fig.add_trace(go.Scatter(x=df['Mês'], y=df['Média (°C)'], mode='lines+markers', name='Média', line=dict(color='#e67e22', width=3)))

                fig.update_layout(
                    height=350, margin=dict(l=20, r=20, t=20, b=20),
                    yaxis_title="Temperatura (°C)", hovermode="x unified",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                )
                st.plotly_chart(fig, use_container_width=True)
                st.caption(f"Fonte: WorldClim V1 | Escala: {abrangencia}")

                excel_data = to_excel_horizontal(df)
                st.download_button(
                    label="📥 Baixar Dados",
                    data=excel_data,
                    file_name=f'temperatura_{chave_cache}.xlsx',
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    use_container_width=True,
                    key=f"dl_temp_{escopo_id}"
                )

    # --- COLUNA 2: PRECIPITAÇÃO ---
    with col_rain:
        st.subheader("☔ Precipitação")
        with st.container(border=True):
            st.markdown("**Médias Mensais (CHIRPS - 0.05°)**")
            
            state_key_rain = f'clim_rain_{escopo_id}'
            
            if st.button("🌧️ Gerar Gráfico de Chuva", key=f"btn_rain_{escopo_id}", use_container_width=True):
                with st.spinner("Processando CHIRPS..."):
                    df_rain = get_chirps_data(target_geometry, chave_cache)
                    if not df_rain.empty:
                        st.session_state[state_key_rain] = df_rain
                    else:
                        msg = st.session_state.get('erro_clima_rain', 'Erro desconhecido.')
                        st.error(f"Erro CHIRPS: {msg}")

            if state_key_rain in st.session_state:
                df = st.session_state[state_key_rain]
                
                total_anual = df['Chuva (mm)'].sum()
                st.metric("Acumulado Anual Médio", f"{total_anual:,.0f}".replace(",", ".") + " mm")
                
                fig = px.bar(
                    df, x="Mês", y="Chuva (mm)",
                    text_auto='.0f',
                    color="Chuva (mm)", color_continuous_scale="Blues"
                )
                
                fig.update_traces(textangle=0, textposition='outside', cliponaxis=False)
                fig.update_layout(
                    height=350, margin=dict(l=20, r=20, t=20, b=20),
                    yaxis_title="Precipitação (mm)",
                    coloraxis_showscale=False
                )
                st.plotly_chart(fig, use_container_width=True)
                st.caption(f"Fonte: CHIRPS (Série Histórica) | Escala: {abrangencia}")

                excel_data = to_excel_horizontal(df)
                st.download_button(
                    label="📥 Baixar Dados",
                    data=excel_data,
                    file_name=f'precipitacao_{chave_cache}.xlsx',
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    use_container_width=True,
                    key=f"dl_rain_{escopo_id}"
                )