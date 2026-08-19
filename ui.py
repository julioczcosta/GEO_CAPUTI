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
