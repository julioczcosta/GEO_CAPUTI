# -*- coding: utf-8 -*-
"""
Leitura "inteligente" de coordenadas digitadas/coladas pelo usuario.

Aceita, uma coordenada por linha (ou separadas por ';'):
  - decimal com ponto ou virgula:      -15.7942, -47.8822  |  -15,7942 -47,8822
  - graus/minutos/segundos:            15°47'39"S 47°52'56"W  |  15 47 39 S, 47 52 56 O
  - graus e minutos decimais:          15°47.65'S 47°52.93'W
  - UTM SIRGAS 2000 (fuso obrigatorio): 23L 192345 8251234  |  fuso 23 E 192345 N 8251234
  - link do Google Maps (com a coordenada no endereco)

Convencoes para o Brasil: sem sinal nem hemisferio assume Sul/Oeste; ordem
lon/lat invertida e detectada pela faixa de valores; UTM sem letra assume Sul.

`interpretar(texto)` devolve lista de (lon, lat) — o formato dos pontos no app —
ou levanta ValueError com mensagem em portugues apontando a linha com problema.
"""

import re

# Caixa do Brasil (com folga) — usada para decidir ordem/sinal na ambiguidade.
_BR_LAT = (-34.5, 6.0)
_BR_LON = (-74.5, -28.0)

_ANG = re.compile(r"""
    (?P<h1>[NSEWOL](?![A-Z]))?\s*
    (?P<sg>[-+])?\s*
    (?P<d>\d+)(?:\.(?P<df>\d+))?\s*(?:°|GRAUS?\b|G\b|D\b)?\s*
    (?(df)|(?:(?P<m>\d+)(?:\.(?P<mf>\d+))?\s*(?:'|MIN\b|M\b)?\s*
        (?(mf)|(?:(?P<s>\d+(?:\.\d+)?)\s*(?:"|SEG\b)?\s*)?)
    )?)
    (?(h1)|(?P<h2>[NSEWOL](?![A-Z]))?)
""", re.X)


def _no_brasil(lat, lon):
    return _BR_LAT[0] <= lat <= _BR_LAT[1] and _BR_LON[0] <= lon <= _BR_LON[1]


def _normaliza(s):
    s = s.upper()
    for a, b in (("º", "°"), ("˚", "°"), ("′", "'"), ("’", "'"), ("´", "'"),
                 ("‘", "'"), ("″", '"'), ("“", '"'), ("”", '"'), ("''", '"'),
                 ("−", "-"), ("–", "-")):
        s = s.replace(a, b)
    s = re.sub(r"\b(LATITUDE|LONGITUDE|LAT|LONG|LON|LNG)\b\s*[:=]?", " ", s)
    s = re.sub(r"\bSUL\b", "S", s)
    s = re.sub(r"\bNORTE\b", "N", s)
    s = re.sub(r"\bOESTE\b", "O", s)
    s = re.sub(r"\bLESTE\b", "L", s)
    # virgula decimal: so quando nao ha ponto decimal no texto (senao e separador)
    if not re.search(r"\d\.\d", s):
        s = re.sub(r"(\d),(\d)", r"\1.\2", s)
    return s


def _google_maps(txt):
    """(lat, lon) de um link do Google Maps, ou None se nao for link."""
    if not re.search(r"https?://|GOOGLE\.|GOO\.GL|MAPS\.APP", txt, re.I):
        return None
    for pad in (r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)",          # local marcado
                r"[?&](?:q|ll|query|center)=(-?\d+\.\d+)(?:,|%2C)\s*(-?\d+\.\d+)",
                r"@(-?\d+\.\d+),(-?\d+\.\d+)"):              # centro da tela
        m = re.search(pad, txt, re.I)
        if m:
            return float(m.group(1)), float(m.group(2))
    raise ValueError("link sem coordenada no endereço (links encurtados não servem) "
                     "— abra o link e copie a coordenada ou o endereço completo")


def _utm(s):
    """(lat, lon) de uma linha UTM, ou None se nao parecer UTM."""
    nums = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\d+(?:\.\d+)?", s)]
    grandes = [(float(t), a) for t, a, _ in nums if float(t) >= 100000]
    if len(grandes) < 2:
        return None
    # fuso: inteiro 1-60 (opcionalmente seguido da letra da faixa / hemisferio)
    zm = None
    for m in re.finditer(r"(?<![\d.])(\d{1,2})\s*([C-X])?(?![\d.])", s):
        if 1 <= int(m.group(1)) <= 60:
            zm = m
            break
    if zm is None:
        raise ValueError("UTM sem fuso — informe o fuso, ex.: 23K 192345 8251234")
    fuso = int(zm.group(1))
    letra = zm.group(2) or ""
    norte = (letra == "N" or (letra >= "P" and letra != "S")
             or bool(re.search(r"HEMISF\w*\s+N\b|\bNORTE\b", s)))

    # E/N: rotulos explicitos, senao pela magnitude (E fica entre 100 e 900 mil)
    rot_e = re.search(r"\b(?:E|X|ESTE)\s*[:=]?\s*(\d+(?:\.\d+)?)", s)
    rot_n = re.search(r"\b(?:N|Y|NORTE)\s*[:=]?\s*(\d+(?:\.\d+)?)", s)
    if rot_e and rot_n:
        e, n = float(rot_e.group(1)), float(rot_n.group(1))
    else:
        v = [g for g, _ in grandes[:2]]
        e, n = (v[1], v[0]) if v[0] >= 1_000_000 > v[1] else (v[0], v[1])
    if not (100_000 <= e <= 900_000) or not (0 <= n <= 10_000_000):
        raise ValueError(f"UTM fora da faixa (E={e:.0f}, N={n:.0f})")

    from pyproj import Transformer
    crs = f"+proj=utm +zone={fuso} {'' if norte else '+south '}+ellps=GRS80 +units=m +no_defs"
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(e, n)
    return lat, lon


def _geografica(s):
    """(lat, lon) de uma linha em graus (decimal, GMS ou GM)."""
    angs = []
    for m in _ANG.finditer(s):
        if m.group("d") is None:
            continue
        d = float(m.group("d") + ("." + m.group("df") if m.group("df") else ""))
        mi = float(m.group("m") + ("." + m.group("mf") if m.group("mf") else "")) \
            if m.group("m") else 0.0
        se = float(m.group("s")) if m.group("s") else 0.0
        if mi >= 60 or se >= 60:
            raise ValueError("minutos/segundos devem ser menores que 60")
        hem = m.group("h1") or m.group("h2")
        val = d + mi / 60 + se / 3600
        neg = m.group("sg") == "-" or hem in ("S", "W", "O")
        angs.append({"v": -val if neg else val, "hem": hem,
                     "sinal": m.group("sg") is not None or hem is not None})
    if len(angs) != 2:
        raise ValueError("não reconheci duas coordenadas (latitude e longitude)")

    a, b = angs
    # hemisferio explicito define quem e lat/lon
    if a["hem"] in ("E", "W", "O", "L") or b["hem"] in ("N", "S"):
        a, b = b, a
    lat, lon = a["v"], b["v"]
    tem_hem = a["hem"] or b["hem"]

    # sem nenhum sinal/hemisferio: convencao Brasil (Sul/Oeste). Com sinal em so
    # uma delas, a outra e positiva (ex.: 2.82, -60.67 = Roraima), salvo longitude
    # positiva que so faz sentido no Brasil se for Oeste.
    def _sinais(la, lo, s_la, s_lo):
        if not s_la and not s_lo:
            return -abs(la), -abs(lo)
        if not s_lo and _no_brasil(la, -abs(lo)):
            lo = -abs(lo)
        return la, lo

    lat, lon = _sinais(lat, lon, a["sinal"], b["sinal"])
    # ordem invertida (lon, lat)? so troca se nao houver hemisferio dizendo o contrario
    if not tem_hem and not _no_brasil(lat, lon):
        lat2, lon2 = _sinais(b["v"], a["v"], b["sinal"], a["sinal"])
        if _no_brasil(lat2, lon2) or abs(lat) > 90:
            lat, lon = lat2, lon2
    if abs(lat) > 90 or abs(lon) > 180:
        raise ValueError("valores fora da faixa de latitude/longitude")
    return lat, lon


def interpretar_linha(linha):
    """(lon, lat) de uma unica coordenada em qualquer formato aceito."""
    g = _google_maps(linha)
    if g is not None:
        lat, lon = g
    else:
        s = _normaliza(linha)
        r = _utm(s)
        lat, lon = r if r is not None else _geografica(s)
    if not _no_brasil(lat, lon):
        raise ValueError(f"ficou fora do Brasil ({lat:.4f}, {lon:.4f}) — confira a "
                         "ordem, o sinal ou o fuso")
    return round(lon, 6), round(lat, 6)


def interpretar(texto):
    """Lista de (lon, lat). Uma coordenada por linha (ou separadas por ';')."""
    linhas = [l.strip() for l in re.split(r"[\n;]+", texto or "") if l.strip()]
    if not linhas:
        raise ValueError("digite ou cole uma coordenada")
    pontos, erros = [], []
    for i, l in enumerate(linhas, 1):
        try:
            pontos.append(interpretar_linha(l))
        except ValueError as e:
            erros.append(f"linha {i} ({l[:40]}): {e}" if len(linhas) > 1 else str(e))
    if erros:
        raise ValueError("; ".join(erros))
    return pontos
