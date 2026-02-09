import streamlit as st
import ee
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import calendar

# ==========================================
# 1. FUNÇÕES DE DADOS (CACHE CORRIGIDO)
# ==========================================

# Adicionamos 'cache_id' para forçar o Streamlit a diferenciar os imóveis
@st.cache_data(show_spinner=False)
def get_worldclim_data(_geometry, cache_id):
    """
    Busca dados de Temperatura (WorldClim V1 Monthly).
    """
    try:
        # Simplificação segura
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
            if p.get('avg') is not None:
                data.append({
                    "Mês_Num": int(p['month']),
                    "Mês": calendar.month_abbr[int(p['month'])],
                    "Média": float(p['avg']),
                    "Mínima": float(p['min']),
                    "Máxima": float(p['max'])
                })
        
        return pd.DataFrame(data).sort_values('Mês_Num')
        
    except Exception as e:
        st.session_state['erro_clima_temp'] = str(e)
        return pd.DataFrame()

@st.cache_data(show_spinner=False)
def get_chirps_data(_geometry, cache_id):
    """
    Busca dados de Precipitação (CHIRPS Pentad - 2000 a 2025).
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
            if p.get('rain') is not None:
                data.append({
                    "Mês_Num": int(p['month']),
                    "Mês": calendar.month_abbr[int(p['month'])],
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

    # --- LÓGICA DE LIMPEZA AUTOMÁTICA ---
    # Verifica se o imóvel mudou desde a última vez que carregamos esta aba.
    # Se mudou, limpamos os gráficos antigos da memória.
    if st.session_state.get('last_clim_source') != source_name:
        if 'clim_temp' in st.session_state: del st.session_state['clim_temp']
        if 'clim_rain' in st.session_state: del st.session_state['clim_rain']
        if 'erro_clima_temp' in st.session_state: del st.session_state['erro_clima_temp']
        if 'erro_clima_rain' in st.session_state: del st.session_state['erro_clima_rain']
        st.session_state['last_clim_source'] = source_name
        # Opcional: st.rerun() # Descomente se quiser que a tela pisque e limpe na hora

    st.info(f"Análise Climática para: **{source_name}**")
    
    col_temp, col_rain = st.columns(2, gap="medium")

    # --- COLUNA 1: TEMPERATURA ---
    with col_temp:
        st.subheader("🌡️ Temperatura")
        with st.container(border=True):
            st.markdown("**Médias Históricas (WorldClim)**")
            
            # Passamos 'source_name' como segundo argumento para garantir que o cache renove
            if st.button("📉 Gerar Gráfico de Temperatura", use_container_width=True):
                with st.spinner("Processando WorldClim..."):
                    df_temp = get_worldclim_data(geometry, source_name)
                    if not df_temp.empty:
                        st.session_state['clim_temp'] = df_temp
                    elif 'erro_clima_temp' in st.session_state:
                        st.error(f"Erro: {st.session_state['erro_clima_temp']}")
            
            if 'clim_temp' in st.session_state:
                df = st.session_state['clim_temp']
                
                med = df['Média'].mean()
                mini = df['Mínima'].mean()
                maxi = df['Máxima'].mean()
                
                fmt_med = f"{med:.1f}".replace(".", ",")
                fmt_min = f"{mini:.1f}".replace(".", ",")
                fmt_max = f"{maxi:.1f}".replace(".", ",")

                c1, c2, c3 = st.columns(3)
                c1.metric("Média Anual", f"{fmt_med} °C")
                c2.metric("Mínima Média", f"{fmt_min} °C")
                c3.metric("Máxima Média", f"{fmt_max} °C")

                fig = go.Figure()
                fig.add_trace(go.Scatter(x=df['Mês'], y=df['Máxima'], mode='lines', line=dict(width=0), showlegend=False, hoverinfo='skip'))
                fig.add_trace(go.Scatter(x=df['Mês'], y=df['Mínima'], mode='lines', line=dict(width=0), fill='tonexty', fillcolor='rgba(230, 126, 34, 0.2)', showlegend=False, hoverinfo='skip'))
                fig.add_trace(go.Scatter(x=df['Mês'], y=df['Máxima'], mode='lines+markers', name='Máxima', line=dict(color='#e74c3c', width=1, dash='dot')))
                fig.add_trace(go.Scatter(x=df['Mês'], y=df['Mínima'], mode='lines+markers', name='Mínima', line=dict(color='#3498db', width=1, dash='dot')))
                fig.add_trace(go.Scatter(x=df['Mês'], y=df['Média'], mode='lines+markers', name='Média', line=dict(color='#e67e22', width=3)))

                fig.update_layout(
                    height=350, margin=dict(l=20, r=20, t=20, b=20),
                    yaxis_title="Temperatura (°C)", hovermode="x unified",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                )
                st.plotly_chart(fig, use_container_width=True)
                st.caption("Fonte: WorldClim V1 (Normais Climatológicas)")

    # --- COLUNA 2: PRECIPITAÇÃO ---
    with col_rain:
        st.subheader("☔ Precipitação")
        with st.container(border=True):
            st.markdown("**Médias Mensais (CHIRPS - 0.05°)**")
            
            # Passamos 'source_name' como segundo argumento para garantir que o cache renove
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
                val_fmt = f"{total_anual:,.0f}".replace(",", ".")
                
                st.metric("Acumulado Anual Médio", f"{val_fmt} mm")
                
                fig = px.bar(
                    df, x="Mês", y="Chuva (mm)",
                    text_auto='.0f',
                    color="Chuva (mm)", color_continuous_scale="Blues"
                )
                fig.update_layout(
                    height=350, margin=dict(l=20, r=20, t=20, b=20),
                    yaxis_title="Precipitação (mm)",
                    coloraxis_showscale=False
                )
                st.plotly_chart(fig, use_container_width=True)
                st.caption("Fonte: CHIRPS (Série Histórica 2000-2025)")