<div align="center">

# Bangumi 搜索插件使用指南
[![version](https://img.shields.io/badge/version-v1.5.1-blue.svg)](https://github.com/united-pooh/astrbot_plugin_bangumi)
[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE-2.0)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.26.2-orange.svg)](https://github.com/Soulter/AstrBot)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)

**和群友一起追番**
</div>

> **astrbot-plugin-bangumi** 是一个基于 AstrBot 框架的 Bangumi (番组计划) 信息查询与追番插件它通过对接 Bangumi API,为机器人用户提供精美的图文条目详情、实时放送时刻表,并具备自动化的订阅更新监控系统无论是想快速查询评分,还是在群内实时接收番剧更新通知,它都能为您提供优雅的交互体验



> [!NOTE]  
> 本项目在 [astrbot_plugin_bangumi](https://github.com/Amatsutsumi/astrbot_plugin_bangumi) 的基础上进行二次开发

## 📌 核心命令

### 1. 基础搜索(图文卡片)

| 命令 | 功能 | 参数 | 示例 |
|:-----|:-----|:-----|:-----|
| `/bgm` | 全类别搜索;空参数或 `help` 显示指令帮助 | `<关键词\|ID\|help> [top_k]` | `/bgm 进击的巨人 3` |
| `/bgm help` | 查看 Bangumi 指令帮助 | 无 | `/bgm help` |
| `/bgm番剧` | 仅搜索 TV 动画 | `<关键词\|ID> [top_k]` | `/bgm番剧 命运石之门` |
| `/bgm动漫` | `/bgm番剧` 的别名 | `<关键词\|ID> [top_k]` | `/bgm动漫 命运石之门` |
| `/bgm动画` | `/bgm番剧` 的别名 | `<关键词\|ID> [top_k]` | `/bgm动画 命运石之门` |
| `/bgm番` | `/bgm番剧` 的别名 | `<关键词\|ID> [top_k]` | `/bgm番 命运石之门` |
| `/bgm动画片` | `/bgm番剧` 的别名 | `<关键词\|ID> [top_k]` | `/bgm动画片 命运石之门` |
| `/bgm剧场版` | 仅搜索剧场版动画 | `<关键词\|ID> [top_k]` | `/bgm剧场版 凉宫春日的消失` |
| `/bgm电影` | `/bgm剧场版` 的别名 | `<关键词\|ID> [top_k]` | `/bgm电影 凉宫春日的消失` |
| `/bgm漫画` | 仅搜索漫画条目 | `<关键词\|ID> [top_k]` | `/bgm漫画 迷宫饭` |

> `top_k`(可选):返回结果数量,默认为 `1`

> 分类命令请使用连续形式,例如 `/bgm番剧 命运石之门`;不要在 `bgm` 和分类词之间加空格。

### 2. 放送与订阅

| 命令 | 功能 | 参数 | 示例 |
|:-----|:-----|:-----|:-----|
| `/calendar` | 获取番剧放送表 | 无 | `/calendar` |
| `/today` | 获取今日番剧更新 | 无 | `/today` |
| `/追番` | 订阅番剧,更新时自动通知 | `<关键词\|ID>` | `/追番 进击的巨人` |
| `/弃坑` | 取消订阅番剧 | `<关键词\|ID>` | `/弃坑 进击的巨人` |
| `/bgm模板` | 查看或切换图片卡片风格 | `[1\|2\|3\|模板名]` | `/bgm模板 1` |
| `/放送时间` | 查看所有已订阅番剧的放送时间,或查询/设置/清除指定番剧 | `[关键词\|ID] [HH:MM\|清空]` | `/放送时间` 查看全部，`/放送时间 尖帽子 22:00` 设置 |


**功能亮点**:
- **精美卡片**:自动生成包含封面、评分、排名、简介及剧集进度的图文卡片
- **每日放送**:渲染精美的每日放送时刻表
- **自动追番**:订阅后自动监控集数更新并实时推送通知

## 🛠️ 配置参数

在 AstrBot 的管理面板或配置文件中设置:

| 参数名 | 类型 | 默认值 | 说明 |
|:-------|:----:|:------:|:-----|
| `access_token` | string | 无 | Bangumi API 访问令牌(部分接口需授权)[¹](#access-token-获取) |
| `user_agent` | string | 无 | 请求头 User-Agent 标识,为空时使用插件默认值 |
| `max_fuzzy_results` | int | `5` | 模糊搜索最大返回数量(范围:1–200) |
| `proxy_http` | string | 无 | 代理主机或地址(例如 `192.168.0.1` 或 `http://127.0.0.1`;省略协议时按 `http://` 处理);启用后同时覆盖 Bangumi API、远程 RPC 渲染、本地 Playwright 和 Pillow 图片下载 |
| `port` | string | 无 | HTTP 代理端口(例如 `7890`;地址已带端口时仍需填写本项以启用代理) |
| `max_retries` | int | `3` | 网络错误最大重试次数(范围:1–10) |
| `render_server_url` | string | `https://api.unitedpooh.top/rpc` | 远程渲染图片的 RPC 服务器地址 |
| `render_mode` | string | `pillow` | 渲染模式;可选 `pillow`、`playwright`、`rpc`;旧配置值 `html` 会兼容为 `playwright` |
| `episode_card_template` | string | `pastel_lightbox` | 图片卡片风格;影响 `/bgm` 搜索结果、单集更新和长文本响应;可选 `pastel_lightbox`、`editorial_digest`、`cinematic_poster`,第一个为默认 |
| `auto_translate_episode_summary` | bool | `false` | 订阅更新渲染单集卡片前,使用 AstrBot 默认聊天模型将非空单集简介翻译为中文;无默认模型、返回空文本或翻译失败时保留原文 |

### Access Token 获取

虽然不强制,但建议配置 Access Token 以避免 API 限流

1. 注册/登录 [Bangumi](https://bgm.tv/)
2. 访问 [个人令牌页面](https://next.bgm.tv/demo/access-token/create) 创建新令牌
3. 将生成的 Token 填入插件配置的 `access_token` 字段

## 📦 环境依赖

插件首次运行时会检查 Playwright 环境状态,并在后台线程预热 Pillow 字体,不会阻塞主流程。字体预热会优先检测本地得意黑/Smiley Sans 缓存;不存在时从官方发布包拉取,拉取失败则退化为当前渲染默认字体。需要本地 HTML 渲染时,请准备以下依赖:
- **Playwright 浏览器内核**:用于 `render_mode=playwright` 的本地浏览器渲染

如果配置 `render_mode=pillow`,条目卡、单集卡、放送表和长文本响应卡会直接使用纯 Pillow 渲染。配置 `render_mode=rpc` 时会优先使用 `render_server_url` 指向的 RPC 渲染服务,失败后退化到 Pillow。

### 搜索结果卡片

`/bgm` 搜索结果卡片同步支持三种风格:`pastel_lightbox`、`editorial_digest`、`cinematic_poster`。默认使用 `pastel_lightbox`,也可以在 `_conf_schema.json` 的 `episode_card_template` 中配置,或通过 `/bgm模板 1`、`/bgm模板 2`、`/bgm模板 3` 指令切换。Playwright/RPC 链路会内嵌同一张 Pillow 预渲染图片,保持三种渲染模式的视觉一致。可用本地脚本从 Bangumi API 拉取真实条目数据生成预览图:

```bash
python scripts/render_subject_variants.py
```

生成结果会写入本地忽略目录 `rendered_images/subject-card-v1.5.1/`,用于用户审核前的可读性自检。

### 长文本响应卡片

普通命令响应会按长度自动选择输出方式:30 字以内且不含换行时仍发送纯文字;超过 30 字或包含换行时会使用当前 `episode_card_template` 对应的三种风格之一渲染为图片。可用本地脚本生成真实数据预览图:

```bash
python scripts/render_response_previews.py
```

生成结果会写入本地忽略目录 `rendered_images/response-card-v1.5.1/`,用于用户审核前的可读性自检。

### 单集卡片预览

单集更新卡片、搜索结果卡片和长文本响应卡片保留三种候选风格:`pastel_lightbox`、`editorial_digest`、`cinematic_poster`。默认使用第一个 `pastel_lightbox`,也就是当前粉彩灯箱式卡片;也可以在 `_conf_schema.json` 的 `episode_card_template` 中配置,或通过 `/bgm模板 1`、`/bgm模板 2`、`/bgm模板 3` 指令切换。订阅更新卡片可通过 `auto_translate_episode_summary=true` 在渲染前调用 AstrBot 默认聊天模型翻译非空单集简介,系统提示词固定为 `Translate to chinese (output translation only):`,单集简介会作为用户提示词单独传入;翻译不可用、返回空文本或失败时会继续使用 Bangumi 原简介。预览脚本只验证 Bangumi 原始数据的渲染效果,不会调用 AstrBot 聊天模型。三种模板都会通过 Pillow 输出,HTML 链路会内嵌同一张 Pillow 预渲染图片以保持像素级对齐。可用本地脚本从 Bangumi API 拉取真实条目与剧集数据,生成真实数据对比图:

```bash
python scripts/render_episode_variants.py
```

生成结果会写入本地忽略目录 `rendered_images/episode-card-variants/`,用于设计挑选和渲染回归检查。需要验证 HTML 与 Pillow 兼容性时,可同时生成像素对齐报告:

```bash
python scripts/render_episode_variants.py --verify-pixel-alignment
```

脚本默认搜索 `葬送的芙莉莲`,也可用 `--subject-id` 或 `--subject-query` 指定真实 Bangumi 条目;自动化测试仍可用 `--data-source fixture` 走离线夹具。

如果遇到环境问题,可尝试手动安装:
```bash
pip install -r requirements.txt
playwright install chromium
```

## ✅ 强类型与本地检查

本项目已切换为 Python 3.12 风格类型写法,并在 CI 中启用阻断式质量门禁(`ruff + mypy + pytest`)

### 本地测试 `.env`

可从 `_conf_schema.json` 生成本地测试用 `.env`,默认保留已有填写值:

```bash
python - <<'PY'
from astrbot_plugin_bangumi.src.utils.env_manager import EnvManager
EnvManager.generate_env_from_schema('_conf_schema.json', '.env', render_mode_default='pillow')
PY
```

### 本地执行命令

```bash
ruff check .
ruff format --check .
python -m mypy src main.py
python -m pytest -q
```

### 本地策略分析脚本

`scripts/analyze_bangumi_strategy.py` 是只读的本地分析工具,用于生成番剧管理策略报告,不会改变插件运行时行为、Bangumi 收藏或 AstrBot 订阅数据库。

```bash
python scripts/analyze_bangumi_strategy.py
python scripts/analyze_bangumi_strategy.py --db-path /path/to/data.db
```

脚本会读取项目 `.env`、环境变量和命令行参数中的 Bangumi 配置;传入 `--db-path` 时会只读读取 AstrBot SQLite 订阅数据。默认报告写入已忽略的 `.pipeline-workspace/`,避免把本地分析结果提交到仓库。

### 强类型编码规则

1. 禁止 `Optional[T]`,统一使用 `T | None`
2. 禁止 `typing.List/Dict/Tuple/Set`,统一使用 `list/dict/tuple/set`
3. 禁止新增 `Any`；优先使用 `TypedDict`、Pydantic 模型或明确类型别名
4. 公共方法必须显式标注参数和返回类型
5. 业务接口层禁止使用 `dict[str, Any]` 作为输入/输出类型
6. 需要可空时必须在类型中明确体现,禁止隐式可空
