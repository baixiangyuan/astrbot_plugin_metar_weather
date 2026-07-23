"""
METAR 报文解析器
================
将原始 METAR 气象报文解析为可读的中文文本。

支持的要素：报告类型、观测时间、风（含阵风/静风/风向不定）、
能见度（含 CAVOK、< / > 标记）、天气现象（强度/描述符/天气类型）、
云况（FEW/SCT/BKN/OVC/VV/NSC/CLR/SKC）、温度/露点、气压（QNH/高度表）、
变化趋势（BECMG/TEMPO/NOSIG + TL/FM/AT 时间）。

仅依赖标准库，可独立测试。
"""

import re

_WIND_UNIT_CN = {"MPS": "米/秒", "KT": "节", "KMH": "公里/小时"}

_CLOUD_CN = {
    "FEW": "少云(FEW)",
    "SCT": "疏云(SCT)",
    "BKN": "多云(BKN)",
    "OVC": "阴天(OVC)",
    "VV": "垂直能见度",
}

_WEATHER_INTENSITY = {"-": "小", "+": "大"}

_WEATHER_DESCRIPTOR = {
    "MI": "浅",
    "PR": "部分",
    "BC": "散片",
    "DR": "低吹",
    "BL": "高吹",
    "SH": "阵性",
    "TS": "雷暴",
    "FZ": "冻",
}

_WEATHER_PHENOMENON = {
    "DZ": "毛毛雨",
    "RA": "雨",
    "SN": "雪",
    "SG": "米雪",
    "PL": "冰粒",
    "GR": "冰雹",
    "GS": "小冰雹",
    "FG": "雾",
    "BR": "轻雾",
    "HZ": "霾",
    "FU": "烟",
    "SA": "沙",
    "DU": "尘",
    "VA": "火山灰",
    "PO": "尘卷风",
    "SQ": "飑",
    "FC": "漏斗云",
    "SS": "沙暴",
    "DS": "尘暴",
}

_TREND_KEYWORD = {
    "NOSIG": "无明显变化",
    "BECMG": "预计转变",
    "TEMPO": "短时波动",
}


def _is_wind(tok: str) -> bool:
    if tok in ("////", "/////", "///00KT", "00000KT", "VRB00KT"):
        return True
    body = tok
    for u in ("MPS", "KT", "KMH"):
        if tok.endswith(u):
            body = tok[: -len(u)]
            break
    return bool(re.match(r"^(VRB|\d{3}|///)(\d{2,3})(G\d{2,3})?$", body))


def _parse_wind(tok: str) -> str:
    if tok in ("////", "/////"):
        return "💨 风：缺测"
    unit = ""
    for u in ("MPS", "KT", "KMH"):
        if tok.endswith(u):
            unit = u
            break
    body = tok[: -len(unit)] if unit else tok
    m = re.match(r"^(VRB|\d{3}|///)(\d{2,3})(G\d{2,3})?$", body)
    if not m:
        return f"💨 风：{tok}"
    direction, speed, gust = m.group(1), m.group(2), m.group(3)
    unit_cn = _WIND_UNIT_CN.get(unit, unit or "未知单位")

    if direction == "000" and speed == "00":
        return "💨 风：静风"
    if direction == "VRB":
        dir_cn = "风向不定"
    elif direction == "///":
        dir_cn = "风向缺测"
    else:
        dir_cn = f"{direction}°"

    res = f"💨 风：{dir_cn} {int(speed)} {unit_cn}"
    if gust:
        res += f"（阵风 {int(gust[1:])} {unit_cn}）"
    return res


def _is_visibility(tok: str) -> bool:
    return tok == "CAVOK" or (
        len(tok) == 4
        and tok.isdigit()
        and tok not in ("0000",)
    ) or (len(tok) == 5 and tok[0] in "MP" and tok[1:].isdigit())


def _parse_visibility(tok: str) -> str:
    if tok == "CAVOK":
        return "👁 能见度：CAVOK（≥10公里，无重要云，无降水）"
    if tok[0] in "MP":
        prefix = "小于" if tok[0] == "M" else "大于"
        meters = int(tok[1:])
        return f"👁 能见度：{prefix} {meters} 米（约 {meters / 1000:.1f} 公里）"
    meters = int(tok)
    if meters >= 9999:
        return "👁 能见度：≥10 公里"
    return f"👁 能见度：{meters} 米（约 {meters / 1000:.1f} 公里）"


def _is_weather(tok: str) -> bool:
    if tok in ("NSW",):
        return True
    t = tok
    if t and t[0] in "+-":
        t = t[1:]
    if t.startswith("VC"):
        t = t[2:]
    if len(t) > 6:
        return False
    # 允许：描述符(2) + 天气(2)，或仅天气(2)，或仅描述符(2)
    return bool(re.match(r"^([A-Z]{2})?([A-Z]{2})?$", t))


def _parse_weather(tok: str) -> str:
    if tok == "NSW":
        return "无重要天气"
    intensity = ""
    t = tok
    if t and t[0] in "+-":
        intensity = _WEATHER_INTENSITY[t[0]]
        t = t[1:]
    vicinity = ""
    if t.startswith("VC"):
        vicinity = "附近"
        t = t[2:]
    desc = ""
    if len(t) >= 2 and t[:2] in _WEATHER_DESCRIPTOR:
        desc = _WEATHER_DESCRIPTOR[t[:2]]
        t = t[2:]
    phen = ""
    if t[:2] in _WEATHER_PHENOMENON:
        phen = _WEATHER_PHENOMENON[t[:2]]
        t = t[2:]
    parts = [p for p in (intensity, desc, phen, vicinity) if p]
    text = "".join(parts)
    return text if text else tok


def _is_cloud(tok: str) -> bool:
    for k in ("FEW", "SCT", "BKN", "OVC", "VV"):
        if tok.startswith(k):
            rest = tok[len(k):]
            return rest == "" or rest.isdigit()
    return tok in ("NSC", "NCD", "CLR", "SKC")


def _parse_cloud(tok: str) -> str:
    for k in ("FEW", "SCT", "BKN", "OVC", "VV"):
        if tok.startswith(k):
            rest = tok[len(k):]
            if rest.isdigit():
                ft = int(rest) * 100
                meters = int(ft * 0.3048)
                return f"{_CLOUD_CN[k]} {ft} 英尺（约 {meters} 米）"
            return _CLOUD_CN.get(k, k)
    if tok in ("NSC", "NCD"):
        return "无重要云"
    if tok in ("CLR", "SKC"):
        return "晴空"
    return tok


def _parse_temp(t: str) -> str:
    if t in ("", "//", "XX", "MM"):
        return "缺测"
    neg = t.startswith("M")
    if neg:
        t = t[1:]
    try:
        v = int(t)
        return f"{-v if neg else v}°C"
    except ValueError:
        return t


def _parse_trend(tokens):
    out = []
    for tok in tokens:
        if tok in _TREND_KEYWORD:
            out.append(_TREND_KEYWORD[tok])
        elif tok.startswith("TL"):
            out.append(f"截至 {tok[2:4]}:{tok[4:6]} UTC")
        elif tok.startswith("FM"):
            out.append(f"自 {tok[2:4]}:{tok[4:6]} UTC 起")
        elif tok.startswith("AT"):
            out.append(f"在 {tok[2:4]}:{tok[4:6]} UTC")
        elif tok == "CAVOK":
            out.append("CAVOK")
        elif _is_weather(tok):
            w = _parse_weather(tok)
            if w != tok or tok == "NSW":
                out.append(w)
        elif _is_cloud(tok):
            out.append(_parse_cloud(tok))
        elif _is_wind(tok):
            out.append(_parse_wind(tok))
    return "，".join(out)


def parse_metar(metar: str) -> str:
    """将单条 METAR 报文解析为可读中文，返回多行文本。"""
    metar = (metar or "").strip()
    if not metar:
        return ""
    parts = metar.split()
    i = 0
    lines: list[str] = []

    # 报告类型
    if parts and parts[0] in ("METAR", "SPECI"):
        i += 1

    # 站名
    station = parts[i] if i < len(parts) else ""
    i += 1

    # 观测时间 DDHHMMZ
    if i < len(parts) and len(parts[i]) >= 7 and parts[i].endswith("Z") and parts[i][:2].isdigit():
        tm = parts[i]
        day, hour, minute = tm[:2], tm[2:4], tm[4:6]
        lines.append(f"🕐 观测时间：{day}日 {hour}:{minute} UTC")
        i += 1

    # 风
    if i < len(parts) and _is_wind(parts[i]):
        lines.append(_parse_wind(parts[i]))
        i += 1

    # 能见度
    if i < len(parts) and _is_visibility(parts[i]):
        lines.append(_parse_visibility(parts[i]))
        i += 1

    # 天气现象（可连续多条）
    weathers = []
    while i < len(parts) and _is_weather(parts[i]):
        w = _parse_weather(parts[i])
        if w and w != parts[i]:
            weathers.append(w)
        i += 1
    if weathers:
        lines.append("🌦 天气现象：" + "、".join(weathers))

    # 云层
    clouds = []
    while i < len(parts) and _is_cloud(parts[i]):
        clouds.append(_parse_cloud(parts[i]))
        i += 1
    if clouds:
        lines.append("☁️ 云况：" + "、".join(clouds))

    # 温度/露点
    if i < len(parts) and "/" in parts[i]:
        t, d = parts[i].split("/", 1)
        # 简单校验：至少一侧为数字/M 开头
        if (t[:1].isdigit() or t.startswith("M") or t in ("//",)) and (
            d[:1].isdigit() or d.startswith("M") or d in ("//",)
        ):
            lines.append(f"🌡 气温：{_parse_temp(t)}　露点：{_parse_temp(d)}")
            i += 1

    # 气压
    if i < len(parts):
        p = parts[i]
        if p.startswith("Q") and p[1:].isdigit():
            lines.append(f"📊 修正海压(QNH)：{int(p[1:])} hPa")
            i += 1
        elif p.startswith("A") and p[1:].isdigit():
            lines.append(f"📊 高度表：{int(p[1:]) / 100:.2f} inHg")
            i += 1

    # 趋势 / 补充信息
    if i < len(parts):
        trend = _parse_trend(parts[i:])
        if trend:
            lines.append("🔮 趋势/补充：" + trend)

    # 顶部加站名标题
    if station:
        lines.insert(0, f"📍 机场：{station}")
    return "\n".join(lines)


if __name__ == "__main__":
    samples = [
        "METAR ZBAA 101400Z VRB01MPS 8000 BKN013 OVC040 25/25 Q1003 BECMG TL1450 SCT011 BKN040",
        "METAR ZSPD 101400Z 12006MPS 9999 BKN008 28/28 Q1007 NOSIG",
    ]
    for s in samples:
        print("=" * 40)
        print(s)
        print("-" * 40)
        print(parse_metar(s))
