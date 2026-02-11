import streamlit as st
import utils
import home
import context   
import sentinel
import climatology
import consulta_car
import consulta_bases
import impedimentos
import aptidao
import hmac

# --- 1. CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(
    layout="wide", 
    page_title="GEO CAPUTI", 
    page_icon="🌍",
    initial_sidebar_state="expanded"
)

# --- 2. SISTEMA DE LOGIN (Bloqueio de Segurança) ---
def check_login():
    """Verifica se o usuário está logado via Secrets"""
    if st.session_state.get("logged_in", False):
        return True

    # Layout da tela de login
    col1, col2, col3 = st.columns([1,1,1])
    with col2:
        st.markdown("## 🔐 Acesso Restrito - GEO Caputi")
        
        with st.form("login_form"):
            email = st.text_input("E-mail").strip().lower()
            password = st.text_input("Senha", type="password")
            submit = st.form_submit_button("Entrar", use_container_width=True)
            
            if submit:
                if "users" not in st.secrets:
                    st.error("⚠️ Configuração de usuários não encontrada nos Secrets.")
                    return False
                
                known_users = st.secrets["users"]
                
                if email in known_users:
                    if hmac.compare_digest(password, known_users[email]):
                        st.session_state["logged_in"] = True
                        st.session_state["user_email"] = email
                        st.rerun()
                    else:
                        st.error("❌ Senha incorreta.")
                else:
                    st.error("❌ E-mail não cadastrado.")
    return False

# --- TRAVA DE SEGURANÇA ---
if not check_login():
    st.stop()

# =========================================================
# 🚀 O APLICATIVO REAL COMEÇA AQUI
# =========================================================

# --- CSS GLOBAL (Personalizado para tons da Logo) ---
st.markdown("""
    <style>
    /* Remove padding excessivo do topo */
    .block-container {padding-top: 1rem; padding-bottom: 2rem;}
    
    /* Estilo dos Títulos */
    h1, h2, h3 { 
        font-family: 'Helvetica Neue', sans-serif; 
        color: #2C3E50; /* Cinza Chumbo da Logo */
    }
    
    /* Centralizar Abas e melhorar visual */
    .stTabs [data-baseweb="tab-list"] { 
        justify-content: center; 
        border-bottom: 2px solid #f0f2f6;
    }
    .stTabs [data-baseweb="tab"] { 
        font-size: 1rem; 
        font-weight: 600; 
        color: #555;
    }
    .stTabs [data-baseweb="tab"][aria-selected="true"] {
        color: #009e60; /* Verde da Logo */
        border-bottom-color: #009e60;
    }
    
    /* Ajustes de botões (Verde da marca) */
    div.stButton > button[kind="primary"] {
        background-color: #009e60;
        border-color: #009e60;
    }
    div.stButton > button[kind="primary"]:hover {
        background-color: #007f4d;
        border-color: #007f4d;
    }
    
    /* Box do Imóvel Ativo */
    .imovel-box {
        background-color: #e8f5e9; /* Verde muito claro */
        color: #1b5e20; 
        padding: 12px; 
        border-radius: 8px; 
        border: 1px solid #c8e6c9; 
        font-size: 14px;
        line-height: 1.4;
        word-wrap: break-word;
    }
    </style>
    """, unsafe_allow_html=True)

# --- INICIALIZAÇÃO DE UTILS ---
if hasattr(utils, 'init_gee'):
    utils.init_gee()
if hasattr(utils, 'init_session_state'):
    utils.init_session_state()

# --- BARRA LATERAL (MENU) ---
with st.sidebar:
    # Mostra quem está logado
    usuario_logado = st.session_state.get("user_email", "Usuário")
    st.info(f"👤 Logado como: **{usuario_logado}**")
    
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
            <div class="imovel-box">
                <span style="font-weight: bold; display: block; margin-bottom: 5px;">📍 Imóvel Ativo:</span>
                {imovel_nome}
            </div>
        """, unsafe_allow_html=True)
        
    st.write("")
    if st.button("Sair / Logout", use_container_width=True):
        st.session_state["logged_in"] = False
        st.rerun()

# =========================================================
# 🖼️ CABEÇALHO COM LOGO (GLOBAL)
# =========================================================
# Colocamos aqui para aparecer em TODAS as abas

# Cria 3 colunas para centralizar a imagem (Vazio | Imagem | Vazio)
c_head_1, c_head_2, c_head_3 = st.columns([1, 1.5, 1]) 

with c_head_2:
    try:
        # Tenta carregar a imagem
        st.image("imagem/geocaputi.png", use_container_width=True)
    except:
        # Fallback caso a imagem não exista ainda (para não quebrar o app)
        st.title("GEOCAPUTI")

st.write("") # Espaçamento

# =========================================================
# LÓGICA DE NAVEGAÇÃO
# =========================================================

if modo_operacao == "Diagnóstico":
    # MÓDULO 1: FLUXO DE ANÁLISE (Imóvel Selecionado)
    # Obs: Removemos o st.title("GEOCAPUTI") daqui porque a logo já está em cima
    
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
    st.markdown("<h3 style='text-align: center; color: #555;'>FERRAMENTAS & CONSULTAS</h3>", unsafe_allow_html=True)
    
    tab_a, tab_b, tab_c = st.tabs([
        "🔍 CONSULTA CAR",
        "📡 CONSULTA INCRA (SIGEF/SNCI)", 
        "🌾 APTIDÃO AGRÍCOLA" 
    ])

    with tab_a: consulta_car.render_tab() 
    with tab_b: consulta_bases.render_tab() 
    with tab_c: aptidao.render_tab()