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
import uso_solo
import hmac
import base64
import streamlit.components.v1 as components
from streamlit_option_menu import option_menu
from PIL import Image as _PILImage

# --- 1. CONFIGURAÇÃO DA PÁGINA ---
try:
    _FAVICON = _PILImage.open("imagem/favicon.png")   # emblema como ícone da aba
except Exception:
    _FAVICON = "🌍"
st.set_page_config(
    layout="wide",
    page_title="GEOCAPUTI",
    page_icon=_FAVICON,
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

                # Compara sempre (mesmo com e-mail inexistente) usando um valor
                # placeholder, para não vazar quais e-mails existem — nem pelo
                # texto da mensagem nem pelo tempo de resposta.
                senha_esperada = known_users.get(email, "")
                senha_valida = bool(senha_esperada) and hmac.compare_digest(password, senha_esperada)

                if senha_valida:
                    st.session_state["logged_in"] = True
                    st.session_state["user_email"] = email
                    st.rerun()
                else:
                    st.error("❌ E-mail ou senha incorretos.")
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
        background-color: #27a64a !important;
        border-color: #27a64a !important;
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
        border-color: #27a64a !important;
        box-shadow: none !important;
    }

    div[role="radiogroup"] label[data-baseweb="radio"] > div:first-child {
        background-color: #27a64a !important; 
        border-color: #27a64a !important;
    }

    .stAlert {
        border-radius: 8px;
        border: none;
    }

    /* ============================================================
       6. MÉTRICAS (CARD PADRÃO EM TODO O APP)
       ============================================================ */

    div[data-testid="stMetric"] {
        background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
        border: 1px solid #e9ecef;
        border-left: 4px solid #27a64a;
        border-radius: 12px;
        padding: 14px 18px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }
    div[data-testid="stMetricLabel"] p {
        font-size: 0.78rem !important;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.4px;
        color: #6c757d;
    }
    div[data-testid="stMetricValue"] {
        color: #2C3E50;
        font-weight: 700;
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
    with open("imagem/header_geocaputi.png", "rb") as f:
        _logo_light = base64.b64encode(f.read()).decode()
    try:
        with open("imagem/header_geocaputi_dark.png", "rb") as f:
            _logo_dark = base64.b64encode(f.read()).decode()
    except FileNotFoundError:
        _logo_dark = _logo_light

    # Duas versões embutidas; troca automática conforme o tema do navegador (a
    # versão escura tem o "CAPUTI" claro pra não sumir em fundo escuro).
    st.markdown(
        f"""
        <style>
          .gc-logo{{display:flex;justify-content:center;align-items:center;
                    padding-bottom:15px;position:relative;z-index:1;}}
          .gc-logo img{{width:520px;max-width:92%;height:auto;object-fit:contain;}}
          .gc-logo .dark{{display:none;}}
          @media (prefers-color-scheme: dark){{
            .gc-logo .light{{display:none;}}
            .gc-logo .dark{{display:block;}}
          }}
        </style>
        <div class="gc-logo">
            <img class="light" src="data:image/png;base64,{_logo_light}" alt="GEOCAPUTI">
            <img class="dark"  src="data:image/png;base64,{_logo_dark}" alt="GEOCAPUTI">
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
    "icon": {"color": "#555", "font-size": "14px"},
    "nav-link": {
        "font-size": "14px",
        "text-align": "center",
        "margin": "0px",
        "padding-top": "12px",
        "padding-bottom": "12px",
        "--hover-color": "#eee"
    },
    "nav-link-selected": {"background-color": "#27a64a", "font-weight": "600"},
}

# O componente streamlit-option-menu roda em um iframe e, quando o menu
# nasce durante a transição de abertura/fechamento da sidebar, mede a si
# mesmo com a largura ainda "encolhida" e nunca refaz essa medição depois
# — os itens ficam espremidos, quebram em várias linhas de alturas
# diferentes e o iframe fica travado numa altura que não bate com o
# conteúdo real (o "buraco" sobrando embaixo do menu). Esse script força a
# largura para 100% do espaço disponível e recalcula a altura pelo
# conteúdo real do menu. Usa ResizeObserver no container do menu (não só
# o evento "resize" da janela) porque abrir/fechar a sidebar muda o
# layout sem redimensionar a janela do navegador.
components.html(
    """
    <script>
    (function() {
        var win = window.parent;
        var doc = win.document;

        function fixOne(iframe) {
            try {
                iframe.style.width = '100%';
                var innerDoc = iframe.contentDocument;
                var ul = innerDoc ? innerDoc.querySelector('ul.nav') : null;
                var h = ul ? ul.getBoundingClientRect().height : 0;
                if (h > 0) { iframe.style.height = h + 'px'; }
            } catch (e) {}
        }

        function fixAll() {
            doc.querySelectorAll('iframe[title="streamlit_option_menu.option_menu"]').forEach(fixOne);
        }

        var observados = new Set();
        function observarContainers() {
            doc.querySelectorAll('iframe[title="streamlit_option_menu.option_menu"]').forEach(function(iframe) {
                var container = iframe.closest('.element-container') || iframe.parentElement;
                if (container && !observados.has(container) && win.ResizeObserver) {
                    observados.add(container);
                    new win.ResizeObserver(function() { fixOne(iframe); }).observe(container);
                }
            });
        }

        [100, 300, 600, 1000, 1800].forEach(function(t) {
            setTimeout(fixAll, t);
            setTimeout(observarContainers, t);
        });
        win.addEventListener('resize', fixAll);
    })();
    </script>
    """,
    height=0,
)

if modo_operacao == "Diagnóstico do Imóvel":
    selected = option_menu(
        menu_title=None, 
        options=["Início", "Contexto", "Satélite", "Climatologia", "Uso do Solo", "Impedimentos", "Confrontantes"],
        icons=["house", "geo-alt", "layers", "cloud-rain", "map", "exclamation-triangle", "people-fill"],
        menu_icon="cast", 
        default_index=0, 
        orientation="horizontal",
        styles=styles_menu
    )

    if selected == "Início":
        home.render_tab()
    elif selected == "Contexto":
        context.render_tab()
    elif selected == "Satélite":
        sentinel.render_tab()
    elif selected == "Climatologia":
        climatology.render_tab()
    elif selected == "Uso do Solo":
        uso_solo.render_tab()
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
