# CUSZ-MFM5110-finalProject

> 面向中国 A 股市场的 FinRobot 多智能体投研系统 —— 模块地图与后续优化路线图
>
> 代码根目录：`finrobot_autogen/`（包名 `finrobot`）
> 文档日期：2026-11-18

---

## 目录

- [1. 项目概述](#1-项目概述)
- [2. 系统架构总览](#2-系统架构总览)
- [3. 功能模块与代码映射](#3-功能模块与代码映射)
  - [3.1 Agent 定义层](#31-agent-定义层角色与提示词)
  - [3.2 工具注册层](#32-工具注册层)
  - [3.3 数据源层](#33-数据源层)
  - [3.4 功能计算层](#34-功能计算层)
  - [3.5 入口与实验](#35-入口与实验)
  - [3.6 配置与工具](#36-配置与工具)
- [4. A 股数据源路由机制](#4-a-股数据源路由机制)
- [5. 运行方式](#5-运行方式)
- [6. 优化路线图](#6-优化路线图)
  - [P0：确定性计算层](#p0确定性计算层收益最大)
  - [P0：数据可靠性](#p0数据可靠性)
  - [P1：工具粒度收敛](#p1工具粒度收敛直接改善-agent-表现)
  - [P1：舆情模块](#p1舆情模块)
  - [P1：Agent 性能与评测](#p1agent-性能与评测)
  - [P2：可观测性与安全](#p2可观测性与安全)
  - [P2：报告与 RAG 扩展](#p2报告与-rag-扩展)
- [7. 实施顺序与验收标准](#7-实施顺序与验收标准)
- [附录 A：A 股数据口径约定](#附录-aa-股数据口径约定)
- [附录 B：已知限制](#附录-b已知限制)

---

## 1. 项目概述

本项目基于 FinRobot / AutoGen（AG2 0.9.0）构建面向**中国 A 股市场**的投研 Agent 系统，主要工作包括：

1. **新增中国数据适配层**：`AkShareUtils`（免费）、`TushareUtils`（Pro）、`CNStockUtils`（统一路由门面）。
2. **改造 Agent 层**：新增 `A_Share_Analyst` 角色，将 `Market_Analyst` / `Expert_Investor` 的工具与提示词切换为 A 股口径。
3. **打通上下游**：图表（K 线、相对收益、PE/EPS）、财报分析、PDF 报告全部改用 `CNStockUtils`，实现"换数据源不改上层代码"。
4. **保留美股能力**：`SECUtils`、`FMPUtils`、`FinnHubUtils`、`YFinanceUtils` 等模块仍在，供 US ticker 分支使用。

核心设计原则：

- **单一工具名、多数据源后端**：上层只认 `CNStockUtils.get_stock_data`，底层由 `CN_DATA_PROVIDER` 决定走 AkShare 还是 Tushare。
- **优雅降级**：Tushare 权限/积分不足时自动回退 AkShare，并在进程内停用 Tushare，避免反复失败。
- **口径统一**：Tushare 输出被归一化为 AkShare 的列名、单位（元 / 亿元 / 手）和索引格式。

---

## 2. 系统架构总览

```
┌──────────────────────────────────────────────────────────────┐
│                         入口层                                │
│                        run_agent.py                         │
└──────────────────────────────┬───────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────┐
│                       Agent 层                               │
│  agents/agent_library.py   角色定义 + 工具清单                │
│  agents/prompts.py         A 股市场背景 / 指令模板            │
│  agents/workflow.py        Single/Multi Assistant 工作流      │
└──────────────────────────────┬───────────────────────────────┘
                               │  register_toolkits
┌──────────────────────────────▼───────────────────────────────┐
│                     工具注册层                                │
│  toolkits.py   register_toolkits / register_tookits_from_cls │
│                stringify_output（DataFrame → 文本）           │
└───────────────┬──────────────────────────────┬───────────────┘
                │                              │
┌───────────────▼──────────────┐  ┌────────────▼───────────────┐
│         数据源层             │  │        功能计算层           │
│ data_source/cn_provider.py   │  │ functional/charting.py     │
│ data_source/akshare_utils.py │  │ functional/analyzer.py     │
│ data_source/tushare_utils.py │  │ functional/reportlab.py    │
│ data_source/*_utils.py (US)  │  │ functional/quantitative.py │
│ data_source/filings_src/ ... │  │ functional/rag.py ...      │
└──────────────────────────────┘  └────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────┐
│                      基础设施                                 │
│  utils.py（配置加载/装饰器）  config_api_keys  OAI_CONFIG_LIST│
└──────────────────────────────────────────────────────────────┘
```

---

## 3. 功能模块与代码映射

### 3.1 Agent 定义层（角色与提示词）

| 模块 | 代码 | 说明 |
|---|---|---|
| 角色库 | `finrobot/agents/agent_library.py` | 5 个角色：`Data_Analyst`、`Financial_Analyst`、`Market_Analyst`、`A_Share_Analyst`（24 个工具）、`Expert_Investor`（15 个工具）。每个角色由 `system_message` + `toolkits` 构成，最终汇总为 `library` 字典 |
| 提示词 | `finrobot/agents/prompts.py` | `a_share_market_context`（A 股市场背景、交易规则、数据源规则）、`order_template`、Leader 模板 |
| 工作流引擎 | `finrobot/agents/workflow.py` | `FinRobot`（AssistantAgent 封装）、`SingleAssistant`、`SingleAssistantRAG`、`SingleAssistantShadow`、`MultiAssistant`、`MultiAssistantWithLeader` |
| 角色工具 | `finrobot/agents/utils.py` | 角色组装辅助函数 |

**角色与工具概览**

| 角色 | 定位 | 工具数 | 主要能力 |
|---|---|---|---|
| `Data_Analyst` | 通用数据 | 较少 | 行情、财务报表原始取数 |
| `Financial_Analyst` | 通用财务 | 中等 | 三表、财务指标、同行对比 |
| `Market_Analyst` | 市场情绪 | 7 | 行情、公司新闻、全球新闻、热榜、公告 |
| `A_Share_Analyst` | **A 股默认角色** | 24 | 行情/信息/分红、三表、财务指标、同行、市值、BVPS、分析师评级/预测、目标价、研报、公告、新闻、热榜、K 线图 |
| `Expert_Investor` | 研究报告 | 15 | 三表分析、研报、目标价、新闻/公告、IPython/文本/PDF、图表工具 |

### 3.2 工具注册层

| 模块 | 代码 | 说明 |
|---|---|---|
| 单个工具注册 | `finrobot/toolkits.py:22` `register_toolkits` | 支持函数或 dict（自定义 `name` / `description`），注册到 `assistant.llm_config["tools"]` 与 `user_proxy._function_map` |
| 整类注册 | `finrobot/toolkits.py:85` `register_tookits_from_cls` | 把一个类的所有公开方法批量注册为工具（`CNStockUtils`、`ReportChartUtils`、`MplFinanceUtils`） |
| 输出序列化 | `finrobot/toolkits.py:10` `stringify_output` | DataFrame 直接 `to_string()` 全量输出，**当前无截断**（优化点） |
| 代码编写工具 | `finrobot/toolkits.py:54` `register_code_writing` | `CodingUtils` 读写/修改文件 |

### 3.3 数据源层

| 模块 | 代码 | 说明 |
|---|---|---|
| **A 股路由** | `finrobot/data_source/cn_provider.py` | `CNStockUtils` 门面 + `CN_DATA_PROVIDER` 路由；Tushare 失败自动降级 AkShare；进程内停用 Tushare |
| A 股主数据源 | `finrobot/data_source/akshare_utils.py` | `AkShareUtils`，24 个方法：行情、三表、财务指标、同比、盈利预测、研报、公告、新闻、热榜、历史市值、BVPS 等 |
| A 股备选源 | `finrobot/data_source/tushare_utils.py` | `TushareUtils`：行情（qfq）、基础信息、分红、三表、历史市值 |
| 美股/海外 | `finnhub_utils.py`、`fmp_utils.py`、`sec_utils.py`、`yfinance_utils.py`、`reddit_utils.py`、`finnlp_utils.py`、`finance_data.py` | A 股角色不再使用，但代码保留，US ticker 分支可用 |
| 文档 RAG 源 | `filings_src/`、`earnings_calls_src/`、`marker_sec_src/` | 美股 10-K / 电话会 PDF → Markdown → 向量库 |

### 3.4 功能计算层

| 模块 | 代码 | 说明 |
|---|---|---|
| K 线/走势图 | `finrobot/functional/charting.py:33` `MplFinanceUtils` | K 线 + 均线 + 成交量，含 CJK 字体处理 |
| 报告图表 | `finrobot/functional/charting.py:105` `ReportChartUtils` | 相对收益对比、PE/EPS 双轴图（A 股基准：沪深 300 `000300`） |
| 财报分析 | `finrobot/functional/analyzer.py` `ReportAnalysisUtils` | 利润表/资产负债表/现金流/分部表分析、风险评估、同行对比、业务亮点、`get_key_data` |
| PDF 报告 | `finrobot/functional/reportlab.py` `ReportLabUtils` | `build_annual_report`，使用 `STSong-Light` 中文字体 |
| 代码执行 | `finrobot/functional/coding.py` | `IPythonUtils.exec_python`、`display_image`、`CodingUtils` 文件编辑 |
| 量化回测 | `finrobot/functional/quantitative.py` | `BackTraderUtils.back_test`、`DeployedCapitalAnalyzer` |
| RAG | `finrobot/functional/rag.py`、`ragquery.py` | 检索函数 + SEC / earnings call 入库 |
| 文本工具 | `finrobot/functional/text.py` | `TextUtils.check_text_length` |

### 3.5 入口与实验

| 模块 | 代码 | 说明 |
|---|---|---|
| 主入口 | `run_agent.py` | 交互 / 单次模式，默认 `auto` 自动分派，支持 `--mode single`、`--role`、`--provider`、`--model` |
| 旧演示 | [归档目录](../archive/legacy_demos/README.md) | 独立的茅台走势预测、道琼斯多因子、拼多多目标价案例，不由主入口调用 |
| 模块测试 | `test_module.py` | 手工验证脚本 |

### 3.6 配置与工具

| 模块 | 代码 | 说明 |
|---|---|---|
| 配置加载 | `finrobot/utils.py:31` `register_keys_from_json` | JSON 键写入环境变量 |
| 类方法装饰 | `finrobot/utils.py:38` `decorate_all_methods` | Tushare 客户端惰性初始化 |
| 密钥 | `config_api_keys`（gitignore）、`OAI_CONFIG_LIST` | 当前仅 Tushare token + provider 配置 |
| 文档 | `README.md` | 数据源表、provider 用法 |

---

## 4. A 股数据源路由机制

配置示例（`config_api_keys`，**不要提交真实密钥**）：

```json
{
    "TUSHARE_TOKEN": "<your token>",
    "CN_DATA_PROVIDER": "auto"
}
```

`CN_DATA_PROVIDER` 取值：

| 值 | 行为 |
|---|---|
| `auto`（默认） | 优先 Tushare；接口失败 / 积分不足时自动回退 AkShare，并在进程内停用 Tushare |
| `akshare` | 始终使用免费的 AkShare |
| `tushare` | 只用 Tushare，失败直接报错（便于排查权限/积分问题） |

数据源分工：

| 能力 | 数据源 |
|---|---|
| 行情、基础信息、分红、三表、历史市值 | Tushare 优先（`auto`），否则 AkShare |
| 财务指标、公告、新闻、研报、机构预期、热榜 | 始终 AkShare（保证单位与口径一致） |

Tushare 积分要求（参考）：行情/基础信息约 120 分，财务报表约 2000 分，新闻约 400 分。

启动时会打印当前数据源状态：

```text
A股数据源：auto（优先 Tushare Pro，失败自动回退 AkShare）
```

---

## 5. 运行方式

```bash
# 交互模式（默认 A_Share_Analyst）
python run_agent.py --role A_Share_Analyst

# 单次提问
python run_agent.py "帮我分析一下 600519 最近一年的基本面和估值" --role A_Share_Analyst

# 指定数据源
python run_agent.py --provider akshare
python run_agent.py --provider tushare

# 市场情绪角色
python run_agent.py "600519 最近为什么涨" --role Market_Analyst
```

依赖环境：conda env `finrobot`（Python 3.10），`autogen`（AG2 0.9.0），从仓库根目录安装（根 `setup.py` 将 `finrobot_autogen` 映射为 `finrobot`）。

---

## 6. 优化路线图

### P0：确定性计算层（收益最大）

**问题**：三表比率、同比、估值分位大部分靠 LLM 读 `to_string()` 后自行计算，算错无法被发现。

**方案**：

- 新建 `finrobot/functional/metrics.py`：纯函数计算毛利率、净利率、ROE、增速、PE/PB 分位、简化 DCF，全部带单位与口径注释。
- 新增 `tests/`：用固定样本做 golden test（如 600519 已知财报值）。
- 工具输出改为结构化 JSON：`{value, unit, period, source, formula}`，LLM 只负责解读与叙述。

**验收**：同一问题的数值在多次运行中完全一致；与手工计算误差为 0（或明确注明四舍五入口径）。

### P0：数据可靠性

**问题**：AkShare 接口不稳定、易限流、列名可能变更；无缓存导致重复请求。

**方案**：

- 新增 `data_source/cache.py`：SQLite/Parquet 本地缓存，按 `(接口, symbol, 参数, 交易日)` 去重。
- 统一重试 / 退避 / 限流包装。
- 交易日历（`ak.tool_trade_date_hist_sina`）统一日期对齐，避免把非交易日价格当交易日。
- 返回值附 `source` / `fetched_at` / `units` 元数据，报告中可标注来源。
- schema 校验：列名不匹配时立即报错，而不是让 LLM 猜测。

**验收**：重复提问 `600519 近一年行情` 时只发生一次网络请求（缓存命中）；接口失败有明确降级日志。

### P1：工具粒度收敛（直接改善 Agent 表现）

**问题**：`A_Share_Analyst` 有 24 个工具、`Expert_Investor` 有 15 个，schema 占用大量 context，模型选错工具概率高；`stringify_output` 输出无截断，token 成本高。

**方案**：

- 增加聚合工具：
  - `get_stock_snapshot`：价格 + 市值 + 估值 + 52 周区间；
  - `get_financial_snapshot`：三表关键项 + 核心指标；
  - `get_news_snapshot`：新闻 + 公告 + 热榜。
- 细粒度工具只保留给 `Expert_Investor`。
- `stringify_output` 增加截断与摘要（DataFrame 只输出 head/tail/shape/列名）。

**验收**：`A_Share_Analyst` 工具数降到 8~10 个；同等问题的 prompt token 明显下降且回答质量不降。

### P1：舆情模块

**方案**：建议先作为工具并入 `A_Share_Analyst` / `Market_Analyst`，而不是一开始就新建角色。

- `finrobot/functional/sentiment.py` → `SentimentUtils.get_sentiment_snapshot(symbol)`：
  - 免费 AkShare 数据：`stock_comment_em`（千股千评）、`stock_hot_rank_em`、`stock_hot_keyword_em`、`stock_hot_search_baidu`、`stock_hot_tweet_xq`、`stock_news_em`；
  - 规则层：利好/利空关键词、公告事件（增持/减持/回购/解禁/业绩预告）；
  - 可选 LLM 层：标题二分类 + 情绪强度；
  - 输出统一为 `{score: -1..1, confidence, drivers[], sources[]}`。
- 可回测：情绪分 vs 未来 5/20 日收益。
- 可选：Tushare `news`（约 400 积分，当前 token 无权限）。

**验收**：对 600519 输出稳定的情绪分与驱动因素；能引用具体新闻标题作为依据。

### P1：Agent 性能与评测

**方案**：

- **Context 预算**：工具输出截断 + 只保留最近 N 轮；长任务用摘要替代原始消息。
- **同轮工具缓存**：同一 run 内相同 `(tool, args)` 直接复用结果。
- **终止条件**：设置 `max_consecutive_auto_reply`，并在 prompt 中强制"先取数、再结论"。
- **评测集**：20~50 条固定问题（如"600519 近 3 年 ROE 与毛利率趋势"），度量数值准确率、引用率、延迟、token 成本。

**验收**：评测集可一键运行；每次改动前后有可对比的分数。

### P2：可观测性与安全

**方案**：

- 结构化日志：每次工具调用记录 `name/args/latency/rows/source/error`，输出 JSONL。
- 成本看板：按角色 / run 统计 token。
- 配置校验：启动时校验 `config_api_keys` 必填项、provider 取值、token 是否存在；支持 `.env`。
- 密钥隔离：Tushare token 只给数据层，不进入 prompt；定期轮换。

**验收**：任意一次回答都能回溯到具体工具调用与数据来源。

### P2：报告与 RAG 扩展

**方案**：

- PDF / 图表改为模板驱动 + 图表缓存，避免每次重新渲染。
- A 股公告 RAG：巨潮资讯公告 PDF → 文本 → 向量库，让 `Expert_Investor` 能引用原文而非只靠新闻标题。

**验收**：报告生成可重复、可缓存；公告问答能给出原文段落与页码。

---

## 7. 实施顺序与验收标准

建议顺序：

1. **P0 确定性计算层 + 缓存**（正确性与稳定性）
2. **P1 工具收敛**（降低 token、提升选工具准确率）
3. **P1 舆情模块**（功能扩展）
4. **评测集**（为后续所有改动提供回归基线）

| 阶段 | 关键文件 | 验收方式 |
|---|---|---|
| 计算层 | `functional/metrics.py`、`tests/` | golden test 全绿 |
| 缓存层 | `data_source/cache.py` | 重复请求命中缓存 |
| 工具收敛 | `agents/agent_library.py`、`toolkits.py` | 工具数下降，评测不退化 |
| 舆情 | `functional/sentiment.py` | 输出稳定、有依据 |
| 评测 | `tests/eval_*.py` | 一键运行、指标可对比 |

---

## 附录 A：A 股数据口径约定

- 股票代码：6 位数字（`600519`），也接受 `600519.SH` / `SH600519` / `sh.600519`。
- 会计口径：CAS（企业会计准则），`归母净利润`，报告期为 `Q1 / 半年报 / Q3 / 年报`。
- 交易规则：T+1，涨跌停 ±10% / ±20%（创业板、科创板）/ ±5%（ST）等。
- 价格：默认前复权（qfq）。
- 成交量单位：手；成交额单位：元。
- 市值单位：亿元。
- 基准指数：沪深 300（`000300`）。

## 附录 B：已知限制

1. Tushare token 当前可能没有接口权限（积分不足），`auto` 模式下会回退 AkShare。
2. 财务指标、公告、新闻、研报固定走 AkShare，Tushare 对应字段尚未做逐项映射验证。
3. `stringify_output` 当前无截断，长表会显著增加 token 成本。
4. Agent 层尚无自动化评测，行为改动缺少回归保护。
5. A 股公告 RAG 尚未接入，中英文档检索只覆盖美股 SEC 材料。
