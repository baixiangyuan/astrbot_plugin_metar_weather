"""航路查询：两机场间的推荐航路（多数据源兜底）。

数据源按以下顺序尝试，命中即返回：
  1. 用户自定义接口（配置 route_api，支持 {dep}/{arr} 占位符，最优先）
  2. skylitefly 航路引擎（dubhenexus 前端调用的公开在线服务
     navigation.api.skylitefly.com/api/routes/plan，默认启用、
     无需配置、含 SID/STAR 程序与导航周期，推荐作为默认在线源）
  3. openRouteFinder（开源飞行模拟航路算法引擎，POST /api/route，
     需自备导航数据 navdata_*.fb.zst 并关闭验证码：disable_captcha=true）
  4. MyChinaFlight（国内飞行圈常用航路库，best-effort 兜底）

解析做容错，兼容多种返回结构（JSON 或 HTML 文本），尽力提取
route（航路字符串）/ distance（距离）/ waypoints（航路点列表）。
不同源的字段名差异很大，这里用多种候选键名兜底；若你的源返回
结构特殊，把一份真实样本贴给开发者即可快速校准。
"""
import json
import re

import aiohttp


def _pick(d, *keys, default=None):
    if not isinstance(d, dict):
        return default
    for k in keys:
        v = d.get(k)
        if v is not None and str(v).strip() != "":
            return v
    return default


# 航路点 / 航段通常是 2+ 位大写字母数字（如 W19、PIKAS、G330、AKARA）
_ROUTE_TOKEN = re.compile(r"^[A-Z0-9]{2,}(?:[A-Z0-9/.\-]*)$")

# dubhenexus 前端调用的公开在线航路引擎（默认启用，无需配置）
SKYLITEFLY_API = "https://navigation.api.skylitefly.com/api/routes/plan"


def _extract_route_text(text):
    """从 HTML/文本里尽量抽取航路字符串（空格分隔的大写航段/航路点）。"""
    if not text:
        return None
    best = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        clean = re.sub(r"<[^>]+>", " ", line)          # 去 HTML 标签
        clean = re.sub(r"&[a-z]+;", " ", clean)         # 去实体
        clean = re.sub(r"\s+", " ", clean).strip()
        tokens = clean.split(" ")
        # 只保留航路点/航段（去掉纯数字、KM/NM 等单位）
        up = [
            t for t in tokens
            if _ROUTE_TOKEN.match(t) and not t.isdigit() and t not in ("KM", "NM")
        ]
        if len(up) >= 3 and (best is None or len(up) > len(best[1])):
            best = (clean, up)
    return " ".join(best[1]) if best else None


async def fetch_route(dep, arr, *, route_api="", skylitefly_api=SKYLITEFLY_API,
                       openroutefinder_api="", timeout=10, session=None):
    """返回 (result_dict|None, err|None)。result 至少含 route / source。

    skylitefly_api: skylitefly 航路引擎的 base URL（默认官方地址，留空即关闭）。
    openroutefinder_api: openRouteFinder 实例的 base URL（如 http://127.0.0.1:9807）。
      该实例需在配置中设置 disable_captcha=true 才能免验证码调用 /api/route。
    """
    dep = (dep or "").strip().upper()
    arr = (arr or "").strip().upper()
    if not (dep and arr):
        return None, "缺少起降机场 ICAO"

    last_err = None

    # 1) 用户自定义接口（最优先）
    if route_api:
        try:
            r, e = await _fetch_url(route_api, dep, arr, timeout, session)
            if r and r.get("route"):
                r["source"] = r.get("source") or "自定义接口"
                return r, None
            last_err = e or last_err
        except Exception as ex:  # noqa: BLE001
            last_err = f"自定义接口异常: {ex}"

    # 2) skylitefly 航路引擎（dubhenexus 前端调用的公开在线服务，默认启用）
    if skylitefly_api:
        try:
            r, e = await _fetch_skylitefly(
                dep, arr, skylitefly_api, timeout, session
            )
            if r and r.get("route"):
                r["source"] = r.get("source") or "skylitefly 航路引擎"
                return r, None
            last_err = e or last_err
        except Exception as ex:  # noqa: BLE001
            last_err = f"skylitefly 异常: {ex}"

    # 3) openRouteFinder（开源航路算法引擎）
    if openroutefinder_api:
        try:
            r, e = await _fetch_openroutefinder(
                dep, arr, openroutefinder_api, timeout, session
            )
            if r and r.get("route"):
                r["source"] = r.get("source") or "openRouteFinder"
                return r, None
            last_err = e or last_err
        except Exception as ex:  # noqa: BLE001
            last_err = f"openRouteFinder 异常: {ex}"

    # 4) MyChinaFlight（best-effort 兜底）
    try:
        r, e = await _fetch_mychinaflight(dep, arr, timeout, session)
        if r and r.get("route"):
            r["source"] = r.get("source") or "MyChinaFlight"
            return r, None
        last_err = e or last_err
    except Exception as ex:  # noqa: BLE001
        last_err = f"MyChinaFlight 异常: {ex}"

    return None, last_err or "所有数据源均无结果"


async def _fetch_openroutefinder(dep, arr, base_url, timeout, session):
    """调用 openRouteFinder 的 POST /api/route。

    依赖实例侧已设置 disable_captcha=true（否则需要验证码，无法自动调用）。
    返回 (result_dict|None, err|None)。
    """
    base = str(base_url).strip().rstrip("/")
    url = f"{base}/api/route"
    payload = {
        "orig": dep,
        "dest": arr,
        "validCode": "",
        "validToken": "",
        "sidExit": "",
        "starEntry": "",
    }
    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    own = session is None
    ses = session or aiohttp.ClientSession(trust_env=True)
    try:
        async with ses.post(url, json=payload, timeout=timeout_obj) as resp:
            if resp.status == 401:
                return None, "openRouteFinder 需要验证码（实例未设置 disable_captcha=true）"
            if resp.status == 404:
                return None, "openRouteFinder 未找到航路（机场代码错误或导航数据缺失）"
            if resp.status != 200:
                return None, f"openRouteFinder HTTP {resp.status}"
            try:
                data = await resp.json()
            except Exception:
                return None, "openRouteFinder 返回非 JSON"
        return _parse_openroutefinder(data, dep, arr), None
    finally:
        if own:
            await ses.close()


def _parse_openroutefinder(data, dep, arr):
    """解析 openRouteFinder /api/route 的响应 JSON。

    关键字段：
      route: 航路字符串（如 "ZBAA SID W40 ... ZGGG"）
      distance: "676.59 nm / 1253.05 km"
      nodes: [{name,lat,lon}, ...]  航路点序列
      activeSIDTransition / activeSTARTransition: 选中的进离场过渡
      airportDetails: {orig:{name,...}, dest:{name,...}}
    """
    if not isinstance(data, dict):
        return None
    route = _pick(data, "route")
    if not route:
        return None
    route = str(route).strip()

    # 距离：优先解析 "676.59 nm / 1253.05 km"
    dist_str = _pick(data, "distance")
    nm = km = None
    if dist_str:
        nums = re.findall(r"[\d.]+", str(dist_str))
        if len(nums) >= 2:
            nm, km = float(nums[0]), float(nums[1])
        elif len(nums) == 1:
            nm = float(nums[0])
    distance = {"value": nm, "unit": "nm"} if nm is not None else None

    # 航路点：优先用 nodes 列表，否则按空格切分航路字符串
    nodes = _pick(data, "nodes", "nodeinformation")
    wps = None
    if isinstance(nodes, list) and nodes:
        wps = [str(n.get("name") if isinstance(n, dict) else n) for n in nodes]
    if not wps:
        wps = [t for t in route.split() if _ROUTE_TOKEN.match(t)]

    extra = []
    if km is not None and nm is not None:
        extra.append(f"距离（双单位）：{nm:.0f} nm / {km:.0f} km")
    sid_t = _pick(data, "activeSIDTransition")
    star_t = _pick(data, "activeSTARTransition")
    if sid_t:
        extra.append(f"离场过渡 SID：{sid_t}")
    if star_t:
        extra.append(f"进场过渡 STAR：{star_t}")
    ad = _pick(data, "airportDetails")
    if isinstance(ad, dict):
        on = _pick(ad.get("orig") or {}, "name") if isinstance(ad.get("orig"), dict) else None
        dn = _pick(ad.get("dest") or {}, "name") if isinstance(ad.get("dest"), dict) else None
        if on:
            extra.append(f"起飞机场：{on}")
        if dn:
            extra.append(f"降落机场：{dn}")
    if wps:
        extra.append(f"航路点数量：{len(wps)}")

    return {
        "dep": dep,
        "arr": arr,
        "route": route,
        "distance": distance,
        "waypoints": wps,
        "source": "openRouteFinder",
        "extra": extra,
    }


async def _fetch_skylitefly(dep, arr, api, timeout, session):
    """调用 skylitefly 航路引擎 GET /api/routes/plan?origin={dep}&destination={arr}。

    这是 dubhenexus 航路查询页面（efb.dubhenexus.org/routes）实际调用的
    公开在线后端，免费、无需鉴权、无需自托管，默认作为在线航路源。
    返回 (result_dict|None, err|None)。
    """
    url = f"{str(api).strip().rstrip('/')}?origin={dep}&destination={arr}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    own = session is None
    ses = session or aiohttp.ClientSession(trust_env=True)
    try:
        async with ses.get(url, headers=headers, timeout=timeout_obj) as resp:
            if resp.status >= 500:
                return None, "skylitefly 服务端错误（HTTP 5xx）"
            if resp.status in (400, 404, 422):
                return None, "skylitefly 未找到该航路（机场代码错误或无可用航线）"
            if resp.status != 200:
                return None, f"skylitefly HTTP {resp.status}"
            try:
                data = await resp.json()
            except Exception:
                return None, "skylitefly 返回非 JSON"
        return _parse_skylitefly(data, dep, arr), None
    finally:
        if own:
            await ses.close()


def _parse_skylitefly(data, dep, arr):
    """解析 skylitefly /api/routes/plan 的响应 JSON。

    关键字段：
      route.string: 完整航路字符串（如 "ZBAA SID ELKUR W40 ... SASAN STAR ZSPD"）
      route.distance_nm / route.distance_km: 双单位距离
      route.route（含 airway_waypoints / waypoints / segments / waypoint_count）
      selected_departure / selected_arrival: 选中的 SID / STAR 程序
      nav_cycle: 导航数据周期 {name, start_date, end_date}
    """
    if not isinstance(data, dict):
        return None
    r = data.get("route")
    if not isinstance(r, dict):
        return None
    route_str = r.get("string")
    if not route_str or not str(route_str).strip():
        return None
    route_str = str(route_str).strip()

    nm = r.get("distance_nm")
    km = r.get("distance_km")
    distance = None
    if nm is not None:
        distance = {"value": float(nm), "unit": "nm"}
    elif km is not None:
        distance = {"value": float(km), "unit": "km"}

    # 航路点：优先 airway_waypoints（纯航路点），否则取完整 waypoints
    wps = None
    aw = r.get("airway_waypoints")
    if isinstance(aw, list) and aw:
        wps = [str(_pick(w, "ident", "name")) for w in aw if isinstance(w, dict)]
        wps = [x for x in wps if x]
    if not wps:
        w = r.get("waypoints")
        if isinstance(w, list) and w:
            wps = [
                str(x.get("ident") if isinstance(x, dict) else x) for x in w
            ]
            wps = [x for x in wps if x]

    extra = []
    if nm is not None or km is not None:
        if nm is not None and km is not None:
            extra.append(f"距离（双单位）：{nm:.0f} nm / {km:.0f} km")
        elif nm is not None:
            extra.append(f"距离：{nm:.0f} nm")
        else:
            extra.append(f"距离：{km:.0f} km")

    sd = data.get("selected_departure") or {}
    sa = data.get("selected_arrival") or {}
    if isinstance(sd, dict) and sd.get("procedure"):
        rw = sd.get("runway")
        extra.append(
            f"离场程序 SID：{sd['procedure']}" + (f"（跑道 {rw}）" if rw else "")
        )
    if isinstance(sa, dict) and sa.get("procedure"):
        rw = sa.get("runway")
        extra.append(
            f"进场程序 STAR：{sa['procedure']}" + (f"（跑道 {rw}）" if rw else "")
        )
    nc = data.get("nav_cycle")
    if isinstance(nc, dict) and nc.get("name"):
        rng = ""
        if nc.get("start_date") and nc.get("end_date"):
            rng = f"（{nc['start_date']} ~ {nc['end_date']}）"
        extra.append(f"导航数据周期：AIRAC {nc['name']} {rng}")

    return {
        "dep": dep,
        "arr": arr,
        "route": route_str,
        "distance": distance,
        "waypoints": wps,
        "source": "skylitefly 航路引擎",
        "extra": extra,
    }


async def _fetch_url(api, dep, arr, timeout, session):
    url = api
    if "{dep}" in url or "{arr}" in url:
        url = url.replace("{dep}", dep).replace("{arr}", arr)
    elif "?" in url:
        url = f"{url}&dep={dep}&arr={arr}"
    else:
        url = f"{url}?dep={dep}&arr={arr}"

    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    own = session is None
    ses = session or aiohttp.ClientSession(trust_env=True)
    try:
        async with ses.get(url, timeout=timeout_obj) as resp:
            if resp.status != 200:
                return None, f"HTTP {resp.status}"
            ctype = resp.headers.get("Content-Type", "")
            text = await resp.text()
        if "json" in ctype or text.lstrip().startswith("{"):
            try:
                data = json.loads(text)
            except Exception:
                data = text
        else:
            data = text
        return _parse_route(data, "自定义接口"), None
    finally:
        if own:
            await ses.close()


async def _fetch_mychinaflight(dep, arr, timeout, session):
    candidates = [
        f"https://www.mychinaflight.com/api/route?dep={dep}&arr={arr}",
        f"https://www.mychinaflight.com/route?dep={dep}&arr={arr}",
        f"https://mychinaflight.com/api/route?dep={dep}&arr={arr}",
    ]
    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    ses = session or aiohttp.ClientSession(trust_env=True)
    try:
        for url in candidates:
            try:
                async with ses.get(url, timeout=timeout_obj) as resp:
                    if resp.status != 200:
                        continue
                    ctype = resp.headers.get("Content-Type", "")
                    text = await resp.text()
                if "json" in ctype or text.lstrip().startswith("{"):
                    try:
                        data = json.loads(text)
                    except Exception:
                        data = text
                else:
                    data = text
                r = _parse_route(data, "MyChinaFlight")
                if r and r.get("route"):
                    return r, None
            except Exception:  # noqa: BLE001
                continue
        return None, "MyChinaFlight 未返回可用航路"
    finally:
        if session is None:
            await ses.close()


def _parse_route(raw, source):
    route = None
    dist = None
    wps = None
    if isinstance(raw, dict):
        route = _pick(raw, "route", "航路", "flyroute", "flight_route",
                      "routing", "plan", "route_text", "routeString")
        data = raw.get("data")
        if isinstance(data, dict):
            route = route or _pick(
                data, "route", "航路", "flyroute", "flight_route", "routing", "plan"
            )
        dist = _pick_distance(raw)
        wps = raw.get("waypoints") or (data or {}).get("waypoints")
        if isinstance(wps, list):
            wps = [str(w) for w in wps]
        else:
            wps = None
    else:
        route = _extract_route_text(str(raw))
        dist = None

    if not route:
        return None
    route = str(route).strip()
    return {
        "dep": None,
        "arr": None,
        "route": route,
        "distance": dist,
        "waypoints": wps,
        "source": source,
    }


def _pick_distance(raw):
    """从（可能嵌套的）返回里提取距离，返回 {"value","unit"} 或 None。

    优先用带单位暗示的键名（distance_nm / distance_km / 公里 / nm），
    模糊键（distance / dist / length）再按值内单位判定，默认公里。
    """
    if not isinstance(raw, dict):
        return None
    data = raw.get("data")
    merged = {**((data if isinstance(data, dict) else {})), **raw}
    cands = [
        ("distance_nm", "nm"),
        ("distance_km", "km"),
        ("公里", "km"),
        ("nm", "nm"),
        ("distance", "auto"),
        ("dist", "auto"),
        ("length", "auto"),
    ]
    for key, unit in cands:
        v = merged.get(key)
        if v is None:
            continue
        s = str(v).strip()
        m = re.search(r"(\d+(?:\.\d+)?)", s)
        if not m:
            continue
        num = float(m.group(1))
        if unit == "auto":
            low = s.lower()
            if "nm" in low or "海里" in s or "节" in s:
                u = "nm"
            elif "km" in low or "公里" in s:
                u = "km"
            else:
                u = "km"
        else:
            u = unit
        return {"value": num, "unit": u}
    return None


def format_route(result, dep, arr, dep_name="", arr_name=""):
    route = result.get("route", "")
    lines = [
        "🧭 航路查询",
        f"🛫 {dep}{(' ' + dep_name) if dep_name else ''}  →  🛬 {arr}{(' ' + arr_name) if arr_name else ''}",
        "",
        "推荐航路：",
        f"  {route}",
    ]
    wps = result.get("waypoints")
    if wps:
        lines.append("")
        lines.append(f"航路点（{len(wps)}）：")
        for i in range(0, len(wps), 8):
            lines.append("  " + " ".join(wps[i:i + 8]))
    dist = result.get("distance")
    if dist and isinstance(dist, dict):
        if dist["unit"] == "nm":
            lines.append(
                f"📏 距离：约 {dist['value']:.0f} nm（{dist['value'] * 1.852:.0f} km）"
            )
        else:
            lines.append(
                f"📏 距离：约 {dist['value']:.0f} km（{dist['value'] / 1.852:.0f} nm）"
            )
    extra = result.get("extra")
    if extra:
        lines.append("")
        for e in extra:
            lines.append(f"  · {e}")
    src = result.get("source")
    if src:
        lines.append(f"🛰 数据来源：{src}")
    return "\n".join(lines)
