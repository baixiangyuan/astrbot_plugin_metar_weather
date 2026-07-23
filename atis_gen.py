"""
机场通播（ATIS / D-ATIS）生成与格式化
=====================================
本模块提供两套能力：

1. format_broadcast(icao, metar_list)
   基于机场「真实 METAR 气象报文」生成一份中文机场通播（数字通播风格）。
   由于公开免费接口几乎不提供中国机场的实时数字通播(D-ATIS)文本，
   这里用真实观测数据拼出一份可读的广播，保证中国机场 100% 可用。

2. format_datis_reports(icao, reports)
   当上游（如 acgsir / Neko 综合气象与情报 API）返回了「真实数字通播」时，
   用此函数把返回值格式化成可读文本。字段结构做了兼容处理，避免解析报错。

仅依赖标准库 + metar_parser（同目录）。
"""

import json

# 兼容两种加载方式：包内相对导入 / 直接模块导入
try:
    from .metar_parser import parse_metar
except ImportError:  # pragma: no cover
    import os
    import sys

    sys.path.insert(0, os.path.dirname(__file__))
    from metar_parser import parse_metar  # type: ignore


def _fmt_metar_block(metar_list):
    """把 METAR 列表解码成中文可读文本，解码失败则退回原始报文。"""
    blocks = [
        parse_metar(m) for m in metar_list if isinstance(m, str) and m.strip()
    ]
    return "\n\n".join(b for b in blocks if b)


def _fmt_airport_header(icao: str, airport_data) -> str:
    """根据 dubhenexus 返回的机场静态信息生成标题行。"""
    if not isinstance(airport_data, dict):
        return f"📻 {icao} 机场通播（基于实时 METAR 生成）"
    name = airport_data.get("name") or ""
    iata = airport_data.get("iataId") or ""
    title = f"📻 {icao}"
    if iata:
        title += f"({iata})"
    if name:
        title += f" {name.title()}"
    title += " 通播（基于实时 METAR 生成）"
    return title


def _fmt_runways(airport_data) -> str:
    """把跑道信息格式化为中文。无数据返回空串。"""
    if not isinstance(airport_data, dict):
        return ""
    runways = airport_data.get("runways") or []
    if not runways:
        return ""
    parts = []
    for r in runways:
        rid = r.get("id", "")
        length = r.get("lengthM")
        width = r.get("widthM")
        surf = r.get("surface", "")
        seg = f"  · {rid}"
        if length:
            seg += f" {length}m"
        if width:
            seg += f"×{width}m"
        if surf:
            seg += f" {surf}"
        parts.append(seg)
    if parts:
        return "🛫 跑道：" + "；".join(parts)
    return ""


def format_broadcast(icao: str, metar_list, airport_data=None) -> str:
    """基于真实 METAR 生成机场通播文本。

    :param icao: 机场四字码，如 ZBAA
    :param metar_list: METAR 报文字符串列表（至少一个元素）
    :param airport_data: 可选，dubhenexus 返回的机场静态信息(dict)，
                         用于补充机场名称与跑道，使通播更完整。
    """
    if isinstance(metar_list, str):
        metar_list = [metar_list]
    metar_list = [str(x) for x in (metar_list or []) if x]

    if not metar_list:
        return f"❌ 未获取到 {icao} 的气象数据，无法生成通播。"

    decoded = _fmt_metar_block(metar_list)
    raw = "\n".join(metar_list)

    lines = [_fmt_airport_header(icao, airport_data), "─" * 28]
    lines.append(decoded if decoded else raw)
    rwy = _fmt_runways(airport_data)
    if rwy:
        lines.append(rwy)
    lines += ["─" * 28, "📋 原始报文：", raw]
    return "\n".join(lines)


def format_datis_reports(icao: str, reports) -> str:
    """把上游返回的真实数字通播(reports)格式化成可读文本。

    reports 通常为列表，元素可以是字符串或字典。字典字段做了兼容：
    常见键 airport/icao/station、code/letter、datis/text/content/message/atis、
    updatedAt/time/timestamp/obsTime。
    """
    if isinstance(reports, str):
        reports = [reports]
    if not isinstance(reports, list):
        # 兜底：整段返回
        return f"📻 {icao} 数字通播 (D-ATIS)：\n\n{reports}"

    parts = [f"📻 {icao} 数字通播 (D-ATIS)"]
    for r in reports:
        if isinstance(r, str):
            parts.append(r.strip())
            continue
        if not isinstance(r, dict):
            parts.append(str(r))
            continue

        airport = r.get("airport") or r.get("icao") or r.get("station") or ""
        code = r.get("code") or r.get("letter") or r.get("atisCode") or ""
        text = (
            r.get("datis")
            or r.get("text")
            or r.get("content")
            or r.get("message")
            or r.get("atis")
            or ""
        )
        ts = (
            r.get("updatedAt")
            or r.get("time")
            or r.get("timestamp")
            or r.get("obsTime")
            or ""
        )

        block = []
        head = f"{airport} 通播 {code}".strip() if (airport or code) else ""
        if head:
            block.append(head)
        if text:
            block.append(str(text))
        elif r:
            # 未知结构：直接 dump，避免丢失信息
            block.append(json.dumps(r, ensure_ascii=False))
        if ts:
            block.append(f"更新时间：{ts}")
        parts.append("\n".join(block))

    return "\n\n".join(parts)
