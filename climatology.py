import streamlit as st
import ee
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import io  # Necessário para criar o arquivo Excel na memória

# ==========================================
# 0. CONFIGURAÇÕES E UTILITÁRIOS
# ==========================================

MESES_PT = {
    1: 'Jan', 2: 'Fev', 3: 'Mar', 4: 'Abr', 5: 'Mai', 6: 'Jun',
    7: 'Jul', 8: 'Ago', 9: 'Set', 10: 'Out', 11: 'Nov', 12: 'Dez'
}

def to_excel_horizontal(df, index_col='Mês'):
    """
    Converte DataFrame para Excel transposto (Horizontal).
    Os meses ficam no cabeçalho e os valores na linha.
    """
    output = io.BytesIO()
    
    # 1. Remove a coluna numérica de ordenação se existir
    if 'Mês_Num' in df.columns:
        df = df.drop(columns=['Mês_Num'])
    
    # 2. Define o Mês como índice para poder transpor
    df_t = df.set_index(index_col).T  # T faz a transposição 
    
    # 3. Salva no Excel
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_t.to_excel(writer, sheet_name='Dados')
        
        # Ajuste cosmético: Tenta alargar as colunas para caber os números
        worksheet = writer.sheets['Dados']
        worksheet.set_column(0, 12, 15) # Alarga primeira coluna (nomes das variáveis)
    
    processed_data = output.getvalue()
    return processed_data

# ==========================================
# 1. FUNÇÕES DE DADOS (CACHE CORRIGIDO)
# ==========================================

@st.cache_data(show_spinner=False)
def get_worldclim_data(_geometry, cache_id):
    """
    Busca dados de Temperatura (WorldClim V1 Monthly).
    """
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
    """
    Busca dados de Precipitação (CHIRPS Pentad).
    """
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

    # Limpeza automática ao mudar de imóvel
    if st.session_state.get('last_clim_source') != source_name:
        if 'clim_temp' in st.session_state: del st.session_state['clim_temp']
        if 'clim_rain' in st.session_state: del st.session_state['clim_rain']
        if 'erro_clima_temp' in st.session_state: del st.session_state['erro_clima_temp']
        if 'erro_clima_rain' in st.session_state: del st.session_state['erro_clima_rain']
        st.session_state['last_clim_source'] = source_name

    st.info(f"Análise Climática para: **{source_name}**")
    
    col_temp, col_rain = st.columns(2, gap="medium")

    # --- COLUNA 1: TEMPERATURA ---
    with col_temp:
        st.subheader("🌡️ Temperatura")
        with st.container(border=True):
            st.markdown("**Médias Históricas (WorldClim)**")
            
            if st.button("📉 Gerar Gráfico de Temperatura", use_container_width=True):
                with st.spinner("Processando WorldClim..."):
                    df_temp = get_worldclim_data(geometry, source_name)
                    if not df_temp.empty:
                        st.session_state['clim_temp'] = df_temp
                    elif 'erro_clima_temp' in st.session_state:
                        st.error(f"Erro: {st.session_state['erro_clima_temp']}")
            
            if 'clim_temp' in st.session_state:
                df = st.session_state['clim_temp']
                
                # Métricas
                med = df['Média (°C)'].mean()
                mini = df['Mínima (°C)'].mean()
                maxi = df['Máxima (°C)'].mean()
                
                c1, c2, c3 = st.columns(3)
                c1.metric("Média Anual", f"{med:.1f}".replace(".", ",") + " °C")
                c2.metric("Mínima Média", f"{mini:.1f}".replace(".", ",") + " °C")
                c3.metric("Máxima Média", f"{maxi:.1f}".replace(".", ",") + " °C")

                # Gráfico
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
                st.caption("Fonte: WorldClim V1 (Normais Climatológicas)")

                # Download Excel Horizontal
                excel_data = to_excel_horizontal(df)
                st.download_button(
                    label="📥 Baixar Dados",
                    data=excel_data,
                    file_name=f'temperatura_{source_name}.xlsx',
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    use_container_width=True
                )

    # --- COLUNA 2: PRECIPITAÇÃO ---
    with col_rain:
        st.subheader("☔ Precipitação")
        with st.container(border=True):
            st.markdown("**Médias Mensais (CHIRPS - 0.05°)**")
            
            if st.button("🌧️ Gerar Gráfico de Chuva", use_container_width=True):
                with st.spinner("Processando CHIRPS..."):
                    df_rain = get_chirps_data(geometry, source_name)
                    if not df_rain.empty:
                        st.session_state['clim_rain'] = df_rain
                    else:
                        msg = st.session_state.get('erro_clima_rain', 'Erro desconhecido.')
                        st.error(f"Erro CHIRPS: {msg}")

            if 'clim_rain' in st.session_state:
                df = st.session_state['clim_rain']
                
                total_anual = df['Chuva (mm)'].sum()
                st.metric("Acumulado Anual Médio", f"{total_anual:,.0f}".replace(",", ".") + " mm")
                
                fig = px.bar(
                    df, x="Mês", y="Chuva (mm)",
                    text_auto='.0f',
                    color="Chuva (mm)", color_continuous_scale="Blues"
                )
                
                # Ajuste de layout dos textos
                fig.update_traces(textangle=0, textposition='outside', cliponaxis=False)
                fig.update_layout(
                    height=350, margin=dict(l=20, r=20, t=20, b=20),
                    yaxis_title="Precipitação (mm)",
                    coloraxis_showscale=False
                )
                st.plotly_chart(fig, use_container_width=True)
                st.caption("Fonte: CHIRPS (Série Histórica 2000-2025)")

                # Download Excel Horizontal
                excel_data = to_excel_horizontal(df)
                st.download_button(
                    label="📥 Baixar Dados",
                    data=excel_data,
                    file_name=f'precipitacao_{source_name}.xlsx',
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    use_container_width=True
                )