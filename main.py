"""
AstrBot 插件：机场天气 / 预报 / 通播 (ATIS)
=========================================
数据源：efb.dubhenexus.org 真实机场 API（覆盖中国及全球机场，
返回 真实 METAR、真实 TAF 预报、跑道/坐标等静态信息）。

命令：
    /天气 ZBAA        ->  查询机场实时天气（解码为中文，小写 /weather zbaa 也行）
    /metar ZSPD       ->  英文别称
    /weather ZGGG     ->  英文别称
    /预报 ZBAA        ->  机场天气预报 TAF（解码为中文，含主预报与 TEMPO/BECMG 时段）
    /taf ZSPD         ->  英文别称
    /机场 ZBAA        ->  机场静态信息（名称、IATA、坐标、磁差、跑道）
    /航图 ZBAA        ->  Jeppesen 航图清单（分类 + 直链 PNG）+ 在线查看入口
    /航图 ZBAA 进近   ->  仅列出进近图（可筛：进近/进场/离场/机场图，或 APP/ARR/DEP/APT）
    /charts ZSPD      ->  英文别称
    /通播 ZBAA        ->  机场通播：基于真实 METAR + 跑道生成（中国机场稳定可用）
    /atis ZSPD        ->  英文别称
    /datis ZGGG       ->  英文别称
    /fsd              ->  FSD 连线地址（仅限群 705230229；别名 /在线 /连线）
    /fsd名单          ->  空翼平台实时在线名单（飞行员+管制员，仅限群 705230229；别名 /在线名单 /名单 /roster）
    /飞机 B6392       ->  飞机实时定位：航班/经纬度/高度/速度/航向（飞友科技 VariFlight；别名 /定位 /plane）
    /航路 ZBAA ZSPD   ->  两机场间推荐航路查询（配置 route_api 或 MyChinaFlight 兜底；别名 /航线 /route /flightplan /rl）

API 返回示例：
    {
      "icao": "ZBAA",
      "data": {
        "name": "BEIJING-CAPITAL AIRPORT", "iataId": "PEK", "state": "BJ",
        "country": "CN", "lat": 40.07, "lon": 116.6, "elev": 31, "magdec": "6W",
        "runways": [{"id":"01/19","lengthM":3800,"widthM":60,"surface":"HARD","alignment":359}],
        "rawMETAR": "METAR ZBAA 101500Z ...",
        "rawTAF": "TAF ZBAA 100900Z ..."
      }
    }
"""

import asyncio
import ssl
from datetime import datetime

import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

# 兼容 AstrBot 的两种插件加载方式：包内相对导入 / 直接模块导入
try:
    from .metar_parser import parse_metar
    from .taf_parser import parse_taf
    from .atis_gen import format_broadcast
    from .charts import (
        fetch_charts,
        format_charts,
        viewer_url,
        pick_sample_charts,
        filter_charts_by_keyword,
        match_category_keyword,
        category_label,
    )
    from .variflight_client import (
        get_realtime_location,
        format_location,
    )
    from .route import (
        fetch_route,
        format_route,
    )
except ImportError:  # pragma: no cover
    import os
    import sys

    sys.path.insert(0, os.path.dirname(__file__))
    from metar_parser import parse_metar  # type: ignore
    from taf_parser import parse_taf  # type: ignore
    from atis_gen import format_broadcast  # type: ignore
    from charts import (  # type: ignore
        fetch_charts,
        format_charts,
        viewer_url,
        pick_sample_charts,
        filter_charts_by_keyword,
        match_category_keyword,
        category_label,
    )
    from variflight_client import (  # type: ignore
        get_realtime_location,
        format_location,
    )
    from route import (  # type: ignore
        fetch_route,
        format_route,
    )


# 真实机场数据源（覆盖中国及全球机场，含 METAR / TAF / 跑道信息）
DUBHE_BASE = "https://efb.dubhenexus.org/api/airports"
# 真实航图数据源（Jeppesen 航图，覆盖中国及全球机场）
DUBHE_CHARTS = "https://efb.dubhenexus.org/api/jeppesen"
# 天气(METAR)主源：KY飞行平台，覆盖国内机场更全，返回 {"code","message","data":["METAR ..."]}
KYFLY_METAR = "https://www.kyfly.online/api/metar"


@register(
    "astrbot_plugin_metar_weather",
    "WorkBuddy",
    "查询机场天气实况(METAR)、天气预报(TAF)、机场信息、航图入口与机场通播(ATIS)。使用 /天气 /预报 /机场 /航图 /通播 ICAO。",
    "2.0.0",
)
class MetarWeatherPlugin(Star):
    """机场天气 / 预报 / 通播插件"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        config = config or {}
        # 机场数据接口基础地址（最后不带 /ICAO）
        self.api_base: str = str(
            config.get("api_base", DUBHE_BASE)
        ).rstrip("/")
        # 天气(METAR)主源地址（/天气 优先用此源，查不到再回退 api_base）。
        self.metar_api: str = str(
            config.get("metar_api", KYFLY_METAR)
        ).strip().rstrip("/")
        # 请求超时时间（秒）
        self.timeout: int = int(config.get("timeout", 10))
        # 可选：独立部署的通播服务地址（如自建 server.py）。留空则直接用内置逻辑。
        self.atis_api: str = str(config.get("atis_api", "")).strip().rstrip("/")
        # 航图（Jeppesen）接口地址。默认使用 efb.dubhenexus.org 真实航图 API，
        # 覆盖中国及全球机场，返回每条航图的直链 PNG。
        self.charts_api: str = str(
            config.get("charts_api", DUBHE_CHARTS)
        ).strip().rstrip("/")
        # 航图规则：IFR / CVFR（VFR 多数机场为空）。
        self.charts_rules: str = str(config.get("charts_rules", "IFR")).strip().upper() or "IFR"
        # 航图「直发图片」数量（0 表示只给链接、不直发图片）。默认 6 张。
        self.charts_image_count: int = int(config.get("charts_image_count", 6))
        # FSD 连线地址（KY飞行平台 / 寰宇航空 网络），供在线飞行员与管制员连接
        self.fsd_address: str = str(config.get("fsd_address", "fsd.kyfly.online")).strip()
        self.fsd_port: int = int(config.get("fsd_port", 6809))
        # 该命令仅在此群号内生效（防止被其他群误用）
        self.fsd_allowed_group: str = str(
            config.get("fsd_allowed_group", "705230229")
        ).strip()
        # 飞友科技(VariFlight) Aviation API Key，用于 /飞机 实时定位查询
        self.variflight_api_key: str = str(
            config.get("variflight_api_key", "")
        ).strip()
        # 飞友科技 Aviation MCP (Streamable HTTP) 地址
        self.variflight_mcp_url: str = str(
            config.get("variflight_mcp_url", "https://ai.variflight.com/servers/aviation/mcp/")
        ).strip().rstrip("/")
        # 空翼(KY飞行)平台「在线客户端」接口：返回所有在线飞行员与管制员
        # （MCP 的 get_online_clients 即调此接口）。默认用标准 443 端口（whazzup v3
        # 格式），比 6810 端口更通用可达；6810 亦可用，可在配置里改。
        self.fsd_clients_api: str = str(
            config.get("fsd_clients_api", "https://www.kyfly.online/api/clients")
        ).strip().rstrip("/")
        # 在线名单每类（飞行员 / 管制员）最多展示条数，超出显示「还有 N 名」
        self.fsd_roster_limit: int = int(config.get("fsd_roster_limit", 30))
        # 航路查询主源接口（两机场间推荐航路）。支持 {dep}/{arr} 占位符，例如
        # https://example.com/route?from={dep}&to={arr} 或
        # https://example.com/api/route/{dep}/{arr}。留空则仅尝试 MyChinaFlight 兜底。
        self.route_api: str = str(config.get("route_api", "")).strip()
        # skylitefly 航路引擎 base URL（dubhenexus 前端调用的公开在线服务，
        # navigation.api.skylitefly.com/api/routes/plan）。默认启用，
        # 留空即关闭该在线数据源。
        self.skylitefly_api: str = str(
            config.get("skylitefly_api", "https://navigation.api.skylitefly.com/api/routes/plan")
        ).strip()
        # openRouteFinder 实例 base URL（开源飞行模拟航路算法引擎）。
        # 例如 http://127.0.0.1:9807 。该实例需在配置中设置 disable_captcha=true
        # 才能免验证码调用 /api/route。留空则不启用该数据源。
        self.openroutefinder_api: str = str(config.get("openroutefinder_api", "")).strip()

    # ------------------------------------------------------------------ #
    #  内部工具方法
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize_icao(event: AstrMessageEvent):
        """从消息中提取并强制大写的 ICAO 代码（仅取命令后的第一个 token）。

        例如「/航图 ZBAA 进近」→ 返回 "ZBAA"（"进近" 作为筛选词另行解析）。
        """
        text = (event.message_str or "").strip()
        parts = text.split()
        if len(parts) < 2:
            return ""
        return parts[1].strip().upper()

    @staticmethod
    def _extra_args(event: AstrMessageEvent):
        """返回命令后的其余词（不含 ICAO）。例如「/航图 ZBAA 进近」→ ["进近"]。"""
        text = (event.message_str or "").strip()
        parts = text.split()
        return parts[2:] if len(parts) > 2 else []

    async def _fetch_airport(self, icao: str):
        """拉取机场完整数据（METAR/TAF/跑道等）。返回 (data_dict|None, err_or_None)。"""
        url = f"{self.api_base}/{icao}"
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as resp:
                    if resp.status in (404, 500):
                        return None, "not_found"
                    if resp.status != 200:
                        return None, f"接口返回状态码 {resp.status}"
                    raw = await resp.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning(f"机场数据查询超时: {url}")
            return None, "请求超时"
        except aiohttp.ClientError as e:
            logger.error(f"机场数据网络请求异常: {e}")
            return None, f"网络请求出错：{e}"
        except Exception as e:  # noqa: BLE001 - 兜底，避免插件崩溃
            logger.error(f"机场数据查询未知异常: {e}")
            return None, f"查询出错：{e}"

        if not isinstance(raw, dict):
            return None, str(raw) if raw else "接口返回内容为空"
        data = raw.get("data")
        if not isinstance(data, dict):
            return None, raw.get("message", "未查询到该机场信息")
        return data, None

    async def _fetch_metar(self, icao: str):
        """从 KY飞行平台(kyfly) 拉取实时 METAR 报文。返回 (raw_metar|None, err_or_None)。

        接口形如 https://www.kyfly.online/api/metar?icao=ZBAA，返回：
            成功 {"code":"GET_METAR","message":"成功获取Metar","data":["METAR ZBAA ..."]}
            无数据 {"code":"METAR_NOT_FOUND","message":"未找到Metar信息","data":null}
        """
        if not self.metar_api:
            return None, "not_found"
        url = f"{self.metar_api}?icao={icao}"
        try:
            # trust_env=True：让 aiohttp 读取系统 HTTP(S)_PROXY 环境变量，
            # 否则直连不到需要走代理的源（如 kyfly）。
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as resp:
                    if resp.status == 404:
                        # kyfly 对该机场无数据（返回 404 或 code=METAR_NOT_FOUND）
                        return None, "not_found"
                    if resp.status != 200:
                        return None, f"接口返回状态码 {resp.status}"
                    raw = await resp.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning(f"METAR 查询超时: {url}")
            return None, "请求超时"
        except aiohttp.ClientError as e:
            logger.error(f"METAR 网络请求异常: {e}")
            return None, f"网络请求出错：{e}"
        except Exception as e:  # noqa: BLE001 - 兜底，避免插件崩溃
            logger.error(f"METAR 查询未知异常: {e}")
            return None, f"查询出错：{e}"

        if not isinstance(raw, dict):
            return None, "not_found"
        data = raw.get("data")
        # data 为报文数组（可能多条），取首条非空
        if isinstance(data, list):
            for item in data:
                if item and str(item).strip():
                    return str(item).strip(), None
            return None, "not_found"
        if isinstance(data, str) and data.strip():
            return data.strip(), None
        return None, "not_found"

    @staticmethod
    def _validate_icao(icao: str):
        return bool(icao) and icao.isalpha() and 3 <= len(icao) <= 4

    @staticmethod
    def _pick(d: dict, *keys, default="未知"):
        """从字典里按顺序取第一个存在的非空字段值（容错不同接口的键名）。"""
        if not isinstance(d, dict):
            return default
        for k in keys:
            v = d.get(k)
            if v is not None and str(v).strip() != "":
                return str(v).strip()
        return default

    @staticmethod
    def _format_client(c, kind: str) -> str | None:
        """把一名在线客户端（飞行员/管制员）格式化为一行文本。

        主要适配 VATSIM whazzup v3 格式（空翼/KY飞行 /api/clients 即此格式）：
        - 飞行员：callsign / name / cid / flight_plan.departure / flight_plan.arrival
          / altitude / groundspeed / transponder
        - 管制员：callsign / name / cid / frequency / rating / facility
        同时保留对其他键名的容错兜底。
        """
        if not isinstance(c, dict):
            return None
        cs = MetarWeatherPlugin._pick(
            c, "callsign", "call_sign", "cs", "callsign_str", default=""
        )
        if not cs:
            return None
        # 姓名 / 呼号备注
        nm = MetarWeatherPlugin._pick(
            c, "name", "realname", "real_name", "pilot_name", "controller_name", "nick", default=""
        )

        line = f"· {cs}"
        if nm and nm != cs:
            line += f"（{nm}）"

        if kind == "controller":
            # 管制员：频率 + 评级
            freq = MetarWeatherPlugin._pick(c, "frequency", "freq", "sector", default="")
            rating = MetarWeatherPlugin._pick(c, "rating", "rating_short", default="")
            if freq and freq != "未知":
                line += f"  {freq}"
            if rating and rating not in ("未知", "", "0"):
                line += f"  [{rating}]"
            return line

        # 飞行员：航线（whazzup v3 出发/到达嵌套在 flight_plan）
        fp = c.get("flight_plan")
        fp = fp if isinstance(fp, dict) else {}
        dep = MetarWeatherPlugin._pick(
            fp, "departure", "dep", default=""
        ) or MetarWeatherPlugin._pick(c, "dep", "dep_icao", "origin", "from", default="")
        arr = MetarWeatherPlugin._pick(
            fp, "arrival", "arr", default=""
        ) or MetarWeatherPlugin._pick(c, "arr", "arr_icao", "destination", "to", default="")
        if dep and arr:
            route = f"{dep}→{arr}"
        elif dep:
            route = f"{dep}→?"
        elif arr:
            route = f"?→{arr}"
        else:
            route = "未提交计划"
        line += f"  {route}"

        # 高度 / 地速
        alt = MetarWeatherPlugin._pick(c, "altitude", "alt", default="")
        gs = MetarWeatherPlugin._pick(c, "groundspeed", "gs", default="")
        tail = []
        if alt and alt not in ("未知", "", "0"):
            tail.append(f"{alt}ft")
        if gs and gs not in ("未知", "", "0"):
            tail.append(f"{gs}kt")
        if tail:
            line += "  " + " ".join(tail)
        return line

    async def _fetch_fsd_clients(self):
        """从空翼平台拉取在线客户端（飞行员 + 管制员）列表。

        返回 (dict|None, err_or_None)。默认走标准 443 端口（whazzup v3 格式）；
        若改用 6810 自签端口，则需跳过证书校验。这里统一跳过证书校验（对应
        curl -k），并 trust_env=True 走系统代理（与 /天气 同源教训）。
        """
        if not self.fsd_clients_api:
            return None, "未配置空翼在线客户端接口地址"
        url = self.fsd_clients_api
        try:
            # 跳过自签证书校验（对应 curl -k）
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            conn = aiohttp.TCPConnector(ssl=ssl_ctx)
            async with aiohttp.ClientSession(connector=conn, trust_env=True) as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as resp:
                    if resp.status != 200:
                        return None, f"接口返回状态码 {resp.status}"
                    raw = await resp.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning(f"空翼在线名单查询超时: {url}")
            return None, "请求超时"
        except aiohttp.ClientError as e:
            logger.error(f"空翼在线名单网络请求异常: {e}")
            return None, f"网络请求出错：{e}"
        except Exception as e:  # noqa: BLE001 - 兜底，避免插件崩溃
            logger.error(f"空翼在线名单查询未知异常: {e}")
            return None, f"查询出错：{e}"

        if not isinstance(raw, dict):
            return None, "接口返回格式异常"
        return raw, None

    # ------------------------------------------------------------------ #
    #  命令：天气
    # ------------------------------------------------------------------ #
    @filter.command("天气", alias={"metar", "weather"})
    async def weather(self, event: AstrMessageEvent):
        """查询机场实时天气(METAR)。用法：/天气 ZBAA（ICAO 大小写均可）"""
        icao = self._normalize_icao(event)
        if not icao:
            yield event.plain_result(
                "请输入机场 ICAO 代码，例如：/天气 ZBAA（北京首都机场）。"
            )
            return
        if not self._validate_icao(icao):
            yield event.plain_result(
                "ICAO 代码格式不正确，应为 3~4 位字母，例如 ZBAA、ZSPD、ZGGG。"
            )
            return

        # 主源：KY飞行平台(kyfly) METAR 接口
        raw_metar, merr = await self._fetch_metar(icao)

        # 机场静态信息（用于标题的机场名/IATA）；顺带作为 METAR 兜底源
        data, _derr = await self._fetch_airport(icao)
        if not raw_metar and isinstance(data, dict):
            raw_metar = (data.get("rawMETAR") or "").strip()

        # 两个源都没有报文 → 判定为未找到
        if not raw_metar:
            yield event.plain_result(
                "未找到该机场的天气信息，请确认 ICAO 代码是否正确（如 ZBAA、ZSPD、ZGGG）。"
                if merr in (None, "not_found")
                else f"❌ 查询失败：{merr}"
            )
            return

        name = (data.get("name") if isinstance(data, dict) else "") or ""
        title = f"✈️ {icao}"
        if isinstance(data, dict) and data.get("iataId"):
            title += f"({data['iataId']})"
        if name:
            title += f" {name.title()}"

        decoded = parse_metar(raw_metar)
        body = decoded if decoded else raw_metar
        result = f"{title} 天气实况：\n\n{body}\n\n📋 原始报文：\n{raw_metar}"

        yield event.plain_result(result)

    # ------------------------------------------------------------------ #
    #  命令：预报 (TAF)
    # ------------------------------------------------------------------ #
    @filter.command("预报", alias={"taf", "forecast"})
    async def taf(self, event: AstrMessageEvent):
        """机场天气预报(TAF)。用法：/预报 ZBAA（ICAO 大小写均可）"""
        icao = self._normalize_icao(event)
        if not icao:
            yield event.plain_result(
                "请输入机场 ICAO 代码，例如：/预报 ZBAA（北京首都机场）。"
            )
            return
        if not self._validate_icao(icao):
            yield event.plain_result(
                "ICAO 代码格式不正确，应为 3~4 位字母，例如 ZBAA、ZSPD、ZGGG。"
            )
            return

        data, err = await self._fetch_airport(icao)
        if data is None:
            yield event.plain_result(
                "未找到该机场的预报信息，请确认 ICAO 代码是否正确。"
                if err == "not_found"
                else f"❌ 查询失败：{err}"
            )
            return

        raw_taf = data.get("rawTAF") or ""
        if not raw_taf:
            yield event.plain_result(f"❌ {icao} 暂无 TAF 预报数据。")
            return

        decoded = parse_taf(raw_taf)
        result = (
            f"{decoded}\n\n📋 原始报文：\n{raw_taf}"
            if decoded
            else raw_taf
        )
        yield event.plain_result(result)

    # ------------------------------------------------------------------ #
    #  命令：机场 (静态信息)
    # ------------------------------------------------------------------ #
    @filter.command("机场", alias={"airport", "ap"})
    async def airport(self, event: AstrMessageEvent):
        """机场静态信息（名称/IATA/坐标/磁差/跑道）。用法：/机场 ZBAA"""
        icao = self._normalize_icao(event)
        if not icao:
            yield event.plain_result(
                "请输入机场 ICAO 代码，例如：/机场 ZBAA（北京首都机场）。"
            )
            return
        if not self._validate_icao(icao):
            yield event.plain_result(
                "ICAO 代码格式不正确，应为 3~4 位字母，例如 ZBAA、ZSPD、ZGGG。"
            )
            return

        data, err = await self._fetch_airport(icao)
        if data is None:
            yield event.plain_result(
                "未找到该机场的信息，请确认 ICAO 代码是否正确。"
                if err == "not_found"
                else f"❌ 查询失败：{err}"
            )
            return

        name = (data.get("name") or "").title()
        lines = [f"🏢 {icao} 机场信息"]
        if data.get("iataId"):
            lines.append(f"· IATA：{data['iataId']}")
        if name:
            lines.append(f"· 名称：{name}")
        if data.get("country"):
            lines.append(f"· 国家/地区：{data['country']}")
        if data.get("state"):
            lines.append(f"· 省/州：{data['state']}")
        if data.get("lat") is not None and data.get("lon") is not None:
            lines.append(f"· 坐标：{data['lat']}, {data['lon']}")
        if data.get("elev") is not None:
            lines.append(f"· 标高：{data['elev']} 米")
        if data.get("magdec"):
            lines.append(f"· 磁差：{data['magdec']}")
        runways = data.get("runways") or []
        if runways:
            lines.append(f"· 跑道（{len(runways)} 条）：")
            for r in runways:
                seg = f"    - {r.get('id','')}"
                if r.get("lengthM"):
                    seg += f" {r['lengthM']}m"
                if r.get("widthM"):
                    seg += f"×{r['widthM']}m"
                if r.get("surface"):
                    seg += f" {r['surface']}"
                lines.append(seg)
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ #
    #  命令：航图 (Charts)
    # ------------------------------------------------------------------ #
    @filter.command("航图", alias={"charts", "chart"})
    async def charts_cmd(self, event: AstrMessageEvent):
        """机场航图（Jeppesen）。用法：/航图 ZBAA [筛选词]

        基于 efb.dubhenexus.org 的真实航图接口，列出该机场的航图清单
        （分类：机场图/离场图/进场图/进近图）及每张航图的直链 PNG，
        并附上在线查看全部航图的网页入口（含中国机场资源）。

        可选筛选词（仅列出该分类，并只直发该分类图片）：
            进近 / APP      -> 进近图
            进场 / ARR/STAR -> 进场图
            离场 / DEP/SID  -> 离场图
            机场 / APT      -> 机场图
        例如：/航图 ZBAA 进近
        """
        icao = self._normalize_icao(event)
        if not icao:
            yield event.plain_result(
                "请输入机场 ICAO 代码，例如：/航图 ZBAA（北京首都机场）。"
            )
            return
        if not self._validate_icao(icao):
            yield event.plain_result(
                "ICAO 代码格式不正确，应为 3~4 位字母，例如 ZBAA、ZSPD、ZGGG。"
            )
            return

        # 解析筛选词（命令后第二个词，如「进近」「APP」「进场」）
        extra = self._extra_args(event)
        kw = " ".join(extra).strip()
        cat = match_category_keyword(kw) if kw else None

        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                charts = await fetch_charts(
                    icao,
                    self.charts_api,
                    self.charts_rules,
                    self.timeout,
                    session=session,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"航图接口请求失败: {e}")
            charts = []

        # 应用筛选
        filter_note = ""
        if charts and cat:
            matched = filter_charts_by_keyword(charts, kw)
            if matched:
                charts = matched
                filter_note = f"（已筛选：{kw} / {category_label(cat)}）"
            else:
                filter_note = f"（「{kw}」无匹配航图，展示全部）"

        if charts:
            rules = self.charts_rules
            sample = pick_sample_charts(charts, self.charts_image_count)
            header = (
                f"🛬 {icao} 航图（Jeppesen，共 {len(charts)} 张，规则 {rules}）{filter_note}\n"
                f"📤 以下为代表性 {len(sample)} 张图示："
            )
            # 优先把「文字 + 图片」打包成一条消息直发；失败则回退逐张发图
            if sample:
                try:
                    from astrbot.api.message_components import Plain, Image

                    chain = [Plain(header)]
                    for c in sample:
                        chain.append(Image.fromURL(c["image_day_url"]))
                    yield event.chain_result(chain)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"航图图片 chain 发送失败，回退逐张发送: {e}")
                    yield event.plain_result(header)
                    for c in sample:
                        try:
                            yield event.image_result(c["image_day_url"])
                        except Exception as ex:  # noqa: BLE001
                            logger.warning(f"航图图片发送失败: {ex}")
            else:
                yield event.plain_result(header)
            # 完整清单（按分类分组 + 直链），方便按需点开
            yield event.plain_result(
                format_charts(icao, charts, rules, max_items=40)
            )
            return

        # 接口无数据：至少给出网页查看入口
        yield event.plain_result(
            f"❌ 未从航图接口获取到 {icao} 的航图（rules={self.charts_rules}）。\n"
            f"你可以直接在网页查看：{viewer_url(icao)}"
        )

    # ------------------------------------------------------------------ #
    #  命令：通播 (ATIS)
    # ------------------------------------------------------------------ #
    @filter.command("通播", alias={"atis", "datis"})
    async def atis(self, event: AstrMessageEvent):
        """机场通播(ATIS)。基于真实 METAR + 跑道生成。用法：/通播 ZBAA"""
        icao = self._normalize_icao(event)
        if not icao:
            yield event.plain_result(
                "请输入机场 ICAO 代码，例如：/通播 ZBAA（北京首都机场）。"
            )
            return
        if not self._validate_icao(icao):
            yield event.plain_result(
                "ICAO 代码格式不正确，应为 3~4 位字母，例如 ZBAA、ZSPD、ZGGG。"
            )
            return

        # 可选：优先用独立部署的通播服务（server.py）
        if self.atis_api:
            try:
                async with aiohttp.ClientSession(trust_env=True) as session:
                    async with session.get(
                        f"{self.atis_api}?icao={icao}",
                        timeout=aiohttp.ClientTimeout(total=self.timeout),
                    ) as resp:
                        if resp.status == 200:
                            j = await resp.json(content_type=None)
                            txt = (
                                j.get("broadcast")
                                or j.get("text")
                                or (j.get("data") if isinstance(j.get("data"), str) else None)
                            )
                            if txt:
                                yield event.plain_result(str(txt))
                                return
            except Exception as e:  # noqa: BLE001
                logger.warning(f"通播独立服务请求失败，回退内置逻辑: {e}")

        # 基于真实 METAR + 跑道生成通播
        data, err = await self._fetch_airport(icao)
        if data is not None and data.get("rawMETAR"):
            yield event.plain_result(
                format_broadcast(icao, [data["rawMETAR"]], airport_data=data)
            )
            return

        yield event.plain_result(
            f"❌ 未查询到 {icao} 的通播或天气信息，请确认 ICAO 代码是否正确。"
        )

    # ------------------------------------------------------------------ #
    #  命令：飞机实时定位（飞友科技 VariFlight）
    # ------------------------------------------------------------------ #
    @filter.command("飞机", alias={"定位", "飞机定位", "plane", "flight", "acloc"})
    async def plane_cmd(self, event: AstrMessageEvent):
        """飞机实时定位。用法：/飞机 B6392（飞机注册号/机尾号，大小写均可）

        基于飞友科技(VariFlight) Aviation 数据，按飞机注册号查询该飞机当前
        实时定位：执行航班、出发地/目的地、经纬度、高度、速度、航向角、更新时间。
        """
        parts = (event.message_str or "").strip().split()
        if len(parts) < 2:
            yield event.plain_result(
                "请输入飞机注册号（机尾号），例如：/飞机 B6392。"
            )
            return
        anum = parts[1].strip().upper()
        if not anum or not (2 <= len(anum) <= 8) or not anum.replace("-", "").isalnum():
            yield event.plain_result(
                "注册号格式不正确，应为 2~8 位字母/数字，例如 B6392、B2021。"
            )
            return
        if not self.variflight_api_key:
            yield event.plain_result(
                "未配置飞友科技 API Key（variflight_api_key），无法查询飞机实时定位。"
            )
            return

        try:
            import asyncio

            loc = await asyncio.to_thread(
                get_realtime_location,
                anum,
                self.variflight_api_key,
                self.variflight_mcp_url,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"飞机实时定位查询失败: {e}")
            loc = None

        if not loc or loc.get("code") != 200:
            yield event.plain_result(
                f"❌ 未查询到 {anum} 的实时定位信息"
                f"（可能该飞机当前未在执飞，或注册号有误）。"
            )
            return

        out = format_location(loc, anum)
        yield event.plain_result(
            out or f"✈️ {anum}：未获取到有效定位数据。"
        )

    # ------------------------------------------------------------------ #
    #  命令：FSD 连线地址（仅限指定群）
    # ------------------------------------------------------------------ #
    @filter.command("fsd", alias={"在线", "连线", "地址"})
    async def fsd_cmd(self, event: AstrMessageEvent):
        """返回 KY飞行平台 / 寰宇航空 FSD 连线地址（供在线飞行员与管制员连接）。

        仅限配置中的群号（默认 705230229）使用；其他群或私聊会拒绝。
        用法：/fsd  （或 /在线、/连线）
        """
        allowed = (self.fsd_allowed_group or "").strip()
        gid = event.get_group_id()
        if not gid or str(gid).strip() != allowed:
            yield event.plain_result(
                f"⛔ 该命令仅限群 {allowed} 使用。"
            )
            return

        addr = self.fsd_address or "fsd.kyfly.online"
        port = self.fsd_port or 6809
        lines = [
            "🔗 KY飞行平台 / 寰宇航空 FSD 连线地址",
            f"· 服务器：{addr}",
            f"· 端口：{port}（默认 FSD 端口；如无法连接请向管理员确认）",
            "· 适用客户端：EuroScope / swift / vPilot / XSquawkbox 等",
            "· 官网：https://www.kyfly.online",
        ]
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ #
    #  命令：FSD 在线名单（仅限指定群）
    # ------------------------------------------------------------------ #
    @filter.command("fsd名单", alias={"在线名单", "fsd在线", "名单", "roster", "fsd列表"})
    async def fsd_roster(self, event: AstrMessageEvent):
        """返回空翼(KY飞行)平台实时在线名单（飞行员 + 管制员）。

        仅限配置中的群号（默认 705230229）使用；其他群或私聊会拒绝。
        用法：/fsd名单  （或 /在线名单、/名单、/roster）
        """
        allowed = (self.fsd_allowed_group or "").strip()
        gid = event.get_group_id()
        if not gid or str(gid).strip() != allowed:
            yield event.plain_result(
                f"⛔ 该命令仅限群 {allowed} 使用。"
            )
            return

        if not self.fsd_clients_api:
            yield event.plain_result("⚠️ 未配置空翼在线客户端接口地址（fsd_clients_api）。")
            return

        data, err = await self._fetch_fsd_clients()
        if err:
            yield event.plain_result(f"❌ 获取在线名单失败：{err}")
            return

        gen = data.get("general") or {}
        total = self._pick(gen, "connected_clients", "total", "count", default="?")
        pcount = self._pick(gen, "online_pilot", "pilots", "pilot_count", default="?")
        ccount = self._pick(gen, "online_controller", "controllers", "controller_count", default="?")
        pilots = data.get("pilots") or []
        controllers = data.get("controllers") or []
        if not isinstance(pilots, list):
            pilots = []
        if not isinstance(controllers, list):
            controllers = []

        limit = max(1, int(self.fsd_roster_limit))
        lines = [
            "🛰 空翼平台实时在线名单",
            f"· 总数 {total} ｜ 飞行员 {pcount} ｜ 管制员 {ccount}",
            "",
            f"✈️ 飞行员（{len(pilots)}）:",
        ]
        if pilots:
            for c in pilots[:limit]:
                ln = self._format_client(c, "pilot")
                if ln:
                    lines.append(ln)
            if len(pilots) > limit:
                lines.append(f"… 还有 {len(pilots) - limit} 名飞行员未显示")
        else:
            lines.append("（暂无在线飞行员）")

        lines.append("")
        lines.append(f"🎧 管制员（{len(controllers)}）:")
        if controllers:
            for c in controllers[:limit]:
                ln = self._format_client(c, "controller")
                if ln:
                    lines.append(ln)
            if len(controllers) > limit:
                lines.append(f"… 还有 {len(controllers) - limit} 名管制员未显示")
        else:
            lines.append("（暂无在线管制员）")

        lines.append("")
        lines.append(f"❄️ 更新于 {datetime.now():%H:%M:%S}")
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ #
    #  命令：航路查询（两机场间推荐航路）
    # ------------------------------------------------------------------ #
    @filter.command("航路", alias={"航线", "route", "flightplan", "rl", "航路查询"})
    async def route_cmd(self, event: AstrMessageEvent):
        """查询两机场之间的推荐航路。

        用法：/航路 出发ICAO 到达ICAO
        例如：/航路 ZBAA ZSPD  （别名 /航线 /route /flightplan /rl）
        数据源顺序：route_api（优先）→ openRouteFinder → MyChinaFlight 兜底。
        """
        parts = event.message_str.split()
        args = [p.strip().upper() for p in parts[1:] if p.strip()]
        if len(args) < 2:
            yield event.plain_result(
                "用法：/航路 出发ICAO 到达ICAO\n例如：/航路 ZBAA ZSPD"
            )
            return
        dep, arr = args[0], args[1]
        if not (self._validate_icao(dep) and self._validate_icao(arr)):
            yield event.plain_result(
                "ICAO 应为 3~4 位字母，例如 ZBAA、ZSPD、ZGGG。"
            )
            return

        result, err = await fetch_route(
            dep, arr,
            route_api=self.route_api,
            skylitefly_api=self.skylitefly_api,
            openroutefinder_api=self.openroutefinder_api,
            timeout=self.timeout, session=None,
        )
        if err or not result or not result.get("route"):
            yield event.plain_result(
                f"❌ 航路查询失败：{err or '无结果'}\n"
                f"（请确认已在插件配置填写 route_api 接口地址，或检查 "
                f"openRouteFinder / MyChinaFlight 是否可达）"
            )
            return

        # 尽力补全机场名（dubhenexus，失败时不影响主结果）
        dep_name = await self._airport_name(dep)
        arr_name = await self._airport_name(arr)
        yield event.plain_result(format_route(result, dep, arr, dep_name, arr_name))

    async def _airport_name(self, icao: str) -> str:
        """尽力从 dubhenexus 取机场名 + IATA（用于航路标题展示），失败返回空串。"""
        try:
            data, _ = await self._fetch_airport(icao)
            if isinstance(data, dict):
                name = data.get("name") or ""
                iata = data.get("iataId") or ""
                s = name.title() if name else ""
                if iata:
                    s = f"{s}({iata})" if s else iata
                return s
        except Exception:  # noqa: BLE001
            pass
        return ""