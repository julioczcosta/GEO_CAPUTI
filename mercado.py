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
import re
import time
import unicodedata

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

# ---- Fase 2: preço (CEPEA) + custo (CONAB) -> renda por hectare ----
TTL_PRECO = 24 * 3600
# kg contidos em cada unidade de preço do CEPEA
KG_UNIDADE = {"BRL/sc60kg": 60, "BRL/sc50kg": 50, "BRL/ton": 1000, "BRL/t": 1000}
ROTULO_UN = {"BRL/sc60kg": "sc 60 kg", "BRL/sc50kg": "sc 50 kg", "BRL/ton": "t", "BRL/t": "t"}
# Cultura da PAM -> indicadores CEPEA possíveis (produto, praça, rótulo) e custo CONAB.
# Feijão não tem indicador CEPEA: o preço é informado pelo avaliador.
# Algodão fica de fora: a PAM mede caroço e o CEPEA/CONAB, pluma.
RENDA = {
    "Soja (em grão)": {"cepea": [("soja", "paranagua", "Paranaguá/PR (porto)"),
                                 ("soja_parana", "parana", "Paraná (média do estado)")],
                       "conab": "soja"},
    "Milho (em grão)": {"cepea": [("milho", "campinas", "Campinas/SP")], "conab": "milho"},
    "Trigo (em grão)": {"cepea": [("trigo", "parana", "Paraná"),
                                  ("trigo", "rio_grande_do_sul", "Rio Grande do Sul")],
                        "conab": "trigo"},
    "Arroz (em casca)": {"cepea": [("arroz", "rio_grande_do_sul", "Rio Grande do Sul")],
                         "conab": "arroz"},
    "Feijão (em grão)": {"cepea": [], "conab": "feijao", "unidade": "BRL/sc60kg"},
    "Café (em grão) Arábica": {"cepea": [("cafe", "sao_paulo", "São Paulo (arábica)")],
                               "conab": "cafe_arabica"},
    "Café (em grão) Canephora": {"cepea": [("cafe_robusta", "espirito_santo", "Espírito Santo (conilon)")],
                                 "conab": "cafe_conilon"},
}
AVISO_CEPEA = ("Fonte: CEPEA/ESALQ-USP. Dados sob licença CC BY-NC 4.0: cite sempre a fonte; "
               "a série não é exportada, só o valor usado na conta.")


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


@st.cache_data(ttl=TTL_PRECO, show_spinner=False)
def cepea_serie(produto, praca):
    """Indicador diário CEPEA dos últimos ~14 meses."""
    import agrobr.cepea as cepea
    inicio = (pd.Timestamp.today().normalize() - pd.Timedelta(days=430)).date()
    df = asyncio.run(cepea.indicador(produto, praca=praca, inicio=inicio))
    if df is None or len(df) == 0:
        raise RuntimeError(f"CEPEA sem dados para {produto}/{praca}")
    df = df[["data", "valor", "unidade", "praca"]].copy()
    df["data"] = pd.to_datetime(df["data"])
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    return df.dropna(subset=["valor"]).sort_values("data")


def _mensal_12(serie):
    """Médias mensais dos 12 últimos meses FECHADOS (o mês corrente fica de fora)."""
    m = serie.set_index("data")["valor"].resample("MS").mean().dropna()
    m = m[m.index < pd.Timestamp.today().normalize().replace(day=1)]
    return m.tail(12)


def _sem_acento(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().upper()


def _rotulo_planilha(nome):
    n = nome.lower()
    for padrao, rot in [(r"1a?_safra", "1ª safra"), (r"2a?_(e_3_)?safra", "2ª safra"),
                        ("irrigado", "irrigado"), ("sequeiro", "sequeiro")]:
        if re.search(padrao, n):
            return rot
    return ""


@st.cache_data(ttl=TTL_PRECO, show_spinner=False)
def conab_polos(cultura):
    """Polos da CONAB do ano mais recente de cada planilha da cultura.
    Lê só os NOMES das abas (rápido); o custo de uma aba é lido depois."""
    from agrobr.conab._custo_producao._acquisition import Acquisition
    from agrobr.conab._custo_producao._workbook import Workbook

    async def _todas():
        acq = Acquisition()
        saida = []
        for r in await acq.catalog(cultura):
            wb = Workbook(await acq.workbook(r))
            try:
                saida.append((r.planilha, list(wb.names)))
            finally:
                try:
                    wb.close()
                except Exception:
                    pass
        return saida

    t0 = time.time()
    linhas = []
    for planilha, abas in asyncio.run(_todas()):
        regs = []
        for aba in abas:
            m_ano = re.search(r"(\d{4})\s*$", aba)
            m_uf = re.search(r"(?:^|-)([A-Z]{2})(?:-|\s)", aba)
            if not (m_ano and m_uf):
                continue
            local = aba[:m_ano.start()].strip(" -")
            local = re.sub(rf"(?:^|-){m_uf.group(1)}(?:-|\s|$)", "-", local).strip(" -")
            local = re.sub(r"^(OGM-|\d+ª\s*)", "", local).strip(" -")
            regs.append({"planilha": planilha, "aba": aba, "local": local, "uf": m_uf.group(1),
                         "ano": int(m_ano.group(1)), "ogm": aba.upper().startswith("OGM"),
                         "sistema": _rotulo_planilha(planilha)})
        if regs:
            ult = max(r["ano"] for r in regs)
            linhas += [r for r in regs if r["ano"] == ult]
    print(f"[mercado] CONAB polos {cultura}: {time.time() - t0:.1f}s", flush=True)
    if not linhas:
        raise RuntimeError(f"CONAB sem planilhas de custo para {cultura}")
    return pd.DataFrame(linhas)


@st.cache_data(ttl=TTL_IBGE, show_spinner=False)
def conab_custo(cultura, planilha, aba):
    """Totais por hectare de uma aba de custo da CONAB."""
    import agrobr.conab as conab
    t0 = time.time()
    d = asyncio.run(conab.custo_producao(cultura, planilha=planilha, aba=aba))
    tot = {}
    for _, r in d[d["tipo_linha"] != "item"].iterrows():
        it = _sem_acento(r["item"])
        for chave, prefixo in [("variavel", "CUSTO VARIAVEL"), ("fixo", "CUSTO FIXO"),
                               ("operacional", "CUSTO OPERACIONAL"),
                               ("fatores", "TOTAL DE RENDA DE FATORES"), ("total", "CUSTO TOTAL")]:
            if it.startswith(prefixo):
                tot[chave] = float(r["valor_ha"])
    if "operacional" not in tot:
        raise RuntimeError(f"CONAB: custo operacional não encontrado em {aba}")
    p = d.iloc[0]
    tot.update({"safra": str(p.get("safra", "")), "referencia": str(p.get("referencia", "")),
                "local": str(p.get("local", "")), "uf": str(p.get("uf", "")),
                "tecnologia": str(p.get("tecnologia", ""))})
    print(f"[mercado] CONAB custo {aba}: {time.time() - t0:.1f}s", flush=True)
    return tot


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


def _rend_escalas(pam, item, ano, pesos, regiao, uf_ref):
    """(imóvel, região, UF, unidade, área colhida por município) de uma cultura num ano."""
    d = pam[(pam["ano"] == ano) & (pam["item"] == item)]
    mun = d[d["nivel"] == "6"].set_index("cod")
    imovel = _media_ponderada(mun["rendimento"].to_dict(), pesos)
    reg = _rend_regiao(d[(d["nivel"] == "6") & d["cod"].isin(regiao)])
    ufv = d[(d["nivel"] == "3") & (d["cod"] == uf_ref)]["rendimento"]
    ufv = ufv.iloc[0] if len(ufv) else float("nan")
    un = d["un_rend"].dropna().iloc[0] if d["un_rend"].notna().any() else ""
    return imovel, reg, ufv, un, mun["area_colhida"]


def _tabela_producao(pam, muns, pesos, regiao, uf_ref, ano, culturas):
    linhas = []
    for item in culturas:
        imovel, reg, ufv, un, area = _rend_escalas(pam, item, ano, pesos, regiao, uf_ref)
        mun = pd.DataFrame({"area_colhida": area})
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
    st.dataframe(pd.DataFrame(linhas), hide_index=True, width="stretch")
    st.caption(f"Fonte: IBGE, Pesquisa da Pecuária Municipal (tabela {TAB_PPM}), {ano}. "
               "Totais não se ponderam: cada município aparece com o seu número.")
    # Preço de referência do gado (CEPEA)
    cols = st.columns(2)
    for col, (prod, praca, nome) in zip(cols, [("boi", "sao_paulo", "Boi gordo"),
                                              ("bezerro", None, "Bezerro")]):
        try:
            s = cepea_serie(prod, praca)
            m12 = _mensal_12(s)
            un = s["unidade"].iloc[-1].replace("BRL/", "R$/").replace("cabeca", "cabeça")
            col.metric(f"{nome} · {s['praca'].iloc[-1]} · média 12 meses",
                       f"R$ {_br(m12.mean(), 2)}",
                       help=f"Último: R$ {_br(s['valor'].iloc[-1], 2)} {un} em "
                            f"{s['data'].iloc[-1]:%d/%m/%Y}. Unidade: {un}.")
        except Exception as e:
            col.caption(f"{nome}: CEPEA indisponível agora ({str(e)[:80]}).")
    st.caption(AVISO_CEPEA)


def _grafico_preco(m12, unidade):
    fig = go.Figure()
    fig.add_scatter(x=m12.index, y=m12.values, mode="lines+markers", name="Média mensal",
                    line=dict(color=ui.VERDE, width=2.5), fill="tozeroy",
                    fillcolor="rgba(0,140,69,0.08)")
    fig.add_hline(y=m12.mean(), line=dict(color=ui.CHUMBO, dash="dash", width=1),
                  annotation_text=f"média {_br(m12.mean(), 2)}", annotation_position="top left")
    lo, hi = m12.min(), m12.max()
    fig.update_layout(height=230, margin=dict(l=10, r=10, t=10, b=10), separators=",.",
                      showlegend=False, yaxis_title=unidade, yaxis_range=[lo * 0.92, hi * 1.05],
                      xaxis=dict(tickformat="%m/%Y", dtick="M2"))
    return fig


def _bloco_renda(pam, ano, culturas, pesos, regiao, uf_ref, chave):
    """Preço (CEPEA) + custo (CONAB) -> renda estimada por hectare de uma cultura."""
    ui.secao("Preço, custo e renda por hectare")
    opcoes = [c for c in culturas if c in RENDA]
    if not opcoes:
        st.info("Nenhuma das culturas escolhidas tem preço e custo de referência (soja, milho, "
                "trigo, arroz, feijão e café). Acrescente uma delas na lista acima.")
        return
    cultura = st.selectbox("Cultura:", opcoes, key=f"{chave}::renda_cultura")
    cfg = RENDA[cultura]
    k = f"{chave}::{cultura}"
    uf_sigla = utils.UF_POR_CODIGO.get(uf_ref, "")

    imovel, reg, _, _, _ = _rend_escalas(pam, cultura, ano, pesos, regiao, uf_ref)
    topo = st.container()   # números-resumo, preenchidos no fim

    c1, c2 = st.columns(2)
    # ---------------- preço ----------------
    with c1:
        st.markdown("**Preço**")
        serie, m12, unidade = None, None, cfg.get("unidade", "BRL/sc60kg")
        if cfg["cepea"]:
            rotulos = [r for _, _, r in cfg["cepea"]]
            padrao = 0
            if cultura.startswith("Soja") and uf_ref == "41":
                padrao = 1
            if cultura.startswith("Trigo") and uf_ref == "43":
                padrao = 1
            escolha = st.selectbox("Indicador CEPEA (praça):", rotulos, index=padrao, key=f"{k}::praca")
            prod, praca, _ = cfg["cepea"][rotulos.index(escolha)]
            try:
                with st.spinner("Consultando CEPEA..."):
                    serie = cepea_serie(prod, praca)
                m12 = _mensal_12(serie)
                unidade = serie["unidade"].iloc[-1]
            except Exception as e:
                ui.erro("O CEPEA não respondeu agora. Informe o preço manualmente.", str(e)[:200])
        else:
            st.caption("Não há indicador CEPEA para esta cultura: informe o preço praticado na região.")
        un_txt = ROTULO_UN.get(unidade, unidade)
        kg_un = KG_UNIDADE.get(unidade, 60)
        media = float(m12.mean()) if m12 is not None and len(m12) else 0.0
        preco = st.number_input(f"Preço de referência (R$/{un_txt})", min_value=0.0, value=round(media, 2),
                                step=1.0, format="%.2f", key=f"{k}::preco::{round(media, 2)}",
                                help="Vem preenchido com a média dos últimos 12 meses fechados do indicador.")
        desconto = st.number_input(f"Desconto até a fazenda: frete e base (R$/{un_txt})", min_value=0.0,
                                   value=0.0, step=1.0, format="%.2f", key=f"{k}::desconto",
                                   help="O indicador é o preço na praça (porto, bolsa). Na fazenda o "
                                        "produtor recebe menos: informe a diferença usada na região.")
        if serie is not None and m12 is not None and len(m12):
            st.plotly_chart(_grafico_preco(m12, f"R$/{un_txt}"), width="stretch")
            st.caption(f"Indicador {escolha}: último R$ {_br(serie['valor'].iloc[-1], 2)}/{un_txt} em "
                       f"{serie['data'].iloc[-1]:%d/%m/%Y}; média mensal de "
                       f"{m12.index[0]:%m/%Y} a {m12.index[-1]:%m/%Y}. {AVISO_CEPEA}")

    # ---------------- custo ----------------
    with c2:
        st.markdown("**Custo de produção (CONAB)**")
        custo = None
        try:
            with st.spinner("Consultando CONAB... na primeira vez leva uns 20 segundos."):
                polos = conab_polos(cfg["conab"])
            polos = polos.copy()
            polos["rot"] = polos.apply(
                lambda r: f"{r['local']}/{r['uf']}" + (f" · {r['sistema']}" if r["sistema"] else "")
                          + (" · OGM" if r["ogm"] else "") + f" · {r['ano']}", axis=1)
            mesma_uf = polos[polos["uf"] == uf_sigla]
            ordem = pd.concat([mesma_uf, polos[polos["uf"] != uf_sigla]])
            if cultura.startswith("Milho"):   # 2ª safra primeiro (soja + milho no mesmo ano)
                ordem = pd.concat([ordem[ordem["sistema"] == "2ª safra"], ordem[ordem["sistema"] != "2ª safra"]])
                ordem = pd.concat([ordem[ordem["uf"] == uf_sigla], ordem[ordem["uf"] != uf_sigla]])
            rot = st.selectbox("Polo de referência:", ordem["rot"].tolist(), key=f"{k}::polo")
            if mesma_uf.empty:
                st.caption(f"A CONAB não tem polo de {cultura.split(' (')[0].lower()} em {uf_sigla}; "
                           "escolha o mais parecido com a região.")
            sel = ordem[ordem["rot"] == rot].iloc[0]
            with st.spinner("Lendo a planilha de custo..."):
                custo = conab_custo(cfg["conab"], sel["planilha"], sel["aba"])
        except Exception as e:
            ui.erro("A CONAB não respondeu agora. Informe o custo manualmente.", str(e)[:200])
        operacional = st.number_input("Custo operacional (R$/ha)", min_value=0.0,
                                      value=round(custo["operacional"], 2) if custo else 0.0,
                                      step=50.0, format="%.2f",
                                      key=f"{k}::custo::{custo['operacional'] if custo else 0}",
                                      help="Custo variável + custo fixo, sem a remuneração da terra.")
        if custo:
            st.caption(f"CONAB, polo {custo['local']}/{custo['uf']}, safra {custo['safra']}, "
                       f"tecnologia {custo['tecnologia']}, referência {custo['referencia']}.")

    # ---------------- renda ----------------
    prod_un = imovel / kg_un if pd.notna(imovel) else float("nan")
    liquido = max(preco - desconto, 0.0)
    receita = prod_un * liquido if pd.notna(prod_un) else float("nan")
    margem_op = receita - operacional if pd.notna(receita) else float("nan")
    linhas = [
        ("Produtividade do imóvel", f"{_br(prod_un, 1)} {un_txt}/ha", f"IBGE PAM {ano} ({_br(imovel)} kg/ha)"),
        ("× Preço líquido na fazenda", f"R$ {_br(liquido, 2)}/{un_txt}", "preço − desconto"),
        ("= Receita bruta", f"R$ {_br(receita, 2)}", "por hectare"),
    ]
    if custo:
        linhas += [("− Custo variável", f"R$ {_br(custo.get('variavel'), 2)}", "CONAB"),
                   ("− Custo fixo (depreciações etc.)", f"R$ {_br(custo.get('fixo'), 2)}", "CONAB")]
    linhas += [("= Margem operacional", f"R$ {_br(margem_op, 2)}", "receita − custo operacional")]
    if custo and custo.get("fatores") is not None:
        linhas += [("Renda de fatores (inclui remuneração da terra)", f"R$ {_br(custo['fatores'], 2)}",
                    "CONAB · referência"),
                   ("= Margem sobre o custo total", f"R$ {_br(receita - operacional - custo['fatores'], 2)}",
                    "receita − custo operacional − renda de fatores")]
    st.dataframe(pd.DataFrame(linhas, columns=["", "R$ por hectare", "Origem"]),
                 hide_index=True, width="stretch")
    st.caption("A renda de fatores da CONAB já embute uma remuneração da terra; para avaliar a terra "
               "pela renda, a margem operacional é a base mais limpa. Todos os valores acima podem "
               "ser alterados nos campos.")

    with topo:
        a, b, c, d = st.columns(4)
        dif = (imovel / reg - 1) * 100 if pd.notna(imovel) and pd.notna(reg) and reg else None
        a.metric(f"Produtividade · {cultura.split(' (')[0]}", f"{_br(prod_un, 1)} {un_txt}/ha",
                 delta=(_pct(dif) + " vs região") if dif is not None else None)
        b.metric("Preço · média 12 meses", f"R$ {_br(preco, 2)}", help=f"por {un_txt}")
        c.metric("Custo operacional", f"R$ {_br(operacional, 2)}", help="por hectare")
        d.metric("Margem operacional", f"R$ {_br(margem_op, 2)}",
                 help="por hectare" + ("" if desconto else " · sem desconto de frete"))


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
    # "Café (em grão) Total" soma arábica + canephora: fica fora para não contar duas vezes.
    todas = [i for i in ranking.index if not i.endswith(" Total")]
    with st.container(border=True):
        ui.secao("O que a região produz")
        culturas = st.multiselect(
            "Culturas (sugeridas: as maiores do município pela área colhida)",
            options=todas, default=todas[:N_CULTURAS], key=f"mercado_culturas::{source_name}")
        if not culturas:
            st.caption("Escolha ao menos uma cultura.")
        else:
            tab = _tabela_producao(pam, muns, pesos, regiao, uf_ref, ano, culturas)
            st.dataframe(tab, hide_index=True, width="stretch")
            st.caption(f"Produtividade em {ano}. Imóvel = {rotulo_imovel.split('(')[1].rstrip(')')}; "
                       "região = Σ produção ÷ Σ área colhida dos municípios da região.")

            foco = st.selectbox("Série de 10 anos da cultura:", culturas, key="mercado_foco")
            st.plotly_chart(_grafico(pam, foco, pesos, regiao, uf_ref, rotulo_imovel),
                            width="stretch")
        st.caption(f"Fonte: IBGE, Produção Agrícola Municipal (tabela {TAB_PAM}), {int(pam['ano'].min())}–{ano}. "
                   f"Consultado em {consultado}.")

    if culturas:
        with st.container(border=True):
            _bloco_renda(pam, ano, culturas, pesos, regiao, uf_ref, f"mercado::{source_name}")

    with st.container(border=True):
        _bloco_pecuaria(muns, regiao, uf_ref, ufs)
