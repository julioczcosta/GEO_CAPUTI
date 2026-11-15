# -*- coding: utf-8 -*-
"""
Aba "Mercado": economia agrícola em volta do imóvel (Fase 1 = produção e pecuária).

Escalas (ver plano da aba): o MUNICÍPIO é a base; a REGIÃO IMEDIATA do IBGE é o
comparativo (soma dos municípios dela); a UF é a referência. Quando o imóvel
cruza vários municípios:
  - taxas (kg/ha) são ponderadas pela área do imóvel em cada município;
  - totais (ha colhidos, cabeças) aparecem por município e nunca são ponderados;
  - se um município tem >= 90% da área, ele vale sozinho.

Dados ao vivo do IBGE/SIDRA pela biblioteca agrobr (Python >= 3.11). Uma consulta
traz TODAS as culturas dos municípios da região e outra as da UF (~5 s), com
fallback automático para a API de agregados do IBGE quando o SIDRA recusa.
Resultado em st.cache_data por 7 dias; falha vira exceção (não é cacheada).
"""

import asyncio
import logging
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui
import utils

# agrobr loga cada requisição em JSON no nível INFO; no app só interessa aviso/erro.
logging.getLogger("agrobr").setLevel(logging.WARNING)

TTL_IBGE = 7 * 24 * 3600
CORTE_UNICO = 90.0          # % de área a partir do qual um município vale sozinho
N_CULTURAS = 5              # culturas sugeridas (maiores pela área colhida)

TAB_PAM = "5457"            # PAM: lavouras temporárias e permanentes
VARS_PAM = {"216": "area_colhida", "214": "producao", "112": "rendimento", "215": "valor"}
TAB_PPM = "3939"            # PPM: efetivo dos rebanhos
VAR_PPM_EFETIVO = "105"
COD_BOVINO = "2670"

UNIDADES = {
    "Quilogramas por Hectare": "kg/ha",
    "Frutos por Hectare": "frutos/ha",
    "Toneladas": "t",
    "Hectares": "ha",
}


# =========================================================================
# Formatação
# =========================================================================
def _br(v, dec=0):
    if v is None or pd.isna(v):
        return "–"
    return f"{float(v):,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v):
    if v is None or pd.isna(v):
        return "–"
    return ("+" if v >= 0 else "−") + _br(abs(v), 1) + "%"


def _num_sidra(v):
    """SIDRA: '-' = zero; '...', '..', 'X' = sem dado/sigilo."""
    s = str(v).strip()
    if s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return float("nan")


# =========================================================================
# Consultas (cacheadas; falha levanta exceção para não ficar presa no cache)
# =========================================================================
def _sidra(*consultas):
    """Roda várias consultas fetch_sidra do agrobr em paralelo."""
    from agrobr.ibge import client  # import tardio: se faltar, só esta aba avisa

    async def _todas():
        return await asyncio.gather(*(client.fetch_sidra(*c) for c in consultas))

    return asyncio.run(_todas())


def _tidy(df):
    df = df.rename(columns={"NC": "nivel", "D1C": "cod", "D1N": "local", "D2C": "ano",
                            "D3C": "var", "D4C": "item_cod", "D4N": "item", "MN": "unidade"})
    df["valor"] = df["V"].map(_num_sidra)
    df["ano"] = pd.to_numeric(df["ano"], errors="coerce").astype("Int64")
    df["nivel"] = df["nivel"].astype(str)
    df["cod"] = df["cod"].astype(str)
    return df[["nivel", "cod", "local", "ano", "var", "item_cod", "item", "unidade", "valor"]]


@st.cache_data(ttl=TTL_IBGE, show_spinner=False)
def pam_regiao(muns_regiao, ufs):
    """Todas as culturas da PAM, últimos 10 anos, para os municípios da região e as UFs.
    Devolve (tabela larga, data da consulta)."""
    t0 = time.time()
    vars_ = ",".join(VARS_PAM)
    a, b = _sidra(
        (TAB_PAM, "6", ",".join(muns_regiao), vars_, "last 10", {"782": "all"}),
        (TAB_PAM, "3", ",".join(ufs), vars_, "last 10", {"782": "all"}),
    )
    df = _tidy(pd.concat([a, b], ignore_index=True))
    df = df[df["item_cod"].astype(str) != "0"]  # linha "Total"
    unid_rend = (df[df["var"] == "112"].groupby("item_cod")["unidade"].first())
    larga = (df.pivot_table(index=["nivel", "cod", "local", "ano", "item_cod", "item"],
                            columns="var", values="valor", aggfunc="first")
               .rename(columns=VARS_PAM).reset_index())
    for c in VARS_PAM.values():
        if c not in larga:
            larga[c] = float("nan")
    larga["item"] = larga["item"].str.replace("*", "", regex=False).str.strip()
    larga["un_rend"] = larga["item_cod"].map(unid_rend).map(lambda u: UNIDADES.get(u, u))
    print(f"[mercado] PAM {len(muns_regiao)} muns + {ufs}: {time.time() - t0:.1f}s", flush=True)
    if larga.empty:
        raise RuntimeError("IBGE devolveu a PAM vazia para esta região")
    return larga, pd.Timestamp.now().strftime("%d/%m/%Y")


@st.cache_data(ttl=TTL_IBGE, show_spinner=False)
def bovinos_regiao(muns_regiao, ufs):
    a, b = _sidra(
        (TAB_PPM, "6", ",".join(muns_regiao), VAR_PPM_EFETIVO, "last", {"79": COD_BOVINO}),
        (TAB_PPM, "3", ",".join(ufs), VAR_PPM_EFETIVO, "last", {"79": COD_BOVINO}),
    )
    df = _tidy(pd.concat([a, b], ignore_index=True))
    if df.empty:
        raise RuntimeError("IBGE devolveu a PPM vazia para esta região")
    return df[["nivel", "cod", "local", "ano", "valor"]]


# =========================================================================
# Escopo geográfico
# =========================================================================
def _centroide(geometry, source_name):
    """Centroide do imóvel (1 ida ao Earth Engine), guardado por imóvel na sessão."""
    chave = f"mercado_centroide::{source_name}"
    if chave not in st.session_state:
        st.session_state[chave] = geometry.centroid(1).coordinates().getInfo()
    return st.session_state[chave]


def _regiao(muns):
    """Municípios das regiões imediatas (IBGE 2017) dos municípios do imóvel."""
    info = utils._municipios_info()
    alvo = set()
    for m in muns:
        r = info.get(m["cod_ibge"])
        if r:
            alvo.add((m["cod_ibge"][:2], r["regiao_imediata"]))
    cods = sorted(c for c, r in info.items() if (c[:2], r["regiao_imediata"]) in alvo)
    nomes = sorted({n for _, n in alvo})
    return cods or [m["cod_ibge"] for m in muns], nomes


def _pesos(muns, modo):
    """{cod: peso} — ponderado pela área, ou só o predominante."""
    if len(muns) == 1 or muns[0]["porcentagem"] >= CORTE_UNICO or modo == "pred":
        return {muns[0]["cod_ibge"]: 1.0}
    tot = sum(m["porcentagem"] for m in muns) or 1
    return {m["cod_ibge"]: m["porcentagem"] / tot for m in muns}


def _media_ponderada(valores, pesos):
    """Média de taxas com pesos renormalizados sobre os municípios que têm o dado."""
    pares = [(valores[c], w) for c, w in pesos.items()
             if c in valores and pd.notna(valores[c]) and valores[c] > 0]
    tw = sum(w for _, w in pares)
    return sum(v * w for v, w in pares) / tw if tw else float("nan")


def _rend_regiao(df):
    """Produtividade da região = Σ(rend × área) ÷ Σ área (equivale a Σprodução ÷ Σárea)."""
    d = df[(df["area_colhida"] > 0) & (df["rendimento"] > 0)]
    return (d["rendimento"] * d["area_colhida"]).sum() / d["area_colhida"].sum() if len(d) else float("nan")


# =========================================================================
# Tela
# =========================================================================
def _bloco_escopo(muns, nomes_reg, n_reg):
    ui.secao("Onde o imóvel está")
    if len(muns) == 1:
        m = muns[0]
        st.markdown(f"**{m['municipio']}** · 100% · {_br(m['area_ha'], 0)} ha")
    else:
        for m in muns:
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"**{m['municipio']}**")
            c1.progress(min(int(round(m["porcentagem"])), 100) / 100.0)
            c2.markdown(f"<div style='text-align:right;padding-top:6px;color:#5a6b62'>"
                        f"{_br(m['porcentagem'], 1)}% · {_br(m['area_ha'], 0)} ha</div>",
                        unsafe_allow_html=True)
    modo = "pond"
    if len(muns) > 1 and muns[0]["porcentagem"] < CORTE_UNICO:
        escolha = st.radio(
            "Como calcular as taxas do imóvel (kg/ha):",
            ["Ponderar pela área do imóvel", f"Só o predominante ({muns[0]['nome_puro']})"],
            horizontal=True, key="mercado_modo")
        modo = "pond" if escolha.startswith("Ponderar") else "pred"
    elif len(muns) > 1:
        st.caption(f"{muns[0]['nome_puro']} tem {_br(muns[0]['porcentagem'], 1)}% da área "
                   f"e vale sozinho nas taxas (corte de {_br(CORTE_UNICO, 0)}%).")
    reg = " e ".join(nomes_reg) if nomes_reg else "–"
    st.caption(f"Comparação com {'as regiões imediatas' if len(nomes_reg) > 1 else 'a região imediata'} "
               f"de {reg} (IBGE 2017, {n_reg} municípios) e com o estado.")
    return modo


def _tabela_producao(pam, muns, pesos, regiao, uf_ref, ano, culturas):
    d_ano = pam[pam["ano"] == ano]
    linhas = []
    for item in culturas:
        d = d_ano[d_ano["item"] == item]
        mun = d[d["nivel"] == "6"].set_index("cod")
        rend_m = mun["rendimento"].to_dict()
        imovel = _media_ponderada(rend_m, pesos)
        reg = _rend_regiao(d[(d["nivel"] == "6") & d["cod"].isin(regiao)])
        ufv = d[(d["nivel"] == "3") & (d["cod"] == uf_ref)]["rendimento"]
        ufv = ufv.iloc[0] if len(ufv) else float("nan")
        un = d["un_rend"].dropna().iloc[0] if d["un_rend"].notna().any() else ""
        linha = {"Cultura": item}
        for m in muns:
            linha[f"Área colhida {m['nome_puro']} (ha)"] = _br(mun["area_colhida"].get(m["cod_ibge"]))
        linha.update({
            f"Imóvel": _br(imovel), "Região": _br(reg), f"{utils.UF_POR_CODIGO.get(uf_ref, 'UF')}": _br(ufv),
            "Unidade": un,
            "vs região": _pct((imovel / reg - 1) * 100) if reg and pd.notna(reg) and pd.notna(imovel) else "–",
        })
        linhas.append(linha)
    return pd.DataFrame(linhas)


def _grafico(pam, item, pesos, regiao, uf_ref, rotulo_imovel):
    d = pam[pam["item"] == item]
    anos = sorted(d["ano"].dropna().unique())
    s_im, s_reg, s_uf = [], [], []
    for a in anos:
        da = d[d["ano"] == a]
        s_im.append(_media_ponderada(da[da["nivel"] == "6"].set_index("cod")["rendimento"].to_dict(), pesos))
        s_reg.append(_rend_regiao(da[(da["nivel"] == "6") & da["cod"].isin(regiao)]))
        u = da[(da["nivel"] == "3") & (da["cod"] == uf_ref)]["rendimento"]
        s_uf.append(u.iloc[0] if len(u) else None)
    un = d["un_rend"].dropna().iloc[0] if d["un_rend"].notna().any() else ""
    fig = go.Figure()
    fig.add_scatter(x=anos, y=s_uf, name=utils.UF_POR_CODIGO.get(uf_ref, "UF"), mode="lines",
                    line=dict(color="#9aa7a0", width=2, dash="dash"))
    fig.add_scatter(x=anos, y=s_reg, name="Região", mode="lines", line=dict(color=ui.CHUMBO, width=2))
    fig.add_scatter(x=anos, y=s_im, name=rotulo_imovel, mode="lines+markers",
                    line=dict(color=ui.VERDE, width=3), marker=dict(size=6))
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10), separators=",.",
                      legend=dict(orientation="h", yanchor="bottom", y=-0.25, x=0),
                      yaxis_title=un, xaxis=dict(dtick=1), hovermode="x unified")
    return fig


def _bloco_pecuaria(muns, regiao, uf_ref, ufs):
    ui.secao("Pecuária · rebanho bovino")
    try:
        with st.spinner("Consultando IBGE (pecuária)..."):
            df = bovinos_regiao(tuple(regiao), tuple(ufs))
    except Exception as e:
        ui.erro("Não foi possível consultar a pecuária no IBGE agora.", str(e)[:300])
        return
    ano = int(df["ano"].max())
    mun = df[df["nivel"] == "6"].set_index("cod")["valor"]
    linhas = [{"Local": m["municipio"], "Cabeças": _br(mun.get(m["cod_ibge"]))} for m in muns]
    linhas.append({"Local": f"Região ({len(regiao)} municípios)", "Cabeças": _br(mun[mun.index.isin(regiao)].sum())})
    u = df[(df["nivel"] == "3") & (df["cod"] == uf_ref)]["valor"]
    linhas.append({"Local": utils.UF_POR_CODIGO.get(uf_ref, "UF"), "Cabeças": _br(u.iloc[0] if len(u) else None)})
    st.dataframe(pd.DataFrame(linhas), hide_index=True, use_container_width=True)
    st.caption(f"Fonte: IBGE, Pesquisa da Pecuária Municipal (tabela {TAB_PPM}), {ano}. "
               "Totais não se ponderam: cada município aparece com o seu número.")


def render_tab():
    geometry = st.session_state.get("current_geometry")
    source_name = st.session_state.get("source_name", "Desconhecido")
    if not geometry:
        ui.vazio()
        return

    ui.barra_imovel(nome=source_name)

    # Mesma chave da aba Contexto -> reaproveita o cache do cruzamento municipal.
    with st.spinner("Identificando os municípios do imóvel..."):
        lon, lat = _centroide(geometry, source_name)
        muns = utils.obter_municipios_interseccao(geometry, f"{source_name}_{lat}_{lon}")
    if not muns or "erro" in muns[0]:
        ui.erro("Não foi possível identificar os municípios do imóvel.",
                muns[0].get("erro") if muns else "sem retorno")
        return

    regiao, nomes_reg = _regiao(muns)
    ufs = sorted({m["cod_ibge"][:2] for m in muns})
    uf_ref = muns[0]["cod_ibge"][:2]          # UF do município predominante

    with st.container(border=True):
        modo = _bloco_escopo(muns, nomes_reg, len(regiao))
    pesos = _pesos(muns, modo)
    rotulo_imovel = ("Imóvel (ponderado pela área)" if len(pesos) > 1
                     else f"Imóvel ({next(m['nome_puro'] for m in muns if m['cod_ibge'] in pesos)})")

    # ---------------- Produção agrícola ----------------
    try:
        with st.spinner("Consultando IBGE (produção agrícola dos municípios da região)... "
                        "A primeira consulta da região leva alguns segundos."):
            pam, consultado = pam_regiao(tuple(regiao), tuple(ufs))
    except Exception as e:
        ui.erro("Não foi possível consultar a produção agrícola no IBGE agora. "
                "Tente de novo em instantes.", str(e)[:300])
        if st.button("Tentar de novo", key="mercado_retry"):
            st.rerun()
        return

    mun6 = pam[(pam["nivel"] == "6") & pam["area_colhida"].notna()]
    anos_validos = mun6[mun6["area_colhida"] > 0]["ano"]
    if anos_validos.empty:
        st.info("O IBGE não registra lavouras nos municípios do imóvel.")
        _bloco_pecuaria(muns, regiao, uf_ref, ufs)
        return
    ano = int(anos_validos.max())

    cods_imovel = [m["cod_ibge"] for m in muns]
    ranking = (mun6[(mun6["ano"] == ano) & mun6["cod"].isin(cods_imovel) & (mun6["area_colhida"] > 0)]
               .groupby("item")["area_colhida"].sum().sort_values(ascending=False))
    todas = ranking.index.tolist()
    with st.container(border=True):
        ui.secao("O que a região produz")
        culturas = st.multiselect(
            "Culturas (sugeridas: as maiores do município pela área colhida)",
            options=todas, default=todas[:N_CULTURAS], key=f"mercado_culturas::{source_name}")
        if not culturas:
            st.caption("Escolha ao menos uma cultura.")
        else:
            tab = _tabela_producao(pam, muns, pesos, regiao, uf_ref, ano, culturas)
            st.dataframe(tab, hide_index=True, use_container_width=True)
            st.caption(f"Produtividade em {ano}. Imóvel = {rotulo_imovel.split('(')[1].rstrip(')')}; "
                       "região = Σ produção ÷ Σ área colhida dos municípios da região.")

            foco = st.selectbox("Série de 10 anos da cultura:", culturas, key="mercado_foco")
            st.plotly_chart(_grafico(pam, foco, pesos, regiao, uf_ref, rotulo_imovel),
                            use_container_width=True)
        st.caption(f"Fonte: IBGE, Produção Agrícola Municipal (tabela {TAB_PAM}), {int(pam['ano'].min())}–{ano}. "
                   f"Consultado em {consultado}.")

    with st.container(border=True):
        _bloco_pecuaria(muns, regiao, uf_ref, ufs)
