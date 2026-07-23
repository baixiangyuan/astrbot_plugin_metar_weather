"""
TAF 预报报文解析器
==================
将原始 TAF（机场天气预报）报文解析为可读的中文文本。
TAF 由「主预报」与若干「次时段」组成，次时段关键词包括
TEMPO（短时波动）、BECMG（逐渐转变）、FM（自某时刻起）、PROBxx（概率）。

本模块复用 metar_parser 中对风/能见度/天气现象/云况的解码函数，
保证两套报文解码风格一致。仅依赖标准库 + metar_parser（同目录）。
"""

import re

# 兼容两种加载方式：包内相对导入 / 直接模块导入
try:
    from .metar_parser import (
        _is_wind,
        _parse_wind,
        _is_visibility,
        _parse_visibility,
        _is_weather,
        _parse_weather,
        _is_cloud,
        _parse_cloud,
    )
except ImportError:  # pragma: no cover
    import os
    import sys

    sys.path.insert(0, os.path.dirname(__file__))
    from metar_parser import (  # type: ignore
        _is_wind,
        _parse_wind,
        _is_visibility,
        _parse_visibility,
        _is_weather,
        _parse_weather,
        _is_cloud,
        _parse_cloud,
    )

_PERIOD_LABEL = {
    "TEMPO": "短时波动 (TEMPO)",
    "BECMG": "逐渐转变 (BECMG)",
    "FM": "自时刻起 (FM)",
    "PROB30": "概率 30%",
    "PROB40": "概率 40%",
}


def _decode_groups(groups):
    """把一组气象要素 token 解码成中文行（风/能见度/天气/云）。"""
    lines = []
    for tok in groups:
        if _is_wind(tok):
            lines.append(_parse_wind(tok))
            break
    for tok in groups:
        if _is_visibility(tok):
            lines.append(_parse_visibility(tok))
            break
    weathers = []
    for tok in groups:
        if _is_weather(tok):
            w = _parse_weather(tok)
            if w and w != tok:
                weathers.append(w)
    if weathers:
        lines.append("🌦 天气现象：" + "、".join(weathers))
    clouds = []
    for tok in groups:
        if _is_cloud(tok):
            c = _parse_cloud(tok)
            if c and c != tok:
                clouds.append(c)
    if clouds:
        lines.append("☁️ 云况：" + "、".join(clouds))
    return lines


def _parse_temp_token(tok: str):
    """解析 TX29/1107Z 或 TN24/1021Z 中的温度值。"""
    neg = tok.startswith("M")
    body = tok[1:] if neg else tok
    try:
        return f"{-int(body)}°C" if neg else f"{int(body)}°C"
    except ValueError:
        return tok


def _extract_temperatures(tokens):
    """扫描全报文，提取 TX/TN 温度预报。"""
    out = []
    for t in tokens:
        m = re.match(r"^(TX|TN)(M?\d{2})/(\d{2})(\d{2})Z$", t)
        if m:
            kind = "最高气温" if m.group(1) == "TX" else "最低气温"
            val = _parse_temp_token(m.group(2))
            day, hour = m.group(3), m.group(4)
            out.append(f"{kind} {val}（{day}日 {hour}:00 UTC）")
    return out


def _split_periods(tokens):
    """将主预报之后的 token 拆分为若干时段。

    返回：[(keyword, valid_range, groups), ...]
    keyword 为 None 表示主预报；valid_range 为空表示沿用全局有效时段。
    """
    periods = []
    cur_kw = None
    cur_valid = ""
    cur_groups = []

    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        # FM 时段：FM1230 或 FM 1230
        if t.startswith("FM") and (t[2:].isdigit() or (i + 1 < n and tokens[i + 1].isdigit() and len(tokens[i + 1]) == 4)):
            periods.append((cur_kw, cur_valid, cur_groups))
            cur_kw = "FM"
            if t[2:].isdigit():
                cur_valid = t[2:]
                i += 1
            else:
                cur_valid = tokens[i + 1]
                i += 2
            cur_groups = []
            continue
        # TEMPO / BECMG 时段，后接可选的有效时段范围
        if t in ("TEMPO", "BECMG"):
            periods.append((cur_kw, cur_valid, cur_groups))
            cur_kw = t
            cur_valid = ""
            i += 1
            if i < n and re.match(r"^\d{4}/\d{4}$", tokens[i]):
                cur_valid = tokens[i]
                i += 1
            cur_groups = []
            continue
        # PROBxx 概率时段
        if t.startswith("PROB") and t[4:].isdigit():
            periods.append((cur_kw, cur_valid, cur_groups))
            cur_kw = t
            cur_valid = ""
            i += 1
            if i < n and re.match(r"^\d{4}/\d{4}$", tokens[i]):
                cur_valid = tokens[i]
                i += 1
            cur_groups = []
            continue
        cur_groups.append(t)
        i += 1
    periods.append((cur_kw, cur_valid, cur_groups))
    return periods


def _fmt_valid(valid: str) -> str:
    """将 1012/1118 这样的时段格式化为中文。"""
    if not valid or "/" not in valid:
        return ""
    f, t = valid.split("/", 1)
    return f"{f[:2]}日 {f[2:4]}:00 - {t[:2]}日 {t[2:4]}:00 UTC"


def parse_taf(taf: str) -> str:
    """将单条 TAF 报文解析为可读中文，返回多行文本。"""
    taf = (taf or "").strip()
    if not taf:
        return ""
    toks = taf.split()
    i = 0
    if toks and toks[0] == "TAF":
        i = 1

    station = ""
    if i < len(toks) and re.match(r"^[A-Z]{4}$", toks[i]):
        station = toks[i]
        i += 1

    issuance = ""
    if i < len(toks) and re.match(r"^\d{6}Z$", toks[i]):
        issuance = toks[i]
        i += 1

    global_valid = ""
    if i < len(toks) and re.match(r"^\d{4}/\d{4}$", toks[i]):
        global_valid = toks[i]
        i += 1

    periods = _split_periods(toks[i:])
    temperatures = _extract_temperatures(toks)

    lines = [f"📑 {station} 机场天气预报 (TAF)"]
    if issuance:
        lines.append(f"🕐 发布时间：{issuance[:2]}日 {issuance[2:4]}:{issuance[4:6]} UTC")
    gv = _fmt_valid(global_valid)
    if gv:
        lines.append(f"⏳ 有效时段：{gv}")

    for kw, valid, groups in periods:
        if kw is None:
            header = "🟢 主预报"
        else:
            label = _PERIOD_LABEL.get(kw, kw)
            vstr = _fmt_valid(valid) if valid else ""
            header = f"🔸 {label}" + (f"（{vstr}）" if vstr else "")
        decoded = _decode_groups(groups)
        if decoded:
            lines.append("")
            lines.append(header)
            lines.extend(decoded)
        else:
            # 该时段无具体气象要素（极少见），原样保留 token
            if groups:
                lines.append("")
                lines.append(header)
                lines.append(" ".join(groups))

    if temperatures:
        lines.append("")
        lines.append("🌡 温度预报：" + "；".join(temperatures))

    return "\n".join(lines)


if __name__ == "__main__":
    samples = [
        "TAF ZBAA 100900Z 1012/1118 16004MPS 5000 BR BKN040 TX29/1107Z TN24/1021Z "
        "TN25/1118Z TEMPO 1012/1018 TSRA SCT040CB BKN040 TEMPO 1112/1118 TSRA SCT040CB BKN040",
        "TAF ZGGG 100900Z 1012/1112 16003MPS CAVOK TX30/1107Z TN24/1021Z BECMG 1018/1020 20005MPS",
    ]
    for s in samples:
        print("=" * 48)
        print(s)
        print("-" * 48)
        print(parse_taf(s))
