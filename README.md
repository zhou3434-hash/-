# 旅行出行助手

基于 **DeepSeek** 的旅游规划智能体。支持多轮记忆、出行方案规划、游玩措施推荐、旅行费用规划，
以及**景点图片识别**（发一张照片，它告诉你这是哪、怎么玩）。

提供**命令行**与**网页**两种界面，共用同一套智能体核心。

---

## 快速开始

### 1. 环境准备

需要 Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)。首次运行：

```powershell
cd E:\deekseek-workplace\bookfindweb
uv venv --python 3.14
uv sync --extra dev
```

### 2. 配置密钥

复制 `.env.example` 为 `.env`，填入你的 DeepSeek API Key：

```ini
DEEPSEEK_API_KEY=sk-你的密钥
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_MAX_TOKENS=4000
```

> `.env` 已在 `.gitignore` 中，不会被提交。

### 3. 运行

**推荐用 PowerShell 启动器**（编码处理正确，不会乱码）：

| 方式 | 文件 |
|---|---|
| 网页界面 | 双击 `启动网页.ps1` —— 服务在后台运行，自动开浏览器 |
| 命令行对话 | 双击 `启动命令行.ps1` |
| 停止网页服务 | 双击 `停止服务.ps1` |
| 出问题时 | 双击 `诊断.ps1` —— 6 项自检并给出结论 |

如果双击 `.ps1` 没反应，用右键「使用 PowerShell 运行」，或在 PowerShell 里执行：

```powershell
cd E:\deekseek-workplace\bookfindweb
powershell -ExecutionPolicy Bypass -File .\启动网页.ps1
```

备用的 `.bat` 启动器（同样可用，但更脆弱）：`START-WEB.bat`、`START-CLI.bat`、`诊断.bat`。

手动运行：

```powershell
$env:PYTHONPATH="$PWD\src"
.\.venv\Scripts\python.exe -m travel_assistant.cli        # 命令行
.\.venv\Scripts\python.exe -m travel_assistant.server     # 网页
```

> 网页版的服务在后台运行（`启动网页.ps1` 用 `Start-Process` 隐藏窗口启动），
> 关闭启动器窗口不影响服务。要停服务请运行 `停止服务.ps1`。
>
> 若网页显示 `TypeError: Failed to fetch`，含义是**浏览器连不上后端服务**，
> 先运行 `停止服务.ps1` 再重新 `启动网页.ps1`。

### 4. 排错

**症状：网页能打开，但一发送就 `TypeError: Failed to fetch`**

这个错误的含义很明确：**浏览器连不上后端服务**（不是模型出错、不是密钥问题）。
最常见原因是启动服务的窗口被关闭了。

排查顺序：

1. 双击 `诊断.ps1`，它会依次检查：虚拟环境、依赖导入、`.env` 密钥、8000 端口占用、
   服务能否启动并响应 `/api/health`、关键文件是否存在
2. 若提示「8000 已被占用」，说明已有服务在跑，直接刷新网页即可
3. 若一切正常但仍连不上，运行 `停止服务.ps1` 清掉残留进程后重试

---

## 功能

### 出行方案规划
`plan_itinerary` 工具按**地理聚类 + 评分 + 负载均衡**排布逐日行程：
同一天的景点尽量顺路（最近邻排序减少折返），单日时长不超过所选节奏上限，
并输出时间轴、门票合计与当日注意事项。

节奏分「轻松 / 标准 / 紧凑」；主题分「综合 / 亲子 / 摄影 / 历史人文 / 自然风光 / 美食 / 休闲度假 / 省钱 / 夜景」；
勾选「有老人/有儿童」会进一步压缩单日强度并提示台阶、排队等风险点。

### 游玩措施推荐
不只列景点，还给**怎么玩**：最佳时段、避坑提示、排队策略、交通接驳、拍照机位。
每个景点都带 1-2 条实测经验（如「故宫周一闭馆，需提前 7 天预约」「熊猫基地 7:30 开园，务必早到」）。

### 旅行费用规划
`estimate_budget` 输出六项明细：**城际交通 / 住宿 / 餐饮 / 门票 / 市内交通 / 购物及其他**，
并给出总计、人均、人均日均。城际交通按**球面距离 × 单价**推算并建议飞机或高铁；
消费档位分「经济 / 舒适 / 高端」。若先调用了行程规划，会把实际门票合计带入，比日均粗估更准。

### 景点图片识别
`deepseek-flash` 模型自带视觉能力，**无需额外模型或第三方服务**。

识别流程：图片 → base64 → 模型输出结构化 JSON（地标、城市、国家、**坐标**、置信度）
→ 反查地区 → 查周边景点 → 查天气 → 生成游玩方案。

支持拖拽上传、剪贴板粘贴；命令行用 `/img <路径>`。

### 记忆系统（两层）

| 层级 | 存放 | 作用 |
|---|---|---|
| **短期记忆** | SQLite，按会话 | 保留该会话**完整**对话历史，注入上下文时截取最近 N 轮，避免费用无限增长 |
| **长期记忆** | SQLite，跨会话 | 自动抽取用户画像：常住城市、预算、同行人、忌口、出行风格、去过的/想去的…下次直接复用，**不重复提问** |

长期记忆抽取有**字段白名单**（见 `memory.py` 的 `PROFILE_KEYS`），模型无法往画像里塞任意键。
命令行用 `/memory` 查看、`/forget <字段>` 删除、`/clear` 清空。

---

## 架构

```
src/travel_assistant/
├── config.py        配置（.env 加载 + 启动校验）
├── models.py        数据模型（Pydantic）
├── llm.py           DeepSeek 客户端（含思维链防护与重试）
├── memory.py        两层记忆（SQLite）
├── vision.py        图片识别（结构化 JSON 输出 + 容错解析）
├── agent.py         智能体主循环（工具编排 + 记忆注入 + 事件流）
├── cli.py           命令行界面（Rich）
├── server.py        FastAPI 服务（SSE 流式）
└── tools/           工具层
    ├── registry.py        注册表（生成 function calling 声明）
    ├── base.py            工具基类（异常兜底）
    ├── attractions.py     景点检索 / 详情 / 周边
    ├── planner.py         行程规划（装箱 + 再平衡 + 最近邻）
    ├── budget.py          费用预算
    ├── weather.py         天气（Open-Meteo）
    ├── geo.py             坐标反查地区
    ├── geocode.py         地名转坐标（模型知识兜底）
    ├── cities.py          城市数据与费用系数
    └── attractions_data.py 景点知识库（83 个景点 / 21 座城市）
```

**设计取舍**：智能体主循环产出的是一串**结构化事件**（status / vision / tool_call /
tool_result / final / error），CLI 与 Web 各自渲染，核心逻辑只写一遍。

---

## 内置数据

覆盖 **21 座城市、83 个景点**：北京、上海、杭州、成都、西安、厦门、三亚、丽江、桂林、青岛、
重庆、长沙、都江堰、峨眉山、苏州、南京、张家界、黄山，以及东京、巴黎、曼谷。

每条景点含门票、建议时长、评分、最佳季节、坐标与实用贴士。数据为公开资料整理的经验值。

外部 API 全部**免费且无需密钥**：Open-Meteo（天气）、BigDataCloud（坐标反查）。

---

## 测试

```powershell
$env:PYTHONPATH="$PWD\src"
.\.venv\Scripts\python.exe -m pytest -q
```

当前 **81 个测试全部通过**，覆盖：数据完整性、城市名归一化、7 个工具的行为边界、
两层记忆（含短期窗口截断与工具消息配对）、图片编码与容错 JSON 解析、配置校验。

---

## 命令行命令

| 命令 | 说明 |
|---|---|
| `/help` | 帮助 |
| `/new` | 新会话（保留长期记忆） |
| `/memory` | 查看长期记忆 |
| `/forget <字段>` | 删除某条记忆 |
| `/clear` | 清空长期记忆 |
| `/trips` | 历史行程归档 |
| `/cities` | 内置城市与景点 |
| `/img <路径> [说明]` | 发送图片识别 |
| `/exit` | 退出 |

另有一次性参数：`-m "问题"`、`--image 路径`、`--memory`、`--tools`、`--cities`、`--sessions`、`--trips`。

---

## 重要实现说明（踩过的坑）

### 1. 思维链会吃光 token，导致正文为空

`deepseek-flash` 会先产生 `reasoning_tokens`。若 `max_tokens` 给小了，token 全部消耗在推理上，
**`content` 会是空字符串**，`finish_reason` 为 `length`。

这不是随机故障，而是稳定复现的行为。`llm.py` 因此内置了**空回复自动扩容重试**
（base → 2× → 4×），失败时抛出带完整 usage 的诊断信息。`.env` 的 `DEEPSEEK_MAX_TOKENS` 请勿调太小。

### 2. 模型名不会被提前校验

`/models` 只返回 `deepseek-flash` 与 `deepseek-v4-pro`，但请求 `deepseek-chat`、
`deepseek-v4-flash-vision-exp` 等**同样返回 200**。判断模型是否真正可用，必须检查**响应正文**，
不能只看状态码。

### 3. 图片只能放在 user 消息里

`system` 或 `assistant` 消息携带图片会返回 `400`。本项目的图片一律通过 `user` 消息传入。

### 4. 中文地名无法用 Open-Meteo 地理编码

实测 `九寨沟` 返回 0 结果，而 `Huangshan` 能正常返回。OSM Nominatim 在部分网络下**完全不可连接**。
因此内置了 `lookup_coordinates` 工具，用模型知识兜底反查坐标，并在返回中**明确标注这是估算值**。

### 5. 工具失败不应中断智能体

`BaseTool.safe_run` 兜住一切异常，把错误作为结果交还给模型，让它自行改写参数重试或换路。
失败信息里会带上可用选项（如可用城市列表），实测模型能据此自我纠正。

### 6. `.bat` 启动脚本必须写成纯 ASCII

本机系统代码页是 **936（GBK）**。把含中文的 `.bat` 存成 UTF-8，cmd.exe 会按 GBK 解读，
中文变乱码只是表象 —— **真正的问题是某些 GBK 双字节组合会吞掉行尾换行符**，
使 `REM` 注释与下一行黏在一起，整行被当成注释，后续命令全部失效。

因此 `START-WEB.bat`、`START-CLI.bat`、`诊断.bat` 三个脚本都用**纯 ASCII**，
并在文件头注释里写明原因，避免以后有人「顺手改成中文」又把脚本改坏。

`chcp 65001` 也无法拯救这种文件：它在中文之后才执行，而且改的是控制台输出代码页，
不影响 cmd 对文件本身的解析。

### 7. cmd 解析陷阱：括号块里不能放 label 和 goto

诊断脚本最初把带 `:label`/`goto` 的流程写进了 `if ... ( ... )` 括号块内，
而块内又有 `set FAIL=1`。cmd 会整体预解析括号块，导致分支判断错乱 ——
表现为「测试明明成功，却多打印了一行 SKIP」。改为纯 `goto` 流程后正常。

### 8. PowerShell 5.1 读无 BOM 的 UTF-8 脚本会乱码并语法崩溃

本机**只有 Windows PowerShell 5.1**（没有 `pwsh`）。5.1 对**没有 BOM** 的 `.ps1`
文件按 **ANSI(cp936/GBK)** 解码，于是：

```powershell
Read-Host '  按回车键退出'
# 实际被读成：Read-Host '  鎸夊洖杞﹂敭閫€鍑?
# 报错：The string is missing the terminator: '.
```

**中文把引号吃掉了，脚本直接语法错误**，连第一行都跑不到。
这与坑 6 的 `.bat` 问题是同一个病根，只是换了个文件类型。

修复：所有 `.ps1`（`启动网页.ps1` / `启动命令行.ps1` / `诊断.ps1` / `停止服务.ps1`）
都**写入 UTF-8 BOM**（`EF BB BF`），5.1 就能正确识别为 UTF-8。
校验方式：用 5.1 的语法解析器逐文件检查，四个脚本全部 `语法 OK`。

> 排查这类问题的最快手段：`[System.Management.Automation.Language.Parser]::ParseFile(...)`
> 用 5.1 的解析器跑一遍，等价于 5.1 实际执行时的语法判断。

---

## 已知限制

- 景点与费用为**内置经验数据**，不是实时接口，价格需以官方为准；未收录城市会退化为模型常识，
  此时提示词要求它明确标注「通用建议，非本地数据库数据」。
- 天气仅覆盖未来 16 天，无法查询远期日期（模型会如实说明）。
- 国际机票与签证费用未纳入预算模型，仅估算城际交通。
- `lookup_coordinates` 依赖模型知识，可能出错，已标注置信度与「距最近内置城市的距离」供交叉验证。
