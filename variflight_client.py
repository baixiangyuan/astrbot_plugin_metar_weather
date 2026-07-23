"""飞友科技(VariFlight) Aviation MCP 轻量客户端 —— 按飞机注册号查询实时定位。

为什么不用 Node / npx 那套 MCP：AstrBot 插件是 Python，直接走 MCP 的
Streamable HTTP(JSON-RPC) 即可，纯标准库 urllib 实现，零额外依赖、无 Node 环境要求。

接口说明：
- MCP 地址：https://ai.variflight.com/servers/aviation/mcp/?api_key=KEY
- 工具：getRealtimeLocationByAnum({ anum: "B6392" })
- 返回体中 content[0].text 形如：
  "Realtime location: {'code': 200, 'data': {'arr': 'NKG', ...}, ...}"
  注意是 Python 单引号 dict repr（非 JSON），用 ast.literal_eval 解析。
"""

import ast
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

DEFAULT_MCP_URL = "https://ai.variflight.com/servers/aviation/mcp/"


# 常见机场 IATA 代码 -> 中文名（覆盖不全时回退显示代码）
_AIRPORT_CN = {
    "PEK": "北京首都", "PKX": "北京大兴", "SHA": "上海虹桥", "PVG": "上海浦东",
    "CAN": "广州白云", "SZX": "深圳宝安", "CTU": "成都双流", "TFU": "成都天府",
    "CKG": "重庆江北", "HGH": "杭州萧山", "XIY": "西安咸阳", "TYN": "太原武宿",
    "NKG": "南京禄口", "HFE": "合肥新桥", "WUH": "武汉天河", "CSX": "长沙黄花",
    "KMG": "昆明长水", "SYX": "三亚凤凰", "XMN": "厦门高崎", "FOC": "福州长乐",
    "TPE": "台北桃园", "KHH": "高雄小港", "HKG": "香港", "MFM": "澳门",
    "JJN": "泉州晋江", "YCU": "运城张孝", "TAO": "青岛胶东", "TSN": "天津滨海",
    "SHE": "沈阳桃仙", "DLC": "大连周水子", "HRB": "哈尔滨太平", "URC": "乌鲁木齐地窝堡",
    "LXA": "拉萨贡嘎", "KWE": "贵阳龙洞堡", "KWL": "桂林两江", "NNG": "南宁吴圩",
    "ZUH": "珠海金湾", "ZHA": "湛江", "JZH": "九寨黄龙", "LJG": "丽江三义",
    "LHW": "兰州中川", "INC": "银川河东", "XNN": "西宁曹家堡", "NLT": "大理",
    "CGO": "郑州新郑", "KHN": "南昌昌北", "NGB": "宁波栎社", "WNZ": "温州龙湾",
    "JUZ": "衢州", "WUX": "苏南硕放", "NTG": "南通兴东", "YTY": "扬州泰州",
    "DOY": "东营", "JNG": "济宁", "LYI": "临沂", "WEH": "威海",
    "BKK": "曼谷", "SIN": "新加坡", "ICN": "首尔仁川", "PUS": "釜山",
    "NRT": "东京成田", "HND": "东京羽田", "KUL": "吉隆坡", "TPE": "台北",
    "LAX": "洛杉矶", "SFO": "旧金山", "JFK": "纽约肯尼迪", "ORD": "芝加哥",
    "LHR": "伦敦希思罗", "CDG": "巴黎戴高乐", "FRA": "法兰克福", "DXB": "迪拜",
    "SYD": "悉尼", "MEL": "墨尔本", "YYZ": "多伦多", "DEL": "新德里",
}


def _ap(code: Optional[str]) -> str:
    if not code:
        return "未知"
    return _AIRPORT_CN.get(code, code)


def _heading(angle: Optional[float]) -> str:
    if angle is None:
        return ""
    a = angle % 360
    dirs = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    return dirs[round(a / 45) % 8]


def _post(url: str, payload: dict):
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "Mozilla/5.0",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as e:
        # 飞友 nginx 会把 https 307 到后端 http，urllib 默认不跨协议跟随
        if e.code == 307 and e.headers.get("Location"):
            req2 = urllib.request.Request(
                e.headers["Location"], data=data, headers=headers, method="POST"
            )
            r = urllib.request.urlopen(req2, timeout=20)
        else:
            raise
    raw = r.read().decode("utf-8", "replace")
    if "data:" in raw:  # SSE 形式
        for line in raw.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _mcp_call(tool: str, arguments: dict, api_key: str, mcp_url: str):
    base = mcp_url.rstrip("/") + "/"
    url = f"{base}?api_key={api_key}"
    # 1) 握手 initialize
    _post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "astrbot-metar-weather", "version": "1.0"},
            },
        },
    )
    # 2) 通知 initialized
    _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    # 3) 调用工具
    res = _post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
    )
    if not res:
        return None
    result = res.get("result") or {}
    content = result.get("content") or []
    if not content:
        return None
    text = content[0].get("text", "")
    return _parse_text(text)


def _parse_text(text: str):
    """text 形如 'Realtime location: {...}'（Python 单引号 dict repr）。"""
    prefix = "Realtime location:"
    if prefix in text:
        text = text.split(prefix, 1)[1].strip()
    try:
        return ast.literal_eval(text)
    except Exception:
        return {"raw": text, "code": -1, "message": "解析失败"}


def get_realtime_location(
    anum: str, api_key: str, mcp_url: str = DEFAULT_MCP_URL
) -> Optional[dict]:
    """按注册号查询实时定位，返回解析后的 dict（含 data 字段）；失败返回 None。"""
    if not api_key or not anum:
        return None
    return _mcp_call("getRealtimeLocationByAnum", {"anum": anum}, api_key, mcp_url)


def format_location(loc: Optional[dict], anum: str) -> Optional[str]:
    """把解析后的定位 dict 格式化为中文文本。失败返回 None。"""
    if not loc or loc.get("code") != 200:
        return None
    d = loc.get("data", {}) or {}
    fnum = d.get("fnum") or "未知"
    dep = d.get("dep")
    arr = d.get("arr")
    pos = d.get("position") or {}
    lng = pos.get("lng")
    lat = pos.get("lat")
    height = d.get("height")
    speed = d.get("speed")
    angle = d.get("angle")
    upd = d.get("updatetime")
    pic = d.get("pic")
    ac = d.get("anum") or anum

    # 更新时间（北京时间）
    ts = ""
    if upd:
        try:
            dt = datetime.fromtimestamp(
                float(upd), tz=timezone(timedelta(hours=8))
            )
            ts = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts = ""

    lines = []
    lines.append(
        f"✈️ 飞机注册号 {ac} 当前正在执行航班 {fnum}，"
        f"从{_ap(dep)}（{dep}）飞往{_ap(arr)}（{arr}）。"
    )

    coord_parts = []
    if lat is not None:
        coord_parts.append(f"纬度 {lat}")
    if lng is not None:
        coord_parts.append(f"经度 {lng}")
    detail = ""
    if height is not None:
        detail += f"，高度约 {round(height)} 米（{round(height * 3.28084)} 英尺）"
    if speed is not None:
        detail += f"，速度约 {round(speed)} 公里/小时"
    if angle is not None:
        detail += f"，航向 {round(angle)}°（{_heading(angle)}）"
    lines.append("最新定位坐标：" + "，".join(coord_parts) + detail + "。")

    if ts:
        lines.append(f"数据更新时间：{ts}（北京时间）。")
    if pic:
        lines.append(f"🛩 机型图片：{pic}")
    return "\n".join(lines)


if __name__ == "__main__":
    import os
    import sys

    sys.path.insert(0, os.path.dirname(__file__))
    k = os.environ.get("VF_KEY", "")
    if not k:
        print("set env VF_KEY to test")
        sys.exit(0)
    test_anum = sys.argv[1] if len(sys.argv) > 1 else "B6392"
    r = get_realtime_location(test_anum, k)
    print("RAW:", r)
    print("----")
    print(format_location(r, test_anum))
