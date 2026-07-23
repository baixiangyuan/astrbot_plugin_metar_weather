"""
航图（Aeronautical Charts）功能模块
===================================

数据源：efb.dubhenexus.org 的 Jeppesen 航图接口（覆盖中国及全球机场）
    GET https://efb.dubhenexus.org/api/jeppesen?icao=ZBAA&rules=IFR
返回结构化 JSON，每条航图含：
    id / name / category / index_number / revision_date / is_georeferenced
    image_day_url / image_night_url / thumb_day_url / thumb_night_url（直链 PNG）

说明：
- 中国机场（ZBAA / ZSPD / ZGGG ...）及全球机场均有覆盖（Jeppesen 航图）。
- rules 可选 IFR / CVFR（VFR 多数机场为空）。默认 IFR。
- 在线查看全部航图的最佳入口是 dubhenexus 航图网页：
    https://efb.dubhenexus.org/charts?icao=ICAO
"""

# 航图分类中文名映射（Jeppesen category 字段）
_CATEGORY_CN = {
    "APT": "机场图",
    "DEP": "离场图(SID)",
    "ARR": "进场图(STAR)",
    "APP": "进近图",
    "GND": "地面图",
    "GEN": "通用",
    "REF": "参考",
}

# 网页查看入口（真正的「航图页面」）
WEB_VIEWER = "https://efb.dubhenexus.org/charts?icao={icao}"


def viewer_url(icao: str) -> str:
    """该机场航图在线总览页面（dubhenexus）。"""
    return WEB_VIEWER.format(icao=icao)


# 用户可能输入的筛选关键词 → Jeppesen category（按长度降序，便于精确匹配）
_CATEGORY_KEYWORDS = [
    ("进近图", "APP"), ("进近", "APP"), ("approach", "APP"), ("app", "APP"),
    ("进场图", "ARR"), ("进场", "ARR"), ("arrival", "ARR"), ("arr", "ARR"), ("star", "ARR"),
    ("离场图", "DEP"), ("离场", "DEP"), ("departure", "DEP"), ("dep", "DEP"), ("sid", "DEP"),
    ("机场图", "APT"), ("机场", "APT"), ("airport", "APT"), ("apt", "APT"),
    ("地面图", "GND"), ("地面", "GND"), ("ground", "GND"), ("gnd", "GND"),
    ("通用", "GEN"), ("gen", "GEN"),
    ("参考", "REF"), ("ref", "REF"),
]


def match_category_keyword(keyword: str):
    """把用户输入的筛选词（如「进近」「APP」「star」）匹配到 Jeppesen 分类。
    无匹配返回 None。
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return None
    for k, cat in _CATEGORY_KEYWORDS:
        if k in kw or kw in k:
            return cat
    return None


def filter_charts_by_keyword(charts: list, keyword: str) -> list:
    """按关键词筛选航图。若关键词无法识别，则返回原列表（不筛选）。"""
    cat = match_category_keyword(keyword)
    if not cat:
        return charts
    return [c for c in charts if (c.get("category") or "").upper() == cat]


def category_label(cat: str) -> str:
    """分类代码 → 中文标签（用于抬头展示）。"""
    return _CATEGORY_CN.get((cat or "").upper(), cat or "其他")


def parse_jeppesen(raw_json) -> list:
    """解析 Jeppesen 接口返回的 JSON，返回航图列表。

    返回元素结构：
        {
            "id", "name", "category", "index_number",
            "revision_date", "is_georeferenced",
            "image_day_url", "image_night_url", "thumb_day_url"
        }
    """
    charts: list = []
    if not isinstance(raw_json, dict):
        return charts
    data = raw_json.get("data")
    if not isinstance(data, dict):
        return charts
    items = data.get("charts") or []
    for it in items:
        if not isinstance(it, dict):
            continue
        day = it.get("image_day_url") or ""
        if not day:
            continue
        charts.append(
            {
                "id": it.get("id") or "",
                "name": it.get("name") or "",
                "category": it.get("category") or "",
                "index_number": it.get("index_number") or "",
                "revision_date": it.get("revision_date") or "",
                "is_georeferenced": it.get("is_georeferenced"),
                "image_day_url": day,
                "image_night_url": it.get("image_night_url") or "",
                "thumb_day_url": it.get("thumb_day_url") or "",
            }
        )
    return charts


async def fetch_charts(
    icao: str,
    base_url: str,
    rules: str = "IFR",
    timeout: int = 10,
    session=None,
):
    """从 dubhenexus Jeppesen 接口拉取航图清单。

    :param base_url: 例如 https://efb.dubhenexus.org/api/jeppesen
    :param rules:    IFR / CVFR（VFR 多数机场为空）
    :return: parse_jeppesen 解析后的航图列表（失败返回空列表）
    """
    import aiohttp

    url = f"{base_url.rstrip('/')}?icao={icao}&rules={rules}"
    own = session is None
    if own:
        session = aiohttp.ClientSession()
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
    except Exception:  # noqa: BLE001 - 调用方负责兜底
        return []
    finally:
        if own:
            await session.close()
    return parse_jeppesen(data)


def format_charts(icao: str, charts: list, rules: str = "IFR", max_items: int = 40):
    """把航图列表格式化为可读文本（含网页总览入口 + 直链）。"""
    if not charts:
        return (
            f"❌ {icao} 暂无 Jeppesen 航图数据（rules={rules}）。\n"
            f"你也可以在网页查看：{viewer_url(icao)}"
        )

    lines = [f"🛬 {icao} 航图（Jeppesen，共 {len(charts)} 张，规则 {rules}）"]

    # 按分类分组，保持出现顺序
    groups: dict = {}
    order: list = []
    for c in charts:
        g = c["category"] or "其他"
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(c)

    shown = 0
    for g in order:
        cn = _CATEGORY_CN.get(g, g)
        items = groups[g]
        lines.append(f"\n【{cn} / {g}】（{len(items)} 张）")
        for c in items:
            if shown >= max_items:
                lines.append(
                    f"… 其余 {len(charts) - shown} 张请在网页查看：{viewer_url(icao)}"
                )
                return "\n".join(lines)
            tag = f"[{c['index_number']}] " if c.get("index_number") else ""
            line = f"· {tag}{c['name']}"
            if c.get("revision_date"):
                line += f"（修订 {c['revision_date']}）"
            line += f"\n  {c['image_day_url']}"
            if c.get("is_georeferenced"):
                line += "  [可地理校准]"
            lines.append(line)
            shown += 1

    return "\n".join(lines)


def pick_sample_charts(charts: list, count: int = 6) -> list:
    """从航图清单中按分类轮询挑选若干张作为「直发图片」样本，保证类型覆盖。

    优先返回每个分类各 1 张，再逐类补齐，直到达到 count 或取完。
    这样即使 count 较小，也能覆盖机场图 / 离场 / 进场 / 进近等类型。
    """
    count = max(0, int(count))
    if count <= 0 or not charts:
        return []
    groups: dict = {}
    order: list = []
    for c in charts:
        g = c["category"] or "其他"
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(c)
    picked: list = []
    idx = {g: 0 for g in order}
    while len(picked) < count:
        progressed = False
        for g in order:
            if idx[g] < len(groups[g]) and len(picked) < count:
                picked.append(groups[g][idx[g]])
                idx[g] += 1
                progressed = True
        if not progressed:
            break
    return picked


if __name__ == "__main__":
    # 简易自测（同步部分，不发起网络请求）
    sample = {
        "data": {
            "charts": [
                {
                    "id": "ZBAA101P",
                    "name": "AIRPORT BRIEFING (GEN)",
                    "category": "APT",
                    "index_number": "10-1P",
                    "revision_date": "20260213",
                    "is_georeferenced": False,
                    "image_day_url": "https://charts-cdn.skylitefly.com/jeppesen/ZBAA/zbaa101p_d_20260213.png",
                    "image_night_url": "https://charts-cdn.skylitefly.com/jeppesen/ZBAA/zbaa101p_n_20260213.png",
                    "thumb_day_url": "https://charts-cdn.skylitefly.com/jeppesen/ZBAA/zbaa101p_thumb_d_20260213.png",
                }
            ]
        }
    }
    parsed = parse_jeppesen(sample)
    print("parse_jeppesen ->", parsed)
    print(format_charts("ZBAA", parsed))
    print("\nviewer:", viewer_url("ZBAA"))
