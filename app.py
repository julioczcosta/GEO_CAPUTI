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
import confrontantes
import hmac
import base64
from streamlit_option_menu import option_menu

# --- 1. CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(
    layout="wide", 
    page_title="GEOCAPUTI", 
    page_icon="🌍",
    initial_sidebar_state="expanded"
)

# --- 2. SISTEMA DE LOGIN (Bloqueio de Segurança) ---
def check_login():
    """Verifica se o usuário está logado via Secrets"""
    if st.session_state.get("logged_in", False):
        return True

    col1, col2, col3 = st.columns([1,1,1])
    with col2:
        st.markdown("## 🔐 Acesso - GEOCAPUTI")
        
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

# --- CSS GLOBAL REFINADO ---
st.markdown("""
    <style>
    /* ============================================================
       1. LAYOUT E ESPAÇAMENTO
       ============================================================ */
    
    .block-container {
        padding-top: 3.5rem !important; 
        padding-bottom: 3rem;
    }
    
    div[data-testid="stHorizontalBlock"] {
        align-items: center;
    }

    /* ============================================================
       2. TIPOGRAFIA E CORES
       ============================================================ */
    
    h1, h2, h3, h4, h5 { 
        font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; 
        color: #2C3E50; 
        font-weight: 600;
    }
    
    .stCaption {
        color: #666;
        font-size: 0.9rem;
    }

    /* ============================================================
       3. SIDEBAR (MENU LATERAL)
       ============================================================ */
    
    .imovel-box {
        background-color: #e8f5e9; 
        color: #1b5e20; 
        padding: 12px; 
        border-radius: 8px; 
        border: 1px solid #c8e6c9; 
        font-size: 13px;
        line-height: 1.4;
        word-wrap: break-word;
        margin-top: 10px;
        margin-bottom: 20px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }

    /* ============================================================
       4. BOTÕES (SISTEMA BICOLOR: VERDE & CHUMBO)
       ============================================================ */

    div.stButton > button[kind="primary"] {
        background-color: #009e60 !important;
        border-color: #009e60 !important;
        color: white !important;
        font-weight: 600;
        border-radius: 6px;
        transition: all 0.3s ease;
    }
    
    div.stButton > button[kind="primary"]:hover {
        background-color: #007f4d !important;
        border-color: #007f4d !important;
        box-shadow: 0 4px 8px rgba(0,158,96,0.2);
        transform: translateY(-1px);
    }

    div.stButton > button:not([kind="primary"]) {
        background-color: transparent !important;
        color: #2C3E50 !important;
        border-color: #2C3E50 !important;
        border-width: 1px !important;
        font-weight: 600;
        border-radius: 6px;
        transition: all 0.3s ease;
    }

    div.stButton > button:not([kind="primary"]):hover {
        background-color: #2C3E50 !important;
        color: white !important;
        border-color: #2C3E50 !important;
        box-shadow: 0 4px 8px rgba(44,62,80,0.2);
    }

    /* ============================================================
       5. COMPONENTES EXTRAS
       ============================================================ */
    
    div[data-baseweb="input"] > div:focus-within {
        border-color: #009e60 !important;
        box-shadow: none !important;
    }

    div[role="radiogroup"] label[data-baseweb="radio"] > div:first-child {
        background-color: #009e60 !important; 
        border-color: #009e60 !important;
    }

    .stAlert {
        border-radius: 8px;
        border: none;
    }
    
    </style>
    """, unsafe_allow_html=True)

# --- INICIALIZAÇÃO DE UTILS ---
if hasattr(utils, 'init_gee'):
    utils.init_gee()
if hasattr(utils, 'init_session_state'):
    utils.init_session_state()

# --- BARRA LATERAL ---
with st.sidebar:
    usuario_logado = st.session_state.get("user_email", "Usuário")
    st.caption(f"👤 {usuario_logado}")
    st.write("")
    
    modo_operacao = st.radio(
        "Navegação", 
        ["Diagnóstico do Imóvel", "Consultas Públicas"],
        captions=["Análise completa do perímetro", "Bases do CAR, INCRA e Aptidão"],
        label_visibility="collapsed" 
    )
    
    st.markdown("---")
    
    if 'last_code' in st.session_state and modo_operacao == "Diagnóstico do Imóvel":
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
# 🖼️ CABEÇALHO COM LOGO
# =========================================================

try:
    with open("imagem/geocaputi.png", "rb") as f:
        img_data = base64.b64encode(f.read()).decode()
    
    st.markdown(
        f"""
        <div style="
            display: flex; 
            justify-content: center; 
            align-items: center; 
            padding-bottom: 15px;
            position: relative;
            z-index: 1;
        ">
            <img src="data:image/png;base64,{img_data}" 
                 style="
                    width: 500px; 
                    max-width: 90%; 
                    height: auto; 
                    object-fit: contain;
                 ">
        </div>
        """,
        unsafe_allow_html=True
    )
except FileNotFoundError:
    st.markdown("<h1 style='text-align: center;'>GEOCAPUTI</h1>", unsafe_allow_html=True)

# =========================================================
# 🧭 BARRA DE NAVEGAÇÃO
# =========================================================

styles_menu = {
    "container": {"padding": "0!important", "background-color": "#f8f9fa"},
    "icon": {"color": "#555", "font-size": "16px"}, 
    "nav-link": {
        "font-size": "16px", 
        "text-align": "center", 
        "margin": "0px", 
        "padding-top": "12px",
        "padding-bottom": "12px",
        "--hover-color": "#eee"
    },
    "nav-link-selected": {"background-color": "#009e60", "font-weight": "600"},
}

if modo_operacao == "Diagnóstico do Imóvel":
    selected = option_menu(
        menu_title=None, 
        options=["Início", "Contexto", "Imagens de Satélite", "Climatologia", "Impedimentos", "Confrontantes"],
        icons=["house", "geo-alt", "layers", "cloud-rain", "exclamation-triangle", "people-fill"], 
        menu_icon="cast", 
        default_index=0, 
        orientation="horizontal",
        styles=styles_menu
    )

    if selected == "Início":
        home.render_tab()
    elif selected == "Contexto":
        context.render_tab()
    elif selected == "Imagens de Satélite":
        sentinel.render_tab()
    elif selected == "Climatologia":
        climatology.render_tab()
    elif selected == "Impedimentos":
        impedimentos.render_tab()
    elif selected == "Confrontantes":
        confrontantes.render_tab()

else:
    selected_tool = option_menu(
        menu_title=None, 
        options=["Consulta CAR", "Consulta INCRA", "Aptidão Agrícola"],
        icons=["search", "broadcast", "tree"], 
        menu_icon="cast", 
        default_index=0, 
        orientation="horizontal",
        styles=styles_menu
    )

    if selected_tool == "Consulta CAR":
        consulta_car.render_tab() 
    elif selected_tool == "Consulta INCRA":
        consulta_bases.render_tab() 
    elif selected_tool == "Aptidão Agrícola":
        aptidao.render_tab()
