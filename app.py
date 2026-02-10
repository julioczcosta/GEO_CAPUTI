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

# --- 1. CONFIGURAÇÃO DA PÁGINA (Deve ser sempre o primeiro comando Streamlit) ---
st.set_page_config(
    layout="wide", 
    page_title="GEO CAPUTI", 
    page_icon="🌍",
    initial_sidebar_state="expanded"
)

# --- 2. SISTEMA DE LOGIN (Bloqueio de Segurança) ---
def check_login():
    """Verifica se o usuário está logado via Secrets"""
    # Se já logou, libera
    if st.session_state.get("logged_in", False):
        return True

    # Layout da tela de login
    st.markdown("## 🔐 Acesso Restrito - GEO Caputi")
    
    with st.form("login_form"):
        email = st.text_input("E-mail").strip().lower()
        password = st.text_input("Senha", type="password")
        submit = st.form_submit_button("Entrar")
        
        if submit:
            if "users" not in st.secrets:
                st.error("⚠️ Configuração de usuários não encontrada nos Secrets.")
                return False
            
            known_users = st.secrets["users"]
            
            if email in known_users:
                # Compara a senha de forma segura
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
    st.stop()  # Se não logar, o código para aqui e não carrega o resto!

# =========================================================
# 🚀 O APLICATIVO REAL COMEÇA AQUI (Só carrega se logar)
# =========================================================

# --- CSS GLOBAL ---
st.markdown("""
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 2rem;}
    h1 { 
        text-align: center; 
        font-family: 'Helvetica Neue', sans-serif; 
        color: #2C3E50; 
        margin-bottom: 20px; 
    }
    .stTabs [data-baseweb="tab-list"] { justify-content: center; }
    .stTabs [data-baseweb="tab"] { font-size: 1.1rem; font-weight: 600; }
    div[data-testid="column"] { display: flex; flex-direction: column; justify-content: flex-end; }
    button { height: auto; padding: 10px !important; font-weight: 600 !important; }
    .stAlert { padding: 0.5rem; margin-bottom: 1rem; }
    .stRadio > label { font-weight: bold; font-size: 1.1rem; margin-bottom: 10px; }
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
        
    # Botão de Sair (Logout)
    if st.button("Sair / Logout"):
        st.session_state["logged_in"] = False
        st.rerun()

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