import streamlit as st
import utils
import home
import context   
import sentinel
import climatology
import consulta_car
import consulta_bases
import impedimentos

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(
    layout="wide", 
    page_title="GEO", 
    page_icon="🌍",
    initial_sidebar_state="expanded"
)

# --- CSS GLOBAL ---
st.markdown("""
    <style>
    /* Remove espaçamentos extras do Streamlit */
    .block-container {padding-top: 1rem; padding-bottom: 2rem;}
    
    /* Estilo do Título Principal */
    h1 { 
        text-align: center; 
        font-family: 'Helvetica Neue', sans-serif; 
        color: #2C3E50; 
        margin-bottom: 20px; 
    }
    
    /* Centralizar as Abas */
    .stTabs [data-baseweb="tab-list"] { justify-content: center; }
    .stTabs [data-baseweb="tab"] { font-size: 1.1rem; font-weight: 600; }
    
    /* Ajustes de botões e colunas */
    div[data-testid="column"] { display: flex; flex-direction: column; justify-content: flex-end; }
    button { height: auto; padding: 10px !important; font-weight: 600 !important; }
    
    /* Ajuste para mensagens de alerta */
    .stAlert { padding: 0.5rem; margin-bottom: 1rem; }
    
    /* Estilo do Radio Button na Sidebar */
    .stRadio > label { font-weight: bold; font-size: 1.1rem; margin-bottom: 10px; }
    </style>
    """, unsafe_allow_html=True)

# --- INICIALIZAÇÃO ---
if hasattr(utils, 'init_gee'):
    utils.init_gee()
if hasattr(utils, 'init_session_state'):
    utils.init_session_state()

# --- BARRA LATERAL (MENU) ---
with st.sidebar:
    st.title("Menu")
    
    # SELETOR DE MODO
    modo_operacao = st.radio(
        "Navegação:",
        ["Diagnóstico", "Ferramentas Avulsas"],
        captions=["Análise do imóvel selecionado", "Consultas em bases públicas"]
    )
    
    st.markdown("---")
    
    # BOX DO IMÓVEL ATIVO
    if 'last_code' in st.session_state and modo_operacao == "Diagnóstico":
        imovel_nome = st.session_state['last_code']
        st.markdown(f"""
            <div style="
                background-color: #d4edda; 
                color: #155724; 
                padding: 12px; 
                border-radius: 8px; 
                border: 1px solid #c3e6cb; 
                font-size: 14px;
                line-height: 1.4;
                word-wrap: break-word;
            ">
                <span style="font-weight: bold; display: block; margin-bottom: 5px;">📍 Imóvel Ativo:</span>
                {imovel_nome}
            </div>
        """, unsafe_allow_html=True)

# --- LÓGICA DE EXIBIÇÃO ---

if modo_operacao == "Diagnóstico":
    # MÓDULO 1: FLUXO DE ANÁLISE (Imóvel Selecionado)
    st.title("GEOCAPUTI")
    
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🏠 INÍCIO", 
        "📚 CONTEXTO", 
        "🛰️ SENTINEL-2", 
        "🌦️ CLIMATOLOGIA", 
        "🚫 IMPEDIMENTOS"
    ])

    with tab1: home.render_tab()
    with tab2: context.render_tab()
    with tab3: sentinel.render_tab()
    with tab4: climatology.render_tab()
    with tab5: impedimentos.render_tab()

else:
    # MÓDULO 2: FERRAMENTAS & CONSULTAS
    st.title("FERRAMENTAS & CONSULTAS")
    
    tab_a, tab_b, tab_c = st.tabs([
        "🔍 CONSULTA CAR",
        "📡 CONSULTA INCRA (SIGEF/SNCI)", 
        "🌾 APTIDÃO AGRÍCOLA" 
    ])

    with tab_a: consulta_car.render_tab() 
    with tab_b: consulta_bases.render_tab() 
    with tab_c: aptidao.render_tab()