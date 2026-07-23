# 机场天气 / 预报 / 通播插件（astrbot_plugin_metar_weather）

通过 [efb.dubhenexus.org](https://efb.dubhenexus.org/api/airports/ZBAA) **真实机场 API**，在 AstrBot 中查询全球（含中国）机场的 **实时天气(METAR)**、**天气预报(TAF)**、**机场信息** 与 **机场通播(ATIS)**。

## 功能

- 🌤 **实时天气** `/天气 ICAO`：优先使用 **KY飞行平台(kyfly.online)** METAR 源（国内机场覆盖更全），查不到时自动回退 dubhenexus；返回机场实时 METAR 并**自动解码**为可读中文（风、能见度、天气现象、云况、气温/露点、气压、趋势）。
- 📑 **天气预报** `/预报 ICAO`：返回机场 TAF，解码为主预报 + TEMPO/BECMG/FM/PROB 各时段的天气变化。
- 🏢 **机场信息** `/机场 ICAO`：返回名称、IATA、国家/地区、坐标、标高、磁差、跑道清单。
- 📻 **机场通播** `/通播 ICAO`：基于该机场**真实 METAR + 跑道信息**生成中文通播（中国机场稳定可用）。
- 🗺 **航图** `/航图 ICAO [筛选词]`：基于 [efb.dubhenexus.org](https://efb.dubhenexus.org/charts) 真实 **Jeppesen 航图接口**，列出该机场的航图清单（分类：机场图 / 离场图 / 进场图 / 进近图）及每张航图的直链 PNG。**默认还会直接把若干张代表性航图图片发到聊天里**（按分类轮询挑选，数量可配 `charts_image_count`），并附在线查看全部航图的网页入口（覆盖中国机场）。
  - 支持筛选词，仅列出并直发该分类航图：`进近`/`APP` → 进近图，`进场`/`ARR`/`STAR` → 进场图，`离场`/`DEP`/`SID` → 离场图，`机场`/`APT` → 机场图。例如 `/航图 ZBAA 进近`。
- 🔗 **FSD 连线地址** `/fsd`（别名 `/在线` `/连线`）：返回 KY飞行平台 / 寰宇航空 网络的 FSD 连线地址（`fsd.kyfly.online`），供在线飞行员与管制员连接客户端（EuroScope / swift / vPilot 等）。**该命令仅限群 `705230229` 使用**，其他群或私聊会拒绝（群号可配 `fsd_allowed_group`）。
- ✈️ **飞机实时定位** `/飞机 注册号`（别名 `/定位` `/plane` `/flight`）：基于 [飞友科技 VariFlight](https://ai.variflight.com) Aviation 数据，按 **飞机注册号/机尾号**（如 `B6392`）查询该飞机**当前实时定位**：执行航班号、出发地→目的地、经纬度、高度、速度、航向角、数据更新时间。纯 Python 标准库实现 MCP 客户端，无需 Node.js / npx。需配置 `variflight_api_key`（在 [飞友 AI 开放平台](https://ai.variflight.com) 申请）。
- 🧭 **航路查询** `/航路 出发 到达`（别名 `/航线` `/route` `/flightplan` `/rl`）：查询两机场之间的**推荐航路**。多数据源兜底：① `route_api`（自定义接口，支持 `{dep}`/`{arr}` 占位符，最优先）；② **[openRouteFinder](https://github.com/gtxzsxxk/openRouteFinder)**（开源飞行模拟航路算法引擎，Dijkstra 计算最短航路，含 SID/STAR 进离场过渡，需自备导航数据并关闭验证码）；③ MyChinaFlight（best-effort 兜底）。
- 全部命令 **ICAO 大小写均可**（输入小写 `zbaa` 会自动转大写查询）。
- 数据源覆盖中国机场（ZBAA / ZSPD / ZGGG / ZUUU / ZJSY / ZSHC …）及国际（东京 RJTT、纽约 KJFK …）。

## 使用方法

在任意已接入的聊天平台（QQ / 微信 / Telegram 等）中发送：

```
/天气 ZBAA        # 实时天气实况（小写 /weather zbaa 亦可，自动转大写）
/预报 ZBAA        # 天气预报 TAF
/机场 ZBAA        # 机场静态信息（跑道 / 坐标等）
/航图 ZBAA        # Jeppesen 航图清单 + 直链（小写 /charts zbaa 亦可）
/航图 ZBAA 进近   # 仅列出进近图（可筛：进近/进场/离场/机场图，或 APP/ARR/DEP/APT）
/fsd              # 返回 FSD 连线地址（仅限群 705230229；别名 /在线 /连线）
/飞机 B6392       # 飞机实时定位（别名 /定位 /plane /flight）
/通播 ZBAA        # 机场通播（基于真实 METAR + 跑道生成）
/航路 ZBAA ZSPD   # 两机场间推荐航路（别名 /航线 /route /flightplan /rl）
```

天气返回示例（已解码）：

```
✈️ ZBAA(PEK) Beijing-Capital Airport 天气实况：

📍 机场：ZBAA
🕐 观测时间：10日 15:00 UTC
💨 风：010° 1 米/秒
👁 能见度：8000 米（约 8.0 公里）
☁️ 云况：疏云(SCT) 1300 英尺（约 396 米）、阴天(OVC) 4000 英尺（约 1219 米）
🌡 气温：25°C　露点：25°C
📊 修正海压(QNH)：1003 hPa
🔮 趋势/补充：无明显变化

📋 原始报文：
METAR ZBAA 101500Z 01001MPS 8000 SCT013 OVC040 25/25 Q1003 NOSIG
```

预报返回示例（已解码）：

```
📑 ZBAA 机场天气预报 (TAF)
🕐 发布时间：10日 09:00 UTC
⏳ 有效时段：10日 12:00 - 11日 18:00 UTC

🟢 主预报
💨 风：160° 4 米/秒
👁 能见度：5000 米（约 5.0 公里）
🌦 天气现象：轻雾
☁️ 云况：多云(BKN) 4000 英尺（约 1219 米）

🔸 短时波动 (TEMPO)（10日 12:00 - 10日 18:00 UTC）
🌦 天气现象：雷暴雨
☁️ 云况：多云(BKN) 4000 英尺（约 1219 米）

🌡 温度预报：最高气温 29°C（11日 07:00 UTC）；最低气温 24°C（10日 21:00 UTC）
```

常用机场 ICAO 代码示例：

| 机场 | ICAO |
| --- | --- |
| 北京首都 | ZBAA |
| 上海浦东 | ZSPD |
| 广州白云 | ZGGG |
| 深圳宝安 | ZGSZ |
| 成都双流 | ZUUU |
| 三亚凤凰 | ZJSY |

## 安装

### 方式一：本地放置（推荐开发 / 调试）

将本插件文件夹 `astrbot_plugin_metar_weather` 复制到 AstrBot 的插件目录下：

```
AstrBot/data/plugins/astrbot_plugin_metar_weather
```

重启 AstrBot 或在 WebUI 的插件管理中点击「重载插件」即可生效。

### 方式二：Git 仓库安装

在 AstrBot WebUI 的插件市场 / 命令行中执行：

```
plugin i <本仓库的 git 地址>
```

## 配置项（`_conf_schema.json`）

| 配置项 | 说明 | 默认值 |
| --- | --- | --- |
| `api_base` | 机场数据接口基础地址（最后不带 `/ICAO`），用于 TAF/机场信息，及 METAR 兜底 | `https://efb.dubhenexus.org/api/airports` |
| `metar_api` | 天气(METAR)主源接口地址（`/天气` 优先用，查不到自动回退 `api_base`） | `https://www.kyfly.online/api/metar` |
| `timeout` | 请求超时时间（秒） | `10` |
| `atis_api` | 独立部署的通播服务地址（如 `http://127.0.0.1:8000`）；留空则用插件内置逻辑 | 空 |
| `charts_api` | 航图(Jeppesen)接口地址 | `https://efb.dubhenexus.org/api/jeppesen` |
| `charts_rules` | 航图规则：`IFR` / `CVFR`（VFR 多数机场为空） | `IFR` |
| `charts_image_count` | `/航图` 直接发到聊天的航图图片数量（0=只发文字链接） | `6` |
| `fsd_address` | FSD 服务器地址（`/fsd` 命令返回） | `fsd.kyfly.online` |
| `fsd_port` | FSD 端口 | `6809` |
| `fsd_allowed_group` | `/fsd` 仅在指定群号生效 | `705230229` |
| `variflight_api_key` | 飞友科技 API Key（`/飞机` 实时定位用，[申请](https://ai.variflight.com)） | 已内置一个 Key |
| `variflight_mcp_url` | 飞友科技 Aviation MCP 地址 | `https://ai.variflight.com/servers/aviation/mcp/` |
| `route_api` | 航路查询最高优先数据源接口，支持 `{dep}`/`{arr}` 占位符（如 `https://example.com/route?from={dep}&to={arr}`）；留空则跳到默认在线源 skylitefly | 空 |
| `skylitefly_api` | 默认在线航路引擎（dubhenexus 前端调用的公开服务 `navigation.api.skylitefly.com/api/routes/plan`）。免费、无需鉴权、无需自托管，作为首选在线源；留空则关闭 | `https://navigation.api.skylitefly.com/api/routes/plan` |
| `openroutefinder_api` | 开源航路引擎 openRouteFinder 实例 base URL（如 `http://127.0.0.1:9807`）。需该实例设置 `disable_captcha=true` 且已加载 `navdata_*.fb.zst` 导航数据；留空则不启用 | 空 |

## 关于「航图」的数据来源

航图使用 [efb.dubhenexus.org](https://efb.dubhenexus.org/charts) 的 **Jeppesen 航图接口**
（`GET /api/jeppesen?icao=ICAO&rules=IFR`），覆盖中国机场（ZBAA / ZSPD / ZGGG …）及全球机场，
返回每张航图的直链 PNG（来自 `charts-cdn.skylitefly.com`）。`/航图` 命令会列出分类清单与直链，
并附带在线查看全部航图的网页入口（`https://efb.dubhenexus.org/charts?icao=ICAO`）。

## 关于「机场通播」的数据来源

公开免费接口对中国机场的**真实数字通播(D-ATIS)文本**覆盖极少。本插件使用 [efb.dubhenexus.org](https://efb.dubhenexus.org/api/airports/ZBAA) 提供的**真实 METAR 实况 + 跑道信息**生成中文机场通播，保证中国机场稳定可用。

如你单独部署了通播服务（见下），可在 `atis_api` 中填写其地址，让 `/通播` 优先走自建服务。

## 独立部署通播 / 天气 / 预报服务（可选）

如需把能力单独对外提供（例如多机器人共用），可运行附带的服务：

```bash
pip install aiohttp
python server.py                 # 默认监听 0.0.0.0:8000
python server.py --host 127.0.0.1 --port 9000
```

接口：

```
GET /api/atis?icao=ZBAA    ->  {"ok": true, "icao": "ZBAA", "source": "metar", "broadcast": "..."}
GET /api/weather?icao=ZBAA ->  {"ok": true, "icao": "ZBAA", "raw": "METAR...", "decoded": "..."}
GET /api/taf?icao=ZBAA     ->  {"ok": true, "icao": "ZBAA", "raw": "TAF...", "decoded": "..."}
GET /health                ->  {"ok": true, "service": "airport-atis"}
```

部署后，在插件配置里填 `atis_api = http://<服务器>:8000` 即可走自建服务。
环境变量：`DUBHE_BASE`、`ATIS_TIMEOUT` 可覆盖默认值。

## 关于「航路查询」的数据源

`/航路 出发 到达` 按以下顺序尝试，命中即返回：

1. **`route_api`（最优先，自定义）**：你自己的航路接口，地址里用 `{dep}`/`{arr}` 占位（如 `https://example.com/route?from={dep}&to={arr}` 或 `https://example.com/api/route/{dep}/{arr}`）。插件会对返回做容错解析（JSON 或 HTML 均可）。
2. **skylitefly 航路引擎（默认在线，推荐）**：`skylitefly_api` 指向的公开在线服务 `navigation.api.skylitefly.com/api/routes/plan`（即 `efb.dubhenexus.org/routes` 页面实际调用的后端）。**免费、无需鉴权、无需自托管**，默认启用，返回完整航路字符串、双单位距离（nm/km）、SID/STAR 进离场程序与 AIRAC 导航周期，是日常使用的首选在线源。
3. **openRouteFinder（开源算法引擎）**：向 `openroutefinder_api` 指向的实例 `POST /api/route`，用 Dijkstra 计算两机场间最短航路，并附带 SID/STAR 进离场过渡、双单位距离（nm/km）、航路点序列。
4. **MyChinaFlight（兜底）**：best-effort 兜底，可能不可达。

### 自托管 openRouteFinder（可选，推荐用于稳定航路）

[openRouteFinder](https://github.com/gtxzsxxk/openRouteFinder) 是一个 Python + FastAPI 的开源航路计算引擎（MIT 协议，可自由使用/修改）。它需要 **导航数据文件 `navdata_*.fb.zst`**（由 Fenix A320 的 `nd.db3` 经其管理后台转换得到，`data/` 目录）才能计算航路——**仓库不含该数据，需自备**。

启动步骤（已在 `openroutefinder_api` 填写实例地址后生效）：

```bash
# 1) 克隆并安装依赖
git clone https://github.com/gtxzsxxk/openRouteFinder.git
cd openRouteFinder
pip install -r requirements.txt

# 2) 准备导航数据：把 navdata_XXXX.fb.zst 放到 data/ 目录
#    （来源：Fenix A320 nd.db3 → 后台 /api/admin/navdata/upload 转换，或他人分享的 .fb.zst）

# 3) 关闭验证码（让插件可免验证自动调用 /api/route）
#    在 .env 中设置：DISABLE_CAPTCHA=true

# 4) 启动后端（默认 :9807）
cd openRouterFinder && uvicorn api:app --host 0.0.0.0 --port 9807
```

然后在插件配置里填 `openroutefinder_api = http://<服务器IP>:9807`。

> 注意：未关闭验证码（`disable_captcha=false`）时，`/api/route` 会返回 401，插件会提示「openRouteFinder 需要验证码」。请务必在实例侧设置 `DISABLE_CAPTCHA=true`。

## 目录结构

```
astrbot_plugin_metar_weather/
├── main.py            # 插件主逻辑（指令 / 网络请求 / 结果组装）
├── route.py           # 航路查询（多数据源兜底：route_api / skylitefly / openRouteFinder / MyChinaFlight）
├── metar_parser.py    # METAR 报文解码为可读中文
├── taf_parser.py      # TAF 预报报文解码为可读中文
├── atis_gen.py        # 通播生成与格式化
├── server.py          # 可独立部署的 HTTP 服务（天气/预报/通播，可选）
├── metadata.yaml      # 插件元信息
├── _conf_schema.json  # 配置项定义
├── requirements.txt   # 依赖
└── README.md
```

## 依赖

- `aiohttp`（AstrBot 核心已自带，此处显式声明以保证完整性）

## 说明

METAR / TAF 报文为原始气象报文，包含风向风速、能见度、云量、温度露点、气压等信息，供飞行相关场景参考。本插件仅做转发展示与解码，不负责报文解读。
