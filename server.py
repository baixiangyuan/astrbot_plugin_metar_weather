"""
机场通播 / 天气 / 预报 独立服务（可单独部署）
============================================
对外暴露 REST 接口，供 AstrBot 插件（配置 atis_api）或其他客户端调用。
数据源：efb.dubhenexus.org 真实机场 API（覆盖中国及全球机场）。

    GET /api/atis?icao=ZBAA     ->  该机场通播文本(JSON，基于真实 METAR + 跑道)
    GET /api/weather?icao=ZBAA  ->  该机场实时天气(JSON，已解码)
    GET /api/taf?icao=ZBAA      ->  该机场天气预报(JSON，已解码)
    GET /health                 ->  健康检查

运行：
    pip install aiohttp
    python server.py                # 默认 0.0.0.0:8000
    python server.py --port 9000 --host 127.0.0.1

然后在插件配置里填：atis_api = http://<你的服务器>:8000
"""

import argparse
import os

import aiohttp
from aiohttp import web

# 让本文件可直接运行：把同目录加入路径以导入 atis_gen / metar_parser / taf_parser
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in os.sys.path:
    os.sys.path.insert(0, BASE_DIR)

from atis_gen import format_broadcast  # noqa: E402
from metar_parser import parse_metar  # noqa: E402
from taf_parser import parse_taf  # noqa: E402

DUBHE_BASE = os.getenv("DUBHE_BASE", "https://efb.dubhenexus.org/api/airports")
TIMEOUT = int(os.getenv("ATIS_TIMEOUT", "10"))


async def fetch_airport(icao: str):
    url = f"{DUBHE_BASE}/{icao}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=TIMEOUT)
            ) as resp:
                if resp.status in (404, 500):
                    return None
                if resp.status != 200:
                    return None
                raw = await resp.json(content_type=None)
        if isinstance(raw, dict):
            data = raw.get("data")
            return data if isinstance(data, dict) else None
        return None
    except Exception:
        return None


async def handle_atis(request: web.Request):
    icao = (request.query.get("icao") or "").strip().upper()
    if not icao or not (icao.isalpha() and 3 <= len(icao) <= 4):
        return web.json_response(
            {"ok": False, "error": "ICAO 代码格式不正确，应为 3~4 位字母。"},
            status=400,
        )
    data = await fetch_airport(icao)
    if data and data.get("rawMETAR"):
        broadcast = format_broadcast(icao, [data["rawMETAR"]], airport_data=data)
        return web.json_response(
            {"ok": True, "icao": icao, "source": "metar", "broadcast": broadcast}
        )
    return web.json_response(
        {"ok": False, "icao": icao, "error": "未查询到该机场的通播或天气信息。"},
        status=404,
    )


async def handle_weather(request: web.Request):
    icao = (request.query.get("icao") or "").strip().upper()
    if not icao or not (icao.isalpha() and 3 <= len(icao) <= 4):
        return web.json_response(
            {"ok": False, "error": "ICAO 代码格式不正确，应为 3~4 位字母。"},
            status=400,
        )
    data = await fetch_airport(icao)
    if not data:
        return web.json_response(
            {"ok": False, "icao": icao, "error": "未查询到该机场信息。"}, status=404
        )
    raw = data.get("rawMETAR") or ""
    decoded = parse_metar(raw) if raw else ""
    return web.json_response(
        {"ok": True, "icao": icao, "raw": raw, "decoded": decoded}
    )


async def handle_taf(request: web.Request):
    icao = (request.query.get("icao") or "").strip().upper()
    if not icao or not (icao.isalpha() and 3 <= len(icao) <= 4):
        return web.json_response(
            {"ok": False, "error": "ICAO 代码格式不正确，应为 3~4 位字母。"},
            status=400,
        )
    data = await fetch_airport(icao)
    if not data:
        return web.json_response(
            {"ok": False, "icao": icao, "error": "未查询到该机场信息。"}, status=404
        )
    raw = data.get("rawTAF") or ""
    if not raw:
        return web.json_response(
            {"ok": False, "icao": icao, "error": "该机场暂无 TAF 预报数据。"},
            status=404,
        )
    decoded = parse_taf(raw)
    return web.json_response(
        {"ok": True, "icao": icao, "raw": raw, "decoded": decoded}
    )


async def handle_health(request: web.Request):
    return web.json_response({"ok": True, "service": "airport-atis"})


def main():
    parser = argparse.ArgumentParser(description="机场通播/天气/预报独立服务")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    app = web.Application()
    app.router.add_get("/api/atis", handle_atis)
    app.router.add_get("/api/weather", handle_weather)
    app.router.add_get("/api/taf", handle_taf)
    app.router.add_get("/health", handle_health)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
