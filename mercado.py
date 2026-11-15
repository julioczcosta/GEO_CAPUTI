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

A aba é dividida em sub-abas preguiçosas (só a aberta roda): Produção (IBGE +
crédito de custeio do Banco Central), Renda e juros (CEPEA, CONAB, Selic/IPCA/
Focus e valor de referência pela renda), Risco (ZARC + seguro rural do MAPA),
Pecuária e Censo. O texto do laudo/Excel está guardado atrás de MOSTRAR_LAUDO.
"""

import asyncio
import logging
import re
import time
import unicodedata
import warnings
from urllib.parse import quote

import pandas as pd
import requests
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

# ---- Juros (Banco Central), crédito rural (SICOR) e seguro rural (MAPA/PSR) ----
TTL_BCB = 24 * 3600
TTL_SEGURO = 30 * 24 * 3600
OLINDA = "https://olinda.bcb.gov.br/olinda/servico"
SGS_SELIC_META = 432        # meta Selic, % a.a.
SGS_IPCA_12M = 13522        # IPCA acumulado em 12 meses
ANOS_CREDITO = 3            # anos fechados de crédito de custeio
ANOS_SEGURO = 5             # anos de apólices do seguro rural
N_PRODUTOS_CREDITO = 8
# Custeio pecuário traz "área", mas R$/ha não faz sentido para rebanho.
SEM_POR_HA = {"BOVINOS", "BUBALINOS", "SUINOS", "AVES", "FRANGOS", "OVINOS", "CAPRINOS", "EQUINOS",
              "PEIXES", "LEITE", "AQUISICAO"}

# O texto do laudo e o Excel (fase 4) ficam guardados, fora da tela, até a equipe
# decidir como vai usá-los. Para religar: MOSTRAR_LAUDO = True.
MOSTRAR_LAUDO = False


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


def _col_classe(df, codigo):
    """Nome da coluna DnC (SIDRA) que contém a categoria `codigo` (ex.: '110087')."""
    for c in df.columns:
        if re.fullmatch(r"D\dC", c) and codigo in set(df[c].astype(str)):
            return c
    raise RuntimeError(f"classificação com a categoria {codigo} não encontrada")


@st.cache_data(ttl=30 * 24 * 3600, show_spinner=False)
def censo_perfil(muns_regiao, ufs):
    """Censo Agro 2017: uso das terras (tab. 6881) e grupos de área total (tab. 6754),
    para os municípios da região e as UFs. Devolve (uso, tamanho) em formato longo."""
    t0 = time.time()
    uso_cls = {"829": "46302", "222": "all", "218": "46502", "12517": "113601", "12567": "41151"}
    tam_cls = {"829": "46302", "221": "111195", "12517": "113601", "220": "all"}
    a6, a3, b6, b3 = _sidra(
        ("6881", "6", ",".join(muns_regiao), "184", "2017", uso_cls),
        ("6881", "3", ",".join(ufs), "184", "2017", uso_cls),
        ("6754", "6", ",".join(muns_regiao), "183,184", "2017", tam_cls),
        ("6754", "3", ",".join(ufs), "183,184", "2017", tam_cls),
    )

    def longo(df, cod_total):
        col = _col_classe(df, cod_total)
        out = pd.DataFrame({"nivel": df["NC"].astype(str), "cod": df["D1C"].astype(str),
                            "var": df["D3C"].astype(str), "cat_cod": df[col].astype(str),
                            "cat": df[col.replace("C", "N")].astype(str),
                            "valor": df["V"].map(_num_sidra)})
        return out

    uso = longo(pd.concat([a6, a3], ignore_index=True), "110087")
    tam = longo(pd.concat([b6, b3], ignore_index=True), "110085")
    print(f"[mercado] Censo {len(muns_regiao)} muns: {time.time() - t0:.1f}s", flush=True)
    if uso.empty:
        raise RuntimeError("IBGE devolveu o Censo Agro vazio")
    return uso, tam


def _grupo_uso(cat):
    c = _sem_acento(cat)
    if c.startswith("TOTAL"):
        return "Total"
    if c.startswith("LAVOURAS"):
        return "Lavouras"
    if c.startswith("PASTAGENS"):
        return "Pastagens"
    if c.startswith("MATAS") and "PLANTADAS" in c:
        return "Florestas plantadas"
    if c.startswith("MATAS"):
        return "Matas e florestas naturais"
    if c.startswith("SISTEMAS AGROFLORESTAIS"):
        return "Sistemas agroflorestais"
    return "Outros usos"


FAIXAS_AREA = [(0, 50, "Menos de 50 ha"), (50, 200, "50 a 200 ha"), (200, 1000, "200 a 1.000 ha"),
               (1000, 2500, "1.000 a 2.500 ha"), (2500, float("inf"), "2.500 ha ou mais")]


def _faixa_area(cat):
    """Faixa a partir do limite inferior do grupo do IBGE ('De 1.000 a menos de 2.500 ha')."""
    c = _sem_acento(cat)
    if c.startswith("TOTAL") or "SEM AREA" in c:
        return None
    m = re.search(r"\d[\d.]*(?:,\d+)?", cat)
    if not m:
        return None
    lo = float(m.group(0).replace(".", "").replace(",", "."))
    if c.startswith("MAIS DE 0"):
        lo = 0.0
    for a, b, rot in FAIXAS_AREA:
        if a <= lo < b:
            return rot
    return None


# ---- ZARC (MAPA) ----
# O agrobr carrega a tábua nacional inteira na memória (~2 GB, ~100 s): inviável no
# Streamlit Cloud. Aqui o CSV oficial (o mesmo que o agrobr usa) é lido EM PEDAÇOS,
# só com as colunas necessárias, e gravado num parquet temporário (~8 MB):
# ~10 s e ~300 MB de pico, uma vez por safra enquanto o servidor estiver de pé.
ZARC_CKAN = ("https://dados.agricultura.gov.br/api/3/action/package_show"
             "?id=tabua-de-risco-zoneamento-agricola-de-risco-climatico")
ZARC_DEC = [f"dec{i}" for i in range(1, 37)]
ZARC_COLS = ["Nome_cultura", "Cod_Ciclo", "Cod_Solo", "geocodigo", "Nome_Clima",
             "Nome_Outros_Manejos", "Portaria"] + ZARC_DEC


def _zarc_recursos():
    import requests
    r = requests.get(ZARC_CKAN, timeout=60, headers={"User-Agent": "GEOCAPUTI/1.0"})
    r.raise_for_status()
    rec = [x for x in r.json()["result"]["resources"] if str(x.get("format", "")).upper() == "CSV"]
    safras = [(re.search(r"(\d{4})/(\d{4})", x["name"]), x) for x in rec]
    safras = [(int(m.group(1)), x) for m, x in safras if m]
    if not safras:
        raise RuntimeError("catálogo do ZARC sem tábuas por safra")
    safras.sort(key=lambda s: s[0], reverse=True)
    anual = safras[0][1]
    anterior = safras[1][1] if len(safras) > 1 else anual
    perene = next((x for x in rec if "PERENE" in _sem_acento(x["name"])), None)
    return {"anual": (anual["name"], anual["url"]), "anterior": (anterior["name"], anterior["url"]),
            "perene": (perene["name"], perene["url"]) if perene else None}


@st.cache_resource(ttl=7 * 24 * 3600, show_spinner=False)
def zarc_tabua(tipo):
    """Baixa e compacta a tábua de risco ('anual' = safra mais recente, 'perene').
    Devolve (caminho do parquet, nome da tábua). Fica no disco temporário do servidor."""
    import os
    import tempfile
    import pyarrow as pa
    import pyarrow.parquet as pq
    import requests

    t0 = time.time()
    nome, url = _zarc_recursos()[tipo]
    destino = os.path.join(tempfile.gettempdir(), f"geocaputi_zarc_{tipo}_{re.sub(r'[^0-9]', '', nome) or 'x'}.parquet")
    # Tábuas antigas têm células vazias: lê como float e converte depois.
    dt = {c: "float32" for c in ZARC_DEC + ["Cod_Ciclo", "Cod_Solo", "geocodigo"]}
    escritor, n = None, 0
    with requests.get(url, stream=True, timeout=300, headers={"User-Agent": "GEOCAPUTI/1.0"}) as r:
        r.raise_for_status()
        r.raw.decode_content = True
        for ch in pd.read_csv(r.raw, sep=";", encoding="utf-8-sig", usecols=ZARC_COLS, dtype=dt,
                              chunksize=300_000):
            ch = ch.dropna(subset=["geocodigo"])
            ch[ZARC_DEC] = ch[ZARC_DEC].fillna(0).astype("int8")
            ch[["Cod_Ciclo", "Cod_Solo"]] = ch[["Cod_Ciclo", "Cod_Solo"]].fillna(0).astype("int16")
            ch["geocodigo"] = ch["geocodigo"].astype("int32")
            for c in ["Nome_cultura", "Nome_Clima", "Nome_Outros_Manejos", "Portaria"]:
                ch[c] = ch[c].astype(str)
            tb = pa.Table.from_pandas(ch, preserve_index=False)
            if escritor is None:
                escritor = pq.ParquetWriter(destino, tb.schema, compression="zstd")
            escritor.write_table(tb)
            n += len(ch)
    if escritor is None:
        raise RuntimeError(f"tábua do ZARC vazia: {nome}")
    escritor.close()
    print(f"[mercado] ZARC {tipo} ({nome}): {n} linhas em {time.time() - t0:.1f}s", flush=True)
    return destino, nome


def zarc_municipios(cods, tipo):
    import pyarrow.parquet as pq
    caminho, nome = zarc_tabua(tipo)
    d = pq.read_table(caminho, filters=[("geocodigo", "in", [int(c) for c in cods])]).to_pandas()
    return d, nome


def _zarc_variantes(nomes_csv, item_pam):
    """Nomes de cultura do ZARC que correspondem a uma cultura da PAM."""
    alvo = _sem_acento(item_pam.split(" (")[0]).split()[0]   # SOJA, MILHO, CAFE...
    sub = _sem_acento(item_pam)
    out = []
    for n in nomes_csv:
        nn = _sem_acento(n)
        if not nn.startswith(alvo):
            continue
        if alvo == "CAFE":
            if "ARABICA" in sub and "ARABICA" not in nn:
                continue
            if "CANEPHORA" in sub and not any(k in nn for k in ("CANEF", "CONILON", "ROBUSTA")):
                continue
        if alvo == "SORGO" and "FORRAG" in nn and "FORRAG" not in sub:
            continue
        out.append(n)
    return sorted(out)


def _rotulo_solo(c):
    c = int(c)
    if 11 <= c <= 16:
        return f"AD{c - 10} (código {c})"
    return f"Tipo {c}"


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
        return None
    ano = int(df["ano"].max())
    mun = df[df["nivel"] == "6"].set_index("cod")["valor"]
    u = df[(df["nivel"] == "3") & (df["cod"] == uf_ref)]["valor"]
    num = [(m["municipio"], mun.get(m["cod_ibge"])) for m in muns]
    num += [(f"Região ({len(regiao)} municípios)", mun[mun.index.isin(regiao)].sum()),
            (utils.UF_POR_CODIGO.get(uf_ref, "UF"), u.iloc[0] if len(u) else None)]
    res = {"ano": ano, "bovinos": num, "precos": []}
    st.dataframe(pd.DataFrame([{"Local": l, "Cabeças": _br(v)} for l, v in num]),
                 hide_index=True, width="stretch")
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
            res["precos"].append({"nome": nome, "praca": s["praca"].iloc[-1], "unidade": un,
                                  "media": float(m12.mean()), "periodo": f"{m12.index[0]:%m/%Y} a {m12.index[-1]:%m/%Y}",
                                  "ultimo": float(s["valor"].iloc[-1]), "data": f"{s['data'].iloc[-1]:%d/%m/%Y}"})
        except Exception as e:
            col.caption(f"{nome}: CEPEA indisponível agora ({str(e)[:80]}).")
    st.caption(AVISO_CEPEA)
    return res


CORES_RISCO = {0: "#e3e8e5", 20: "#008C45", 30: "#8fbf3a", 40: "#e0a020"}
MESES_PT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def _faixa_zarc(decs, rotulo):
    """Faixa HTML de 36 decêndios, de julho a junho (safra contínua)."""
    ordem = [(k + 18) % 36 for k in range(36)]
    meses = "".join(f"<span style='grid-column:span 3;font-size:.68rem;color:#5a6b62;"
                    f"border-left:1px solid #dfe5e1;padding-left:2px'>{MESES_PT[(k + 6) % 12]}</span>"
                    for k in range(12))
    cel = "".join(
        f"<i title='{['1–10', '11–20', '21–fim'][ix % 3]} {MESES_PT[ix // 3]}: "
        f"{('risco ' + str(int(decs[ix])) + '%') if decs[ix] else 'não indicado'}' "
        f"style='display:block;height:20px;border-radius:2px;background:{CORES_RISCO.get(int(decs[ix]), '#e3e8e5')}'></i>"
        for ix in ordem)
    return (f"<div style='font-size:.82rem;font-weight:600;margin:6px 0 2px'>{rotulo}</div>"
            f"<div style='display:grid;grid-template-columns:repeat(36,minmax(0,1fr));gap:2px'>{meses}{cel}</div>")


MESES_LONGOS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
                "setembro", "outubro", "novembro", "dezembro"]
_ULTIMO_DIA = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _janela_texto(decs, longo=False):
    ind = [(k + 18) % 36 for k in range(36) if decs[(k + 18) % 36] > 0]
    if not ind:
        return "sem decêndio indicado para plantio"
    meses = MESES_LONGOS if longo else MESES_PT
    d = lambda ix: f"{['1º', '11', '21'][ix % 3]} de {meses[ix // 3]}"
    fim = lambda ix: (f"{['10', '20', str(_ULTIMO_DIA[ix // 3])][ix % 3]} de {meses[ix // 3]}" if longo
                      else f"{['10', '20', 'fim'][ix % 3]} de {meses[ix // 3]}")
    baixos = sum(1 for ix in ind if decs[ix] == 20)
    return (f"de {d(ind[0])} a {fim(ind[-1])} ({len(ind)} decêndios, "
            f"{baixos} com risco de 20%)")


def _bloco_zarc(muns, culturas, chave):
    ui.secao("Risco climático e janela de plantio · ZARC")
    cods = [m["cod_ibge"] for m in muns]
    perenes = any(_sem_acento(c).startswith(("CAFE", "CANA", "LARANJA", "BANANA", "CACAU")) for c in culturas)
    try:
        with st.spinner("Carregando o zoneamento do MAPA... na primeira vez leva uns 10 segundos."):
            partes, nomes_tab = [], []
            for tipo in ["anual"] + (["perene"] if perenes else []):
                d, nome = zarc_municipios(cods, tipo)
                partes.append(d)
                nomes_tab.append(nome)
            z = pd.concat(partes, ignore_index=True)
            # Culturas de inverno (trigo, aveia...) às vezes só estão na tábua da safra anterior.
            nomes_csv = sorted(z["Nome_cultura"].unique())
            faltam = [c for c in culturas if not _zarc_variantes(nomes_csv, c)]
            if faltam:
                d, nome = zarc_municipios(cods, "anterior")
                d = d[d["Nome_cultura"].isin([v for c in faltam for v in
                                              _zarc_variantes(sorted(d["Nome_cultura"].unique()), c)])]
                if not d.empty:
                    z = pd.concat([z, d], ignore_index=True)
                    nomes_tab.append(nome)
    except Exception as e:
        ui.erro("Não foi possível carregar o zoneamento (ZARC) agora.", str(e)[:300])
        return None
    nomes_csv = sorted(z["Nome_cultura"].unique())
    opcoes = {c: _zarc_variantes(nomes_csv, c) for c in culturas}
    opcoes = {c: v for c, v in opcoes.items() if v}
    if not opcoes:
        st.info("O ZARC não tem zoneamento para as culturas escolhidas neste município.")
        return None
    c1, c2 = st.columns(2)
    cultura = c1.selectbox("Cultura:", list(opcoes), key=f"{chave}::zarc_cultura")
    vs = opcoes[cultura]
    padrao = next((i for i, v in enumerate(vs) if "PRODUC" in _sem_acento(v)), 0)  # perene: produção
    variante = c2.selectbox("Zoneamento:", vs, index=padrao, key=f"{chave}::zarc_var::{cultura}")
    d = z[z["Nome_cultura"] == variante]
    combos = d[["Nome_Clima", "Nome_Outros_Manejos"]].drop_duplicates()
    filtro = d
    if len(combos) > 1:
        rot = combos.apply(lambda r: " · ".join(x for x in r if x and x.lower() not in ("nan", "não se aplica")) or "padrão", axis=1)
        esc = st.selectbox("Condição:", rot.tolist(), key=f"{chave}::zarc_cond::{variante}")
        sel = combos.iloc[rot.tolist().index(esc)]
        filtro = d[(d["Nome_Clima"] == sel["Nome_Clima"]) & (d["Nome_Outros_Manejos"] == sel["Nome_Outros_Manejos"])]
    c3, c4 = st.columns(2)
    solos = sorted(filtro["Cod_Solo"].unique())
    solo = c3.selectbox("Tipo de solo (água disponível):", solos, format_func=_rotulo_solo,
                        index=len(solos) // 2, key=f"{chave}::zarc_solo::{variante}",
                        help="Classes de capacidade de reter água: AD1 (arenoso, retém pouca) a AD6 (retém mais).")
    ciclos = sorted(filtro[filtro["Cod_Solo"] == solo]["Cod_Ciclo"].unique())
    ciclo = c4.selectbox("Ciclo da cultivar (código ZARC):", ciclos, index=len(ciclos) // 2,
                         key=f"{chave}::zarc_ciclo::{variante}")
    linhas = filtro[(filtro["Cod_Solo"] == solo) & (filtro["Cod_Ciclo"] == ciclo)]
    html, janelas = "", {}
    for m in muns:
        lm = linhas[linhas["geocodigo"] == int(m["cod_ibge"])]
        if lm.empty:
            html += f"<div style='font-size:.82rem;margin:6px 0'>{m['municipio']}: sem indicação para esta combinação.</div>"
            continue
        decs = lm[ZARC_DEC].max().astype(int).tolist()
        janelas[m["municipio"]] = decs
        html += _faixa_zarc(decs, m["municipio"])
    legenda = "".join(f"<span style='margin-right:12px'><i style='display:inline-block;width:11px;height:11px;"
                      f"border-radius:2px;background:{cor};vertical-align:-1px;margin-right:4px'></i>{rot}</span>"
                      for cor, rot in [(CORES_RISCO[20], "risco 20%"), (CORES_RISCO[30], "30%"),
                                       (CORES_RISCO[40], "40%"), (CORES_RISCO[0], "não indicado")])
    st.markdown(html + f"<div style='font-size:.75rem;color:#5a6b62;margin-top:6px'>{legenda}"
                       f"<span style='float:right'>eixo de julho a junho</span></div>",
                unsafe_allow_html=True)
    for mun, decs in janelas.items():
        st.caption(f"{mun}: janela {_janela_texto(decs)}.")
    if len(janelas) > 1 and len({tuple(v) for v in janelas.values()}) > 1:
        st.warning("Os municípios do imóvel têm janelas diferentes. O zoneamento não se pondera: "
                   "vale o do município onde fica cada talhão.")
    port = ", ".join(sorted(linhas["Portaria"].unique())[:3]).replace("_", " ").replace("Port.", "Portaria ")
    st.caption(f"Fonte: MAPA, Zoneamento Agrícola de Risco Climático ({'; '.join(nomes_tab)}). {port}. "
               "Risco = chance de perda por evento climático; crédito rural, Proagro e seguro "
               "subvencionado exigem plantio dentro da janela.")
    return {"cultura": cultura, "variante": variante, "solo": _rotulo_solo(solo), "ciclo": int(ciclo),
            "janelas": janelas, "portaria": port, "tabuas": "; ".join(nomes_tab)}


def _bloco_censo(muns, regiao, uf_ref, ufs):
    ui.secao("Perfil do campo no município · Censo Agro 2017")
    try:
        with st.spinner("Consultando IBGE (Censo Agro)..."):
            uso, tam = censo_perfil(tuple(regiao), tuple(ufs))
    except Exception as e:
        ui.erro("Não foi possível consultar o Censo Agro no IBGE agora.", str(e)[:300])
        return None
    uf_nome = utils.UF_POR_CODIGO.get(uf_ref, "UF")
    res = {"uso": {}, "tamanho": {}}
    colunas = [(m["nome_puro"], [m["cod_ibge"]], "6") for m in muns]
    colunas += [(f"Região ({len(regiao)} mun.)", list(regiao), "6"), (uf_nome, [uf_ref], "3")]

    # --- uso das terras: % da área dos estabelecimentos ---
    u = uso[uso["var"] == "184"].copy()
    u["grupo"] = u["cat"].map(_grupo_uso)
    ordem = ["Lavouras", "Pastagens", "Matas e florestas naturais", "Florestas plantadas",
             "Sistemas agroflorestais", "Outros usos"]
    tab_uso = {"Uso das terras": ordem + ["Área total dos estabelecimentos"]}
    for nome, cods, nivel in colunas:
        d = u[(u["nivel"] == nivel) & u["cod"].isin(cods)]
        total = d[d["grupo"] == "Total"]["valor"].sum()
        pcts = [d[d["grupo"] == g]["valor"].sum() / total * 100 if total else None for g in ordem]
        res["uso"][nome] = {**dict(zip(ordem, pcts)), "area_total_ha": total or None}
        tab_uso[nome] = [(_br(p, 1) + "%") if p is not None else "–" for p in pcts] + \
                        [_br(total) + " ha" if total else "–"]
    st.markdown("**Uso das terras** (% da área dos estabelecimentos agropecuários)")
    st.dataframe(pd.DataFrame(tab_uso), hide_index=True, width="stretch")

    # --- tamanho: % dos estabelecimentos e da área por faixa ---
    t = tam.copy()
    t["faixa"] = t["cat"].map(_faixa_area)
    t = t.dropna(subset=["faixa"])
    faixas = [r for _, _, r in FAIXAS_AREA]
    linhas = {"Tamanho do estabelecimento": faixas}
    for nome, cods, nivel in colunas:
        d = t[(t["nivel"] == nivel) & t["cod"].isin(cods)]
        n, a = d[d["var"] == "183"], d[d["var"] == "184"]
        nt, at = n["valor"].sum(), a["valor"].sum()
        res["tamanho"][nome] = {f: (n[n["faixa"] == f]["valor"].sum() / nt * 100 if nt else None,
                                    a[a["faixa"] == f]["valor"].sum() / at * 100 if at else None)
                                for f in faixas}
        linhas[nome] = [
            (f"{_br(n[n['faixa'] == f]['valor'].sum() / nt * 100, 0)}% · "
             f"{_br(a[a['faixa'] == f]['valor'].sum() / at * 100, 0)}% da área") if nt and at else "–"
            for f in faixas]
    st.markdown("**Tamanho dos estabelecimentos** (% do número · % da área)")
    st.dataframe(pd.DataFrame(linhas), hide_index=True, width="stretch")
    st.caption("Fonte: IBGE, Censo Agropecuário 2017 (tabelas 6881 e 6754). É o retrato mais recente "
               "e completo do campo, mas é de 2017. Totais por município e região, sem ponderação.")
    return res


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


def _bloco_renda(pam, ano, culturas, pesos, regiao, uf_ref, chave, credito=None):
    """Preço (CEPEA) + custo (CONAB) -> renda estimada por hectare de uma cultura.
    `credito`: {chave da cultura: (R$/ha financiado, ano)} para a linha de referência."""
    ui.secao("Preço, custo e renda por hectare")
    opcoes = [c for c in culturas if c in RENDA]
    if not opcoes:
        st.info("Nenhuma das culturas escolhidas tem preço e custo de referência (soja, milho, "
                "trigo, arroz, feijão e café). Acrescente uma delas na lista acima.")
        return None
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
        linhas += [("− Custo variável", f"R$ {_br(custo.get('variavel'), 2)}", "CONAB")]
    ref_cred = (credito or {}).get(_chave_produto(cultura))
    if ref_cred:
        linhas += [("   Referência: custeio financiado por ha no município", f"R$ {_br(ref_cred[0], 2)}",
                    f"Banco Central · SICOR {ref_cred[1]} · compare com o custo variável")]
    if custo:
        linhas += [("− Custo fixo (depreciações etc.)", f"R$ {_br(custo.get('fixo'), 2)}", "CONAB")]
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

    fonte_preco = None
    if serie is not None and m12 is not None and len(m12):
        fonte_preco = {"indicador": escolha, "media": media, "periodo": f"{m12.index[0]:%m/%Y} a {m12.index[-1]:%m/%Y}",
                       "ultimo": float(serie["valor"].iloc[-1]), "data": f"{serie['data'].iloc[-1]:%d/%m/%Y}"}
    return {"cultura": cultura, "unidade": un_txt, "rend_kg": imovel, "rend_regiao_kg": reg, "dif_regiao": dif,
            "prod_un": prod_un, "preco": preco, "preco_editado": fonte_preco is None or abs(preco - media) > 0.005,
            "fonte_preco": fonte_preco, "desconto": desconto, "liquido": liquido, "receita": receita,
            "operacional": operacional, "custo": custo,
            "custo_editado": custo is None or abs(operacional - custo["operacional"]) > 0.005,
            "margem_op": margem_op,
            "margem_total": (receita - operacional - custo["fatores"]) if custo and custo.get("fatores") is not None else None}


# =========================================================================
# Juros, crédito rural e seguro rural
# =========================================================================
def _juro_real(nominal, inflacao):
    return ((1 + nominal / 100) / (1 + inflacao / 100) - 1) * 100


def _chave_produto(nome):
    """'Soja (em grão)' / 'SOJA' / 'Café (em grão) Arábica' / 'CAFÉ' -> 'SOJA' / 'CAFE'."""
    s = _sem_acento(str(nome)).upper().replace('"', "").strip()
    return s.split()[0] if s else ""


def _olinda(caminho, params):
    """Consulta OData do Banco Central (Olinda), com novas tentativas.

    O Olinda às vezes devolve um erro 500 passageiro ("Erro desconhecido") e só
    aceita espaço codificado como %20 (com '+' a consulta vira erro de tipo)."""
    q = "&".join(f"{k}={quote(str(v), safe=chr(39) + '(),')}" for k, v in params.items())
    url = f"{OLINDA}/{caminho}?{q}"
    erro = ""
    for tentativa in range(6):
        try:
            r = requests.get(url, timeout=120)
            txt = r.text.lstrip()
            if r.ok and txt.startswith("{"):
                return r.json()["value"]
            erro = re.sub(r"\s+", " ", txt[:200])
        except requests.RequestException as e:
            erro = str(e)[:200]
        time.sleep(min(2 * (tentativa + 1), 6))
    raise RuntimeError(f"Banco Central não respondeu: {erro}")


@st.cache_data(ttl=TTL_BCB, show_spinner=False)
def juros_bcb():
    """Selic meta e IPCA 12 meses (SGS) + medianas do Focus (Selic e IPCA anuais)."""
    from agrobr import bcb

    async def _sgs():
        return await asyncio.gather(bcb.sgs(SGS_SELIC_META, ultimos=1), bcb.sgs(SGS_IPCA_12M, ultimos=1))

    selic, ipca = asyncio.run(_sgs())
    # O Focus pelo agrobr baixa o histórico desde 2000 (40+ min): vai direto ao Olinda.
    foc = _olinda("Expectativas/versao/v1/odata/ExpectativasMercadoAnuais", {
        "$format": "json", "$top": "60", "$orderby": "Data desc",
        "$filter": "(Indicador eq 'Selic' or Indicador eq 'IPCA') and baseCalculo eq 0",
        "$select": "Indicador,Data,DataReferencia,Mediana"})
    ultima = {}
    for x in foc:
        ultima.setdefault(x["Indicador"], x["Data"])
    focus = {}
    for x in foc:
        if x["Data"] == ultima.get(x["Indicador"]):
            focus.setdefault(int(x["DataReferencia"]), {})[x["Indicador"]] = float(x["Mediana"])
    focus = {a: v for a, v in sorted(focus.items()) if "Selic" in v and "IPCA" in v}
    return {"selic": float(selic["valor"].iloc[-1]), "selic_data": pd.Timestamp(selic["data"].iloc[-1]),
            "ipca12": float(ipca["valor"].iloc[-1]), "ipca_ref": pd.Timestamp(ipca["data"].iloc[-1]),
            "focus": focus, "focus_data": pd.Timestamp(max(ultima.values())) if ultima else None}


@st.cache_data(ttl=TTL_IBGE, show_spinner=False)
def credito_custeio(cod_ibge, anos):
    """Crédito rural de custeio contratado no município (SICOR), por produto e ano.

    O agrobr só traz o SICOR por UF; por município a consulta vai direto ao Olinda
    (CusteioMunicipioProduto filtrado por codIbge), um ano por requisição."""
    def _ano(a):
        return _olinda("SICOR/versao/v2/odata/CusteioMunicipioProduto", {
            "$format": "json", "$top": "100000",
            "$filter": f"codIbge eq '{cod_ibge}' and AnoEmissao eq '{a}'",
            "$select": "nomeProduto,AnoEmissao,VlCusteio,AreaCusteio"})

    # Um ano por vez: requisições simultâneas aumentam os 500 passageiros do Olinda.
    blocos = [_ano(a) for a in anos]
    linhas = [{"ano": int(x["AnoEmissao"]), "produto": str(x["nomeProduto"]).strip('"').strip().capitalize(),
               "valor": float(x.get("VlCusteio") or 0), "area": float(x.get("AreaCusteio") or 0)}
              for b in blocos for x in b]
    df = pd.DataFrame(linhas, columns=["ano", "produto", "valor", "area"])
    return df.groupby(["ano", "produto"], as_index=False)[["valor", "area"]].sum()


@st.cache_data(ttl=TTL_SEGURO, show_spinner=False)
def seguro_uf(uf, ano_inicio):
    """Apólices do seguro rural subvencionado (MAPA/PSR) de uma UF, já resumidas.

    O agrobr baixa o arquivo nacional de cada período (~45 s, ~60 MB de pico) e
    filtra a UF; aqui guarda só os agregados por município, ano e cultura."""
    from agrobr.alt import mapa_psr

    t0 = time.time()
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        d = asyncio.run(mapa_psr.apolices(uf=uf, ano_inicio=ano_inicio))
    d = d.copy()
    d["cd_ibge"] = d["cd_ibge"].astype(str).str.replace(r"\.0$", "", regex=True)
    d["cultura"] = d["cultura"].astype(str).str.strip().str.capitalize()
    d["indenizacao"] = pd.to_numeric(d["valor_indenizacao"], errors="coerce").fillna(0.0)
    d["sinistro"] = d["indenizacao"] > 0
    resumo = (d.groupby(["cd_ibge", "ano_apolice", "cultura"])
              .agg(apolices=("nr_apolice", "count"), area=("area_total", "sum"), premio=("valor_premio", "sum"),
                   indenizacao=("indenizacao", "sum"), sinistros=("sinistro", "sum"))
              .reset_index())
    causas = (d[d["sinistro"]].groupby(["cd_ibge", "cultura", "evento"])
              .agg(n=("nr_apolice", "count"), indenizacao=("indenizacao", "sum")).reset_index())
    ultima = pd.to_datetime(d["data_apolice"], errors="coerce").max() if len(d) else None
    parcial = [str(a.message) for a in avisos if "incompleto" in str(a.message)]
    print(f"[mercado] seguro PSR {uf} desde {ano_inicio}: {time.time() - t0:.1f}s, {len(d)} apólices", flush=True)
    return resumo, causas, ultima, parcial


def _credito_imovel(muns, pesos):
    """Crédito de custeio dos municípios do imóvel + R$/ha financiado (ponderado pela área)."""
    hoje = pd.Timestamp.today()
    anos = tuple(range(hoje.year - ANOS_CREDITO, hoje.year))
    dados, erros = {}, []
    for m in muns:
        try:
            dados[m["cod_ibge"]] = credito_custeio(m["cod_ibge"], anos)
        except Exception as e:
            erros.append(f"{m['municipio']}: {str(e)[:160]}")
    ult = anos[-1]
    por_ha = {}
    for cod, df in dados.items():
        d = df[(df["ano"] == ult) & (df["area"] > 0)]
        for _, r in d.iterrows():
            k = _chave_produto(r["produto"])
            if k in SEM_POR_HA or r["valor"] / r["area"] > 50000:   # pecuária: "área" não é lavoura
                continue
            por_ha.setdefault(k, {})[cod] = r["valor"] / r["area"]
    ref = {}
    for k, vals in por_ha.items():
        v = _media_ponderada(vals, pesos) if any(c in pesos for c in vals) else float("nan")
        if pd.notna(v):
            ref[k] = (v, ult)
    return dados, ref, anos, erros


def _bloco_credito(muns, pesos):
    ui.secao("Crédito rural de custeio")
    with st.spinner("Consultando o crédito rural no Banco Central..."):
        dados, ref, anos, erros = _credito_imovel(muns, pesos)
    for e in erros:
        ui.erro("O Banco Central não respondeu para um dos municípios. Tente de novo em instantes.", e)
    if erros and st.button("Tentar de novo", key="mercado_credito_retry"):
        st.rerun()
    if not dados:
        return None
    a0, a1 = anos[0], anos[-1]
    tudo = pd.concat([df.assign(cod=c) for c, df in dados.items()], ignore_index=True)
    if tudo.empty:
        st.info(f"Não há contratos de custeio registrados no SICOR para {'os municípios' if len(muns) > 1 else 'o município'} "
                f"do imóvel entre {a0} e {a1}.")
        return None
    rank = tudo[tudo["ano"] == a1].groupby("produto")["valor"].sum().sort_values(ascending=False)
    produtos = list(rank.index[:N_PRODUTOS_CREDITO])
    linhas = []
    for p in produtos:
        linha = {"Atividade": p}
        for m in muns:
            if m["cod_ibge"] in dados:
                df = dados[m["cod_ibge"]]
                v = df[(df["ano"] == a1) & (df["produto"] == p)]["valor"].sum()
                linha[f"{m['nome_puro']} {a1} (R$ mi)"] = _br(v / 1e6, 1) if v else "–"
        t0 = tudo[(tudo["ano"] == a0) & (tudo["produto"] == p)]["valor"].sum()
        t1 = tudo[(tudo["ano"] == a1) & (tudo["produto"] == p)]["valor"].sum()
        linha[f"{a0} → {a1}"] = _pct((t1 / t0 - 1) * 100) if t0 else "–"
        r = ref.get(_chave_produto(p))
        linha["Financiado por ha (R$)" + (" · ponderado" if len(pesos) > 1 else "")] = _br(r[0]) if r else "–"
        linhas.append(linha)
    st.dataframe(pd.DataFrame(linhas), hide_index=True, width="stretch")
    st.caption(f"Valor dos contratos de custeio emitidos em {a1} no município. Mostra quanto dinheiro de "
               f"banco roda em cada atividade e se está crescendo ou caindo desde {a0}. Totais aparecem por "
               "município; só o valor por hectare é ponderado pela área do imóvel. Fonte: Banco Central, "
               "SICOR (Matriz de Dados do Crédito Rural).")
    return {"anos": anos, "ref": ref}


def _bloco_juros():
    ui.secao("Juros e inflação")
    try:
        with st.spinner("Consultando o Banco Central..."):
            j = juros_bcb()
    except Exception as e:
        ui.erro("O Banco Central não respondeu agora. Informe a taxa de desconto manualmente.", str(e)[:200])
        return None
    anos = list(j["focus"])[:5]
    longo = next((a for a in anos if a >= pd.Timestamp.today().year + 2), anos[-1] if anos else None)
    real_hoje = _juro_real(j["selic"], j["ipca12"])
    a, b, c, d = st.columns(4)
    a.metric("Selic hoje", f"{_br(j['selic'], 2)}%", help="meta ao ano")
    b.metric("IPCA 12 meses", f"{_br(j['ipca12'], 2)}%", help=f"até {j['ipca_ref']:%m/%Y}")
    c.metric("Juro real hoje", f"{_br(real_hoje, 1)}%", help="Selic descontada a inflação")
    sug = None
    if longo:
        f = j["focus"][longo]
        sug = round(_juro_real(f["Selic"], f["IPCA"]), 1)
        d.metric(f"Juro real esperado {longo}", f"{_br(sug, 1)}%",
                 help=f"Mercado (Focus): Selic {_br(f['Selic'], 2)}% e IPCA {_br(f['IPCA'], 2)}%")
        tab = pd.DataFrame(
            [["Selic (fim do ano)"] + [f"{_br(j['focus'][x]['Selic'], 2)}%" for x in anos],
             ["IPCA"] + [f"{_br(j['focus'][x]['IPCA'], 2)}%" for x in anos],
             ["Juro real"] + [f"{_br(_juro_real(j['focus'][x]['Selic'], j['focus'][x]['IPCA']), 1)}%" for x in anos]],
            columns=["Expectativa do mercado"] + [str(x) for x in anos])
        st.dataframe(tab, hide_index=True, width="stretch")
        st.caption(f"O juro real esperado para {longo} é a sugestão de taxa de desconto do bloco abaixo: um "
                   "prazo longo, sem o pico de juros do momento.")
    st.caption(f"Fonte: Banco Central do Brasil, SGS (séries {SGS_SELIC_META} e {SGS_IPCA_12M}) e boletim Focus"
               + (f" de {j['focus_data']:%d/%m/%Y}" if j["focus_data"] is not None else "") + ".")
    return {**j, "real_hoje": real_hoje, "ano_longo": longo, "taxa_sugerida": sug}


def _bloco_valor(renda, juros, area_ha, chave):
    ui.secao("Valor de referência pela renda")
    st.caption("Opcional · NBR 14653-3, método da capitalização da renda.")
    if not renda or pd.isna(renda.get("margem_op")):
        st.info("Monte a renda por hectare no bloco acima para usar esta conta.")
        return None
    st.warning("**Não é o valor do imóvel.** É uma conta de conferência: quanto vale um hectare que gera "
               "essa renda todo ano, à taxa escolhida. O valor de mercado continua vindo das amostras.")
    sug = (juros or {}).get("taxa_sugerida") or 6.0
    cultura = renda["cultura"].split(" (")[0]
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"Margem operacional · {cultura}: **R$ {_br(renda['margem_op'], 2)}/ha**")
        extra = st.number_input("+ Margem de outra safra no mesmo ano (R$/ha)", value=0.0, step=50.0,
                                format="%.2f", key=f"{chave}::valor_extra",
                                help="Ex.: milho 2ª safra depois da soja. Deixe 0 se o imóvel não faz.")
        taxa = st.number_input("Taxa de desconto (% ao ano, real)", min_value=0.5, max_value=30.0,
                               value=float(sug), step=0.1, format="%.1f", key=f"{chave}::taxa::{sug}",
                               help=("Sugestão: juro real esperado pelo mercado (Focus) para "
                                     f"{juros['ano_longo']}." if juros and juros.get("ano_longo")
                                     else "Informe a taxa usada pela equipe."))
    renda_ano = renda["margem_op"] + extra
    with c2:
        if renda_ano <= 0:
            st.info("Com renda líquida zero ou negativa, a conta pela renda não se aplica.")
            return {"renda_ano": renda_ano, "taxa": taxa, "valor_ha": None}
        valor_ha = renda_ano / (taxa / 100)
        st.metric("Valor de referência por hectare", f"R$ {_br(valor_ha, 0)}",
                  help=f"R$ {_br(renda_ano, 2)}/ha ao ano ÷ {_br(taxa, 1)}%")
        st.caption(f"× {_br(area_ha, 2)} ha do imóvel = R$ {_br(valor_ha * area_ha / 1e6, 2)} milhões")
        sens = [(t, renda_ano / (t / 100)) for t in (taxa - 1, taxa, taxa + 1) if t > 0]
        st.dataframe(pd.DataFrame({"Taxa": [f"{_br(t, 1)}%" for t, _ in sens],
                                   "R$ por ha": [_br(v, 0) for _, v in sens]}),
                     hide_index=True, width="stretch")
        st.caption("Quanto o valor muda se a taxa variar 1 ponto.")
    return {"renda_ano": renda_ano, "taxa": taxa, "valor_ha": valor_ha}


def _bloco_seguro(muns, ufs):
    ui.secao("O que deu errado de fato · seguro rural")
    hoje = pd.Timestamp.today()
    ano0 = hoje.year - ANOS_SEGURO
    cods = {m["cod_ibge"] for m in muns}
    resumos, causas, parciais, ultima = [], [], [], None
    for uf in ufs:
        sigla = utils.UF_POR_CODIGO.get(uf, uf)
        try:
            with st.spinner(f"Consultando o seguro rural (MAPA) de {sigla}... a primeira consulta do estado "
                            "leva cerca de 1 minuto e depois fica guardada por 30 dias."):
                r, c, u, p = seguro_uf(sigla, ano0)
        except Exception as e:
            ui.erro(f"O MAPA não respondeu agora para {sigla}. Tente de novo em instantes.", str(e)[:200])
            continue
        resumos.append(r[r["cd_ibge"].isin(cods)])
        causas.append(c[c["cd_ibge"].isin(cods)])
        parciais += p
        ultima = u if ultima is None or (u is not None and u > ultima) else ultima
    if not resumos:
        return None
    r = pd.concat(resumos, ignore_index=True)
    if r.empty:
        st.info(f"Nenhuma apólice de seguro rural subvencionado nos municípios do imóvel desde {ano0}.")
        return None
    nomes = {m["cod_ibge"]: m["nome_puro"] for m in muns}
    tot = r[["apolices", "area", "premio", "indenizacao", "sinistros"]].sum()
    sinistralidade = tot["indenizacao"] / tot["premio"] * 100 if tot["premio"] else float("nan")
    a, b, c_, d = st.columns(4)
    a.metric("Apólices", _br(tot["apolices"]), help=f"{_br(tot['area'])} ha segurados")
    b.metric("Com sinistro", _br(tot["sinistros"]),
             help=f"{_br(tot['sinistros'] / tot['apolices'] * 100, 0)}% das apólices")
    c_.metric("Indenizações pagas", f"R$ {_br(tot['indenizacao'] / 1e6, 1)} mi",
              help=f"prêmios pagos: R$ {_br(tot['premio'] / 1e6, 1)} mi")
    d.metric("Sinistralidade", f"{_br(sinistralidade, 0)}%",
             help="indenização ÷ prêmio. Abaixo de 100%: as seguradoras pagaram menos do que receberam.")

    if len(muns) > 1:
        pm = r.groupby("cd_ibge")[["apolices", "sinistros", "indenizacao", "premio"]].sum()
        st.dataframe(pd.DataFrame([{
            "Município": nomes.get(cod, cod), "Apólices": _br(x["apolices"]), "Com sinistro": _br(x["sinistros"]),
            "Indenização (R$ mi)": _br(x["indenizacao"] / 1e6, 1),
            "Sinistralidade": f"{_br(x['indenizacao'] / x['premio'] * 100, 0)}%" if x["premio"] else "–"}
            for cod, x in pm.iterrows()]), hide_index=True, width="stretch")

    c1, c2 = st.container(), st.container()   # tabela e, abaixo, o gráfico de causas
    with c1:
        pc = r.groupby("cultura")[["apolices", "sinistros", "indenizacao", "premio"]].sum() \
              .sort_values("apolices", ascending=False)
        st.dataframe(pd.DataFrame([{
            "Cultura": cult, "Apólices": _br(x["apolices"]), "Sinistros": _br(x["sinistros"]),
            "% com sinistro": f"{_br(x['sinistros'] / x['apolices'] * 100, 0)}%",
            "Indenização (R$ mi)": _br(x["indenizacao"] / 1e6, 1),
            "Sinistralidade": f"{_br(x['indenizacao'] / x['premio'] * 100, 0)}%" if x["premio"] else "–"}
            for cult, x in pc.iterrows()]), hide_index=True, width="stretch")
    with c2:
        cs = pd.concat(causas, ignore_index=True) if causas else pd.DataFrame()
        if not cs.empty:
            pe = cs.groupby("evento")["indenizacao"].sum().sort_values()
            fig = go.Figure(go.Bar(x=pe.values / 1e6, y=[str(e).capitalize() for e in pe.index], orientation="h",
                                   marker_color="#b26a00",
                                   text=[f"R$ {_br(v / 1e6, 1)} mi" for v in pe.values], textposition="auto"))
            fig.update_layout(height=max(160, 40 * len(pe) + 60), margin=dict(l=10, r=10, t=30, b=10),
                              title=dict(text="Indenização por causa", font=dict(size=13)),
                              xaxis_title="R$ milhões", separators=",.",
                              yaxis=dict(automargin=True))
            st.plotly_chart(fig, width="stretch")
        else:
            st.caption("Nenhuma indenização paga no período.")

    pa = r.groupby("ano_apolice")[["apolices", "sinistros", "indenizacao"]].sum()
    st.dataframe(pd.DataFrame([{"Ano": str(int(ano)), "Apólices": _br(x["apolices"]),
                                "Com sinistro": _br(x["sinistros"]),
                                "Indenização (R$ mi)": _br(x["indenizacao"] / 1e6, 1)}
                               for ano, x in pa.iterrows()]), hide_index=True, width="stretch")
    nota = ""
    if parciais:
        msg = parciais[0].replace("PSR: ", "").strip()
        nota = f" Atenção: {msg}."
    st.caption("Só entram apólices com subvenção federal (PSR). Grandes produtores costumam usar seguro "
               "privado ou nenhum, então o número de apólices pode ser baixo mesmo em regiões grandes."
               f"{nota} Fonte: MAPA, Programa de Subvenção ao Prêmio do Seguro Rural.")
    return {"apolices": int(tot["apolices"]), "sinistros": int(tot["sinistros"]),
            "indenizacao": float(tot["indenizacao"]), "premio": float(tot["premio"]),
            "sinistralidade": sinistralidade, "ano0": ano0}


# =========================================================================
# Fase 4: texto para o laudo e Excel com os valores usados e as fontes
# =========================================================================
def _sinal(v, ref):
    if v is None or ref is None or pd.isna(v) or pd.isna(ref) or not ref:
        return None
    return (v / ref - 1) * 100


def _acima(p):
    return f"{_br(abs(p), 1)}% {'acima' if p >= 0 else 'abaixo'}"


def _lista_pt(itens):
    itens = [i for i in itens if i]
    return itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1] if itens else ""


def _texto_laudo(res):
    muns = res["muns"]
    par = []
    if len(muns) == 1:
        onde = f"integralmente no município de {muns[0]['municipio'].replace(' - ', '/')}"
    else:
        onde = "nos municípios de " + _lista_pt(
            [f"{m['municipio'].replace(' - ', '/')} ({_br(m['porcentagem'], 1)}% da área)" for m in muns])
    reg = _lista_pt(res["nomes_reg"])
    par.append(f"O imóvel {res['imovel']}, com {_br(res['area_ha'], 2)} ha, localiza-se {onde}, "
               f"{'nas regiões geográficas imediatas' if len(res['nomes_reg']) > 1 else 'na região geográfica imediata'} "
               f"de {reg} (IBGE, 2017), que {'reúnem' if len(res['nomes_reg']) > 1 else 'reúne'} {res['n_reg']} municípios.")

    if res["producao"]:
        base = "dos municípios onde se insere o imóvel" if len(muns) > 1 else "do município"
        principais = _lista_pt([p["cultura"].split(" (")[0].lower() for p in res["producao"][:4]])
        varias = len(res["producao"]) > 1
        frases = [f"Segundo a Produção Agrícola Municipal do IBGE ({res['ano_pam']}), "
                  f"{'as principais lavouras' if varias else 'a principal lavoura'} {base} "
                  f"{'são' if varias else 'é'} {principais}."]
        for p in res["producao"][:3]:
            if pd.isna(p["imovel"]):
                continue
            d = _sinal(p["imovel"], p["regiao"])
            pond = " (média ponderada pela área do imóvel em cada município)" if res["modo"] == "ponderado" else ""
            frases.append(f"A produtividade média de {p['cultura'].split(' (')[0].lower()} foi de "
                          f"{_br(p['imovel'])} {p['unidade']}{pond}"
                          + (f", {_acima(d)} da média da região imediata ({_br(p['regiao'])} {p['unidade']})" if d is not None else "")
                          + (f"; no estado ({res['uf']}), a média foi de {_br(p['uf'])} {p['unidade']}" if pd.notna(p['uf']) else "")
                          + ".")
        par.append(" ".join(frases))

    r = res.get("renda")
    if r:
        un = r["unidade"]
        fp = r["fonte_preco"]
        if fp and not r["preco_editado"]:
            praca = re.sub(r"\s*\(.*?\)", "", fp["indicador"])
            preco = (f"o preço de R$ {_br(r['preco'], 2)} por {un}, correspondente à média mensal do indicador "
                     f"CEPEA/ESALQ de {r['cultura'].split(' (')[0].lower()} ({praca}) no período de "
                     f"{fp['periodo']} (Fonte: CEPEA/ESALQ)")
        else:
            preco = f"o preço de R$ {_br(r['preco'], 2)} por {un}, informado pelo avaliador"
        desc = (f", deduzidos R$ {_br(r['desconto'], 2)} por {un} referentes a frete e base até a propriedade, "
                f"resultando em R$ {_br(r['liquido'], 2)} por {un}") if r["desconto"] else ""
        c = r["custo"]
        if c and not r["custo_editado"]:
            custo = (f"O custo operacional de referência é de R$ {_br(r['operacional'], 2)} por hectare, conforme a "
                     f"CONAB para o polo de {c['local']}/{c['uf']}, safra {c['safra']} "
                     f"(tecnologia {c['tecnologia']}, referência {c['referencia']})")
        else:
            custo = f"O custo operacional adotado é de R$ {_br(r['operacional'], 2)} por hectare, informado pelo avaliador"
        par.append(f"Para a {r['cultura'].split(' (')[0].lower()}, adotou-se {preco}{desc}. {custo}. "
                   f"Com produtividade de {_br(r['prod_un'], 1)} {un} por hectare, a receita bruta estimada é de "
                   f"R$ {_br(r['receita'], 2)} por hectare e a margem operacional, de R$ {_br(r['margem_op'], 2)} por hectare.")

    z = res.get("zarc")
    if z and z["janelas"]:
        js = list(z["janelas"].items())
        abre = (f"De acordo com o Zoneamento Agrícola de Risco Climático do MAPA ({z['portaria']}), para "
                f"{z['variante'].lower()} em solo {z['solo'].split(' (')[0]} e ciclo {z['ciclo']}, ")
        if len(js) == 1:
            par.append(abre + f"a janela de plantio indicada em {js[0][0].replace(' - ', '/')} vai "
                              f"{_janela_texto(js[0][1], longo=True)}.")
        else:
            par.append(abre + "as janelas de plantio indicadas são: " + "; ".join(
                f"em {m.replace(' - ', '/')}, {_janela_texto(d, longo=True)}" for m, d in js) + ".")

    pc = res.get("pecuaria")
    if pc:
        muns_b = [(l, v) for l, v in pc["bovinos"][:len(muns)] if v is not None and pd.notna(v)]
        reg_b = pc["bovinos"][len(muns)][1] if len(pc["bovinos"]) > len(muns) else None
        reb = _lista_pt([f"{_br(v)} cabeças em {l.replace(' - ', '/')}" for l, v in muns_b])
        txt = f"Segundo a Pesquisa da Pecuária Municipal do IBGE ({pc['ano']}), o rebanho bovino era de {reb}"
        na_reg = "nas regiões imediatas" if len(res["nomes_reg"]) > 1 else "na região imediata"
        txt += (f", e de {_br(reg_b)} cabeças {na_reg} ({res['n_reg']} municípios)." if reg_b else ".")
        boi = next((p for p in pc["precos"] if p["nome"] == "Boi gordo"), None)
        if boi:
            txt += (f" O Indicador do Boi Gordo CEPEA/ESALQ ({boi['praca']}) teve média de R$ {_br(boi['media'], 2)} "
                    f"por arroba no período de {boi['periodo']} (Fonte: CEPEA/ESALQ).")
        par.append(txt)

    cs = res.get("censo")
    if cs and cs["uso"]:
        m0 = muns[0]["nome_puro"]
        u = cs["uso"].get(m0)
        t = cs["tamanho"].get(m0)
        if u and u.get("Lavouras") is not None:
            txt = (f"Segundo o Censo Agropecuário 2017 (IBGE), em {m0} as lavouras ocupavam {_br(u['Lavouras'], 1)}% "
                   f"da área dos estabelecimentos agropecuários, as pastagens {_br(u['Pastagens'], 1)}% e as matas e "
                   f"florestas naturais {_br(u['Matas e florestas naturais'], 1)}%.")
            if t:
                grandes = [t[f] for f in ("1.000 a 2.500 ha", "2.500 ha ou mais") if t.get(f) and t[f][0] is not None]
                if grandes:
                    txt += (f" Os estabelecimentos com 1.000 ha ou mais eram {_br(sum(g[0] for g in grandes), 1)}% do total "
                            f"e detinham {_br(sum(g[1] for g in grandes), 1)}% da área.")
            par.append(txt)
    return "\n\n".join(par)


def _excel_laudo(res, texto):
    import io
    buf = io.BytesIO()
    hoje = pd.Timestamp.today().strftime("%d/%m/%Y")
    with pd.ExcelWriter(buf, engine="xlsxwriter") as xw:
        resumo = [("Imóvel", res["imovel"], "", "GEOCAPUTI", hoje),
                  ("Área do imóvel", round(res["area_ha"], 2), "ha", "GEOCAPUTI", hoje)]
        for m in res["muns"]:
            resumo.append((f"Parte em {m['municipio']}", round(m["porcentagem"], 2), "% da área", "IBGE malha municipal", ""))
        resumo.append(("Região imediata de comparação", ", ".join(res["nomes_reg"]), f"{res['n_reg']} municípios", "IBGE 2017", ""))
        r = res.get("renda")
        if r:
            fp = r["fonte_preco"] or {}
            c = r["custo"] or {}
            resumo += [
                (f"Produtividade do imóvel · {r['cultura']}", round(r["prod_un"], 2), f"{r['unidade']}/ha", f"IBGE PAM {res['ano_pam']}", res["consultado"]),
                ("Preço de referência", round(r["preco"], 2), f"R$/{r['unidade']}",
                 "Avaliador" if r["preco_editado"] else f"CEPEA/ESALQ · {fp.get('indicador', '')} · média {fp.get('periodo', '')}", fp.get("data", "")),
                ("Desconto até a fazenda (frete e base)", round(r["desconto"], 2), f"R$/{r['unidade']}", "Avaliador", hoje),
                ("Receita bruta", round(r["receita"], 2), "R$/ha", "Cálculo", hoje),
                ("Custo operacional", round(r["operacional"], 2), "R$/ha",
                 "Avaliador" if r["custo_editado"] else f"CONAB · polo {c.get('local', '')}/{c.get('uf', '')} · safra {c.get('safra', '')}", c.get("referencia", "")),
                ("Margem operacional", round(r["margem_op"], 2), "R$/ha", "Cálculo", hoje)]
            if c:
                resumo += [("Custo variável (CONAB)", c.get("variavel"), "R$/ha", "CONAB", c.get("referencia", "")),
                           ("Custo fixo (CONAB)", c.get("fixo"), "R$/ha", "CONAB", c.get("referencia", "")),
                           ("Renda de fatores (CONAB)", c.get("fatores"), "R$/ha", "CONAB", c.get("referencia", ""))]
        pd.DataFrame(resumo, columns=["Item", "Valor", "Unidade", "Fonte", "Referência/consulta"]).to_excel(xw, sheet_name="Resumo", index=False)

        if res["producao"]:
            linhas = []
            for p in res["producao"]:
                l = {"Cultura": p["cultura"], "Ano": res["ano_pam"]}
                for mun, a in p["area"].items():
                    l[f"Área colhida {mun} (ha)"] = a
                l.update({"Produtividade imóvel": p["imovel"], "Produtividade região": p["regiao"],
                          f"Produtividade {res['uf']}": p["uf"], "Unidade": p["unidade"]})
                linhas.append(l)
            pd.DataFrame(linhas).to_excel(xw, sheet_name="Produção", index=False)

        z = res.get("zarc")
        if z and z["janelas"]:
            rot = [f"{['1-10', '11-20', '21-fim'][i % 3]} {MESES_PT[i // 3]}" for i in range(36)]
            zl = [{"Município": m, "Zoneamento": z["variante"], "Solo": z["solo"], "Ciclo": z["ciclo"],
                   **{rot[i]: d[i] for i in range(36)}} for m, d in z["janelas"].items()]
            pd.DataFrame(zl).to_excel(xw, sheet_name="ZARC (risco %)", index=False)

        pc = res.get("pecuaria")
        if pc:
            pd.DataFrame(pc["bovinos"], columns=["Local", f"Bovinos {pc['ano']} (cabeças)"]).to_excel(xw, sheet_name="Pecuária", index=False)

        cs = res.get("censo")
        if cs and cs["uso"]:
            pd.DataFrame(cs["uso"]).T.reset_index(names="Local").to_excel(xw, sheet_name="Censo · uso das terras", index=False)
            tl = [{"Local": loc, "Faixa": f, "% dos estabelecimentos": v[0], "% da área": v[1]}
                  for loc, faixas in cs["tamanho"].items() for f, v in faixas.items()]
            pd.DataFrame(tl).to_excel(xw, sheet_name="Censo · tamanho", index=False)

        fontes = [
            ("IBGE · Produção Agrícola Municipal", f"SIDRA tabela {TAB_PAM}", f"{res['ano_pam']}", "Livre, citar a fonte"),
            ("IBGE · Pesquisa da Pecuária Municipal", f"SIDRA tabela {TAB_PPM}", str(pc["ano"]) if pc else "", "Livre, citar a fonte"),
            ("IBGE · Censo Agropecuário 2017", "SIDRA tabelas 6881 e 6754", "2017", "Livre, citar a fonte"),
            ("CEPEA/ESALQ-USP · indicadores de preço", "cepea.esalq.usp.br", "média 12 meses fechados",
             "CC BY-NC 4.0: atribuição obrigatória; uso interno de análise"),
            ("CONAB · custos de produção", "conab.gov.br", (r["custo"] or {}).get("safra", "") if r else "", "Livre, citar a fonte"),
            ("MAPA · ZARC (tábua de risco)", "dados.agricultura.gov.br", z["tabuas"] if z else "", "Livre, citar a portaria"),
        ]
        pd.DataFrame(fontes, columns=["Fonte", "Origem", "Período/edição", "Uso"]).to_excel(xw, sheet_name="Fontes", index=False)
        pd.DataFrame({"Texto para o laudo": texto.split("\n\n")}).to_excel(xw, sheet_name="Texto", index=False)
        for ws in xw.sheets.values():
            ws.set_column(0, 0, 38)
            ws.set_column(1, 60, 16)
    return buf.getvalue()


def _bloco_laudo(res):
    ui.secao("Levar para o laudo")
    texto = _texto_laudo(res)
    if not texto:
        st.caption("Sem dados suficientes para montar o texto.")
        return
    st.caption("Texto gerado com os mesmos números da tela, já com as fontes. Use o ícone de copiar "
               "no canto da caixa e ajuste no laudo como preferir.")
    st.code(texto, language=None, wrap_lines=True)
    nome = re.sub(r"[^\w.-]+", "_", str(res["imovel"]))[:40]
    try:
        dados = _excel_laudo(res, texto)
        st.download_button("⬇ Excel com valores usados e fontes", data=dados,
                           file_name=f"mercado_{nome}_{pd.Timestamp.today():%Y%m%d}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           type="primary")
        st.caption("A planilha traz só os números usados na análise, com fonte e data de cada um. "
                   "As séries de preço do CEPEA não são exportadas.")
    except Exception as e:
        ui.erro("Não foi possível gerar o Excel.", str(e)[:200])


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
    # Fica acima das sub-abas: Produção, Renda e Risco usam a mesma lista (e um
    # widget dentro de uma aba fechada perderia o valor escolhido).
    culturas = st.multiselect(
        "Culturas analisadas (sugeridas: as maiores do município pela área colhida)",
        options=todas, default=todas[:N_CULTURAS], key=f"mercado_culturas::{source_name}")
    if not culturas:
        st.caption("Escolha ao menos uma cultura.")

    # Mesma área da faixa do imóvel (ui.barra_imovel): calculada do perímetro em EPSG:5880.
    area_ha = sum(m["area_ha"] for m in muns)
    gdf = st.session_state.get("gdf_imovel")
    if gdf is not None and getattr(gdf, "empty", True) is False:
        try:
            g = gdf if gdf.crs is not None else gdf.set_crs(4326)
            area_ha = float(g.to_crs(5880).area.sum() / 1e4)
        except Exception:
            pass
    res = {"imovel": source_name, "area_ha": area_ha, "muns": muns,
           "modo": "ponderado" if len(pesos) > 1 else "unico", "nomes_reg": nomes_reg, "n_reg": len(regiao),
           "uf": utils.UF_POR_CODIGO.get(uf_ref, ""), "consultado": consultado, "ano_pam": ano,
           "producao": [], "renda": None, "zarc": None, "pecuaria": None, "censo": None,
           "credito": None, "juros": None, "valor": None, "seguro": None}
    for c in culturas:
        im, rg, uv, un, area = _rend_escalas(pam, c, ano, pesos, regiao, uf_ref)
        res["producao"].append({"cultura": c, "imovel": im, "regiao": rg, "uf": uv, "unidade": un,
                                "area": {m["municipio"]: area.get(m["cod_ibge"]) for m in muns}})

    chave = f"mercado::{source_name}"

    # Sub-abas preguiçosas: só a aba aberta roda (o seguro, por exemplo, leva ~1 min
    # na primeira consulta do estado e não deve atrasar quem só quer a produção).
    t_prod, t_renda, t_risco, t_pec, t_censo = st.tabs(
        ["Produção", "Renda e juros", "Risco", "Pecuária", "Censo"], key="mercado_subaba", on_change="rerun")

    if t_prod.open:
        with t_prod:
            with st.container(border=True):
                ui.secao("O que a região produz")
                if culturas:
                    tab = _tabela_producao(pam, muns, pesos, regiao, uf_ref, ano, culturas)
                    st.dataframe(tab, hide_index=True, width="stretch")
                    st.caption(f"Produtividade em {ano}. Imóvel = {rotulo_imovel.split('(')[1].rstrip(')')}; "
                               "região = Σ produção ÷ Σ área colhida dos municípios da região.")
                    foco = st.selectbox("Série de 10 anos da cultura:", culturas, key="mercado_foco")
                    st.plotly_chart(_grafico(pam, foco, pesos, regiao, uf_ref, rotulo_imovel), width="stretch")
                st.caption(f"Fonte: IBGE, Produção Agrícola Municipal (tabela {TAB_PAM}), "
                           f"{int(pam['ano'].min())}–{ano}. Consultado em {consultado}.")
            with st.container(border=True):
                res["credito"] = _bloco_credito(muns, pesos)

    if t_renda.open:
        with t_renda:
            if culturas:
                with st.spinner("Consultando o crédito rural no Banco Central..."):
                    _, ref_cred, _, _ = _credito_imovel(muns, pesos)   # já trata falhas por município
                with st.container(border=True):
                    res["renda"] = _bloco_renda(pam, ano, culturas, pesos, regiao, uf_ref, chave, ref_cred)
            with st.container(border=True):
                res["juros"] = _bloco_juros()
            with st.container(border=True):
                res["valor"] = _bloco_valor(res["renda"], res["juros"], area_ha, chave)

    if t_risco.open:
        with t_risco:
            if culturas:
                with st.container(border=True):
                    res["zarc"] = _bloco_zarc(muns, culturas, chave)
            with st.container(border=True):
                res["seguro"] = _bloco_seguro(muns, ufs)

    if t_pec.open:
        with t_pec:
            with st.container(border=True):
                res["pecuaria"] = _bloco_pecuaria(muns, regiao, uf_ref, ufs)

    if t_censo.open:
        with t_censo:
            with st.container(border=True):
                res["censo"] = _bloco_censo(muns, regiao, uf_ref, ufs)

    if MOSTRAR_LAUDO:
        with st.container(border=True):
            _bloco_laudo(res)
