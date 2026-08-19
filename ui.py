# -*- coding: utf-8 -*-
"""
Componentes e constantes de UI compartilhados (Fase 2 da auditoria de layout).

Objetivo: dar ao app uma cara única — mesmos títulos de seção, mesma cor de
perímetro e mesmas alturas de mapa em todas as abas — sem cada tela reinventar
o seu estilo. Importe com `import ui` e use `ui.secao(...)`, `ui.COR_PERIMETRO`, etc.
"""

import streamlit as st

# ---- Paleta (mesma identidade do app: verde + chumbo) ----
VERDE = "#009e60"
VERDE_ESCURO = "#007f4d"
CHUMBO = "#2C3E50"

# Cor do PERÍMETRO do imóvel analisado — uma só em todo o app (lê bem no
# satélite). Antes variava: vermelho no Início/Uso do Solo, ciano em outras.
COR_PERIMETRO = "#00E5FF"
# Cor de RESULTADO de consulta (parcela CAR/SIGEF localizada, sobreposição).
COR_RESULTADO = "#FFD400"

# ---- Alturas de mapa padronizadas ----
MAPA_H = 460          # mapa embutido (visualização)
MAPA_H_INTER = 540    # mapa interativo (clique/seleção)


def secao(titulo):
    """Cabeçalho de seção padrão: barra de acento verde + título em chumbo.
    Use no lugar de st.subheader/###/#### para todas as seções ficarem iguais."""
    st.markdown(
        "<div style='display:flex;align-items:center;gap:9px;margin:6px 0 8px;'>"
        f"<span style='width:4px;height:18px;border-radius:2px;background:{VERDE};"
        "display:inline-block;flex:none;'></span>"
        f"<span style='font-weight:650;font-size:1.05rem;color:{CHUMBO};"
        "letter-spacing:-.01em;'>" + titulo + "</span></div>",
        unsafe_allow_html=True,
    )


def vazio(msg=None):
    """Estado vazio padrão: nenhum imóvel carregado. Mesma mensagem/estilo em
    todas as abas de análise (antes cada uma usava um warning/info diferente)."""
    if msg is None:
        msg = ("Carregue um imóvel na aba **Início** para começar. As análises "
               "são feitas sobre o perímetro selecionado lá.")
    st.info("📍 " + msg)


def erro(msg, detalhe=None):
    """Mensagem de erro padrão. Mostra o que aconteceu e, se houver, o
    **detalhe técnico** (para reportar / diagnosticar) — em vez de sumir a
    informação ou estourar um traceback."""
    texto = "⚠️ " + msg
    if detalhe:
        texto += f"\n\n**Detalhe técnico:** `{detalhe}`"
    st.error(texto)


def _br_num(valor, dec=2):
    try:
        return f"{float(valor):,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(valor)


def barra_imovel(nome=None, area_ha=None):
    """Faixa padrão de 'imóvel ativo' no topo das abas de análise (item 3 da
    auditoria). Mostra o imóvel + área. Lê do session_state se não vier por
    parâmetro e NÃO faz rede (área calculada localmente do gdf) — assim pode
    aparecer em toda aba sem custo. Não renderiza nada se não houver imóvel."""
    if nome is None:
        nome = st.session_state.get("last_code") or st.session_state.get("source_name")
    if not nome:
        return

    if area_ha is None:
        gdf = st.session_state.get("gdf_imovel")
        if gdf is not None and getattr(gdf, "empty", True) is False:
            try:
                g = gdf if gdf.crs is not None else gdf.set_crs(4326)
                area_ha = float(g.to_crs(5880).area.sum() / 1e4)
            except Exception:
                area_ha = None

    area_txt = ""
    if area_ha:
        area_txt = (f"<span style='color:#5a6b62;font-size:.9rem;'>"
                    f"· {_br_num(area_ha)} ha</span>")

    st.markdown(
        "<div style='display:flex;align-items:center;gap:10px;background:#eef5f0;"
        f"border:1px solid #d7e6dd;border-left:4px solid {VERDE};border-radius:8px;"
        "padding:8px 14px;margin:0 0 14px;'>"
        "<span>📍</span>"
        f"<span style='font-weight:650;color:{CHUMBO};'>{nome}</span>"
        f"{area_txt}</div>",
        unsafe_allow_html=True,
    )
