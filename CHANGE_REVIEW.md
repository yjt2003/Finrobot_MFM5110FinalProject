# FinRobot A 股投研改造：提交审阅说明

**整理日期：2026 年 11 月 19 日（香港时间）**

**提交目的：** 在原有 FinRobot CLI 和投研工具上加入自动分工、可复算的 A 股因子及单股回测；修正年报选择和财务口径，提高中文 PDF 生成的可靠性。旧演示迁入归档，不再和主入口混放。

## 1. 比较基线

| 范围 | 基线与说明 |
| --- | --- |
| 原项目 | `upstream/main`：`8d2cc02`，已与远端 `main` 核对。 |
| 拟提交工作区 | 本地 `main` 的 `HEAD` 和 `origin/main` 均为 `8ac58bf`。整理前相对 `HEAD` 有 11 个已跟踪文件修改、6 个旧路径删除，后者均有归档对应项。其余新增文件尚未跟踪，普通 `git diff` 不会列出。 |
| 独立的上游差异 | 上游比本地多一个根 `README.md` 提交，新增了更换模型供应商的指南。它不是本次主动删除项；合并时应保留该段，并核对其中提到的默认模型。 |

## 2. 目录结构与运行链路

```text
Finrobot_MFM5110FinalProject/
├── CHANGE_REVIEW.md               本审阅说明
├── archive/legacy_demos/           旧演示及专用配置
└── finrobot_autogen/
    ├── run_agent.py                交互 / 单次提问，默认自动分派
    ├── finrobot/
    │   ├── agents/                 角色、工具注册、分派与汇总
    │   ├── data_source/            CNStockUtils 路由与数据适配
    │   └── functional/             因子、回测、图表、PDF 和检索
    ├── experiments/               验收和批量回测源码，不跟踪实验产物
    └── tests/                      离线回归测试
```

`run_agent.py` → 自动路由或固定角色 → `SingleAssistant` → 注册工具 → 行情、财务查询或 Python 计算。综合请求由多个角色依次处理，再由不取数、不执行代码的汇总角色整理当轮证据。旧演示不由主入口调用。

## 3. 功能变更

| 范围 | 原项目 | 本次变更与影响 |
| --- | --- | --- |
| CLI 与分派 | 默认固定 `A_Share_Analyst`，其他角色手选。 | 默认 `auto`，按关键词分派基本面、市场和量化；显式研报、因子设计分别交给专门角色。综合模式保留成功分项并报告失败，汇总失败时回退至分项结果。追问参考最近三轮简短文本；多角色与汇总会增加模型调用。 |
| A 股因子 | 有独立多因子演示，主 CLI 无确定性价量计算工具。 | `A_Share_Factor_Researcher` 用前复权日线计算 20/60 日动量、20 日年化波动率、20 日日均成交量（手/日），返回实际行情日期、公式与单位。窗口按可用日线计，指标输入不足返回 `null`。 |
| 单股回测 | 有独立案例，主 CLI 无统一的 A 股策略与基准对照。 | 提供固定 20 日正动量与通用规则回测，比较策略、个股买入持有、沪深 300 价格指数，并输出累计收益和最大回撤。收盘出信号、下一可用开盘执行；默认每次变仓 10 基点，通用工具滑点默认 0，最后一个开盘只估值、不强制平仓。 |
| 因子创意 | 无专门的“提出假设 → 验证规则”流程。 | `A_Share_Factor_Ideator` 给出候选假设、公式与验证计划；明确要求回测且给出标的和日期时，执行经白名单校验的 JSON 规则，支持动量、均量比、均线比较及 `all`/`any` 组合，也可追问回测上轮规则。两个量化角色均禁用通用代码执行。 |
| A 股数据源 | 支持自动路由、AkShare、Tushare，已有腾讯个股行情回退。 | 新增可选 `--provider baostock`，只接管沪深个股日线，其他数据仍走 AkShare；需额外安装 `baostock`。兼容腾讯个股 OHLC 字段并新增指数腾讯回退；部分股票简称接口不可用时用代码代替。 |
| 基本面与市场 | 基本面角色兼查行情、新闻和热榜；未分别列出两个营业收入概念。 | 基本面聚焦财务、估值与公司公告，行情和新闻由市场角色处理。AkShare 财务指标区分 `OPERATE_INCOME`（营业收入）与 `TOTAL_OPERATE_INCOME`（营业总收入），按报告期合并三表；财务指标和同业比较支持可选 `as_of`。未新增完整的历史新闻库。 |
| 年报、图表与 PDF | 已有年报和 PDF 能力，但指定年报可能错取同年披露的上年度报告，非关键元数据也可能导致构建失败。 | 指定年度时按标题匹配报告年度、优先正式版本，并返回标题、链接及披露日；图表与 PDF 校验落盘。PDF 成功返回文件路径、失败抛出异常；改进中文字体和表格，并限制引用无法确认在截止日前已公开的机构观点。RAG 依赖改为按需导入。 |

**兼容性：** 只传 `--role` 仍进入单角色模式；`--mode single` 不指定角色仍使用 `A_Share_Analyst`。`--mode auto` 不接受 `--role`。CLI 默认模型从 `gpt-4o` 改为 `gpt-6.2-sol`，可用 `--model` 选择已配置模型。下游若按名称读取 AkShare 财务表，需适配 `Revenue (亿元)`、`Revenue Growth (%)` 两个旧指标名的替换；依赖 PDF 构建固定成功字符串的调用应改为处理返回路径和异常。

## 4. 文件变更清单

下表路径相对于 `finrobot_autogen/`。

| 状态 | 文件 | 审阅重点 |
| --- | --- | --- |
| 修改 | `run_agent.py` | CLI 默认模式、固定角色兼容、模型选择与 BaoStock。 |
| 修改 | `finrobot/agents/agent_library.py`、`finrobot/agents/workflow.py` | 新量化角色、基本面与研报职责、工具权限。 |
| 修改 | `finrobot/data_source/akshare_utils.py`、`finrobot/data_source/cn_provider.py`、`finrobot/data_source/tushare_utils.py` | 数据路由、行情回退、财务与年报口径、公司元数据回退。 |
| 修改 | `finrobot/functional/charting.py`、`finrobot/functional/reportlab.py`、`finrobot/functional/rag.py` | 图表与 PDF 产物、字体和历史报告、检索依赖延迟加载。 |
| 修改 | `CONFIG_README.md`、`CUSZ-MFM5110-finalProject.md` | 启动方式、角色、实验和旧演示位置。 |
| 新增 | `finrobot/agents/research_router.py` | 分派、追问上下文和分项失败处理。 |
| 新增 | `finrobot/data_source/baostock_utils.py` | 沪深个股日线、复权与成交量单位转换。 |
| 新增 | `finrobot/functional/factors.py`、`finrobot/functional/momentum_backtest.py` | 确定性因子、规则校验、执行时序与基准比较。 |
| 新增 | `experiments/evaluate_factor_agent.py`、`experiments/run_research_e2e.py` | 固定行情 + 真实模型验收，以及真实 CLI 和数据源联测。 |
| 新增 | `experiments/validate_momentum.py`、`experiments/validate_momentum_holdout.py` | 历史批量和独立样本回测。 |
| 新增 | `tests/test_factors.py`、`tests/test_factor_agent_acceptance.py`、`tests/test_research_router.py`、`tests/test_data_and_reports.py` | 因子、回测、验收判分、路由、工具限制及报告回归。 |

仓库根目录的 `CHANGE_REVIEW.md` 随本次提交提供审阅说明；`.gitignore` 新增规则，忽略整个实验结果目录。

### 旧路径迁移

以下 6 个文件从 `finrobot_autogen/` 移到 `archive/legacy_demos/`，相对层级保留；另新增[归档说明](archive/legacy_demos/README.md)。未验证从归档目录直接运行。

| 演示 | 相对路径 | 与原版本相比 |
| --- | --- | --- |
| 茅台走势预测 | `agent_builder_demo.py`、`configs/save_config_forecaster.json` | 均保留归档前已有的模型配置调整。 |
| 道琼斯多因子 | `experiments/multi_factor_agents.py`、`experiments/quantitative_investment_group_config.json` | 脚本保留模型配置调整；角色配置内容不变。 |
| 拼多多目标价 | `experiments/portfolio_optimization.py`、`experiments/investment_group.py` | 前者保留模型配置调整；投研组定义内容不变。 |

### 不纳入本次提交

`finrobot_autogen/experiments/results/` 内的回测输出、验收记录、模型 trace、PDF、图片和抽取文本全部保留在本地。此次只提交功能代码、回归测试与复跑脚本源码、项目文档，以及旧入口的归档迁移；早期交接笔记 `finrobot_autogen/AGENT_HANDOFF.md` 和本地索引不纳入提交。

## 5. 验收方法

离线回归 **40/40 通过**。固定数据和替代客户端验证不等同于外部服务联测；实验和联测输出不随提交发布。

从 `finrobot_autogen/` 目录运行。`MODEL_NAME` 替换为本地已配置且支持工具调用的模型；用新输出路径以保留旧记录。

```powershell
$env:PYTHONUTF8 = '1'
conda run --no-capture-output -n finrobot python -m unittest discover -s tests -p 'test_*.py'
conda run --no-capture-output -n finrobot python -m experiments.evaluate_factor_agent --model MODEL_NAME --output experiments/results/factor_acceptance_rerun.json
conda run --no-capture-output -n finrobot python -m experiments.run_research_e2e report --model MODEL_NAME --output experiments/results/e2e_rerun
```

第一条无需外部行情或模型密钥；第二条需要模型但不请求真实行情；第三条同时使用模型和真实数据。验收研报时检查文件存在、能打开、报告年度及披露日正确、正文与取数一致，不以退出码或 `execution_ok` 代替业务核验。环境与批量回测用法见[配置说明](finrobot_autogen/CONFIG_README.md)。

## 6. 合并前检查

- **数据口径：** `as_of` 按报表期末筛选，不等于按首次披露日构建完整历史快照。营业收入列整体缺失时会用营业总收入回退，归母净利润缺失时会用净利润回退；应核对字段来源，不能把回退值当作同口径数据。历史新闻查不到也不等于当时无事件。
- **回测边界：** 仅限单股、做多或空仓，不建模涨跌停成交、部分成交和市场冲击；前复权行情不等于严格点时快照，沪深 300 采用价格指数而不是总收益指数。结果不作为实盘能力承诺。
- **提交范围：** 保留上游 `README.md` 的模型供应商说明。排除 `.codegraph/`、`.cache/` 和全部实验产物。`OAI_CONFIG_LIST`、`config_api_keys` 在此检出是已跟踪、标为 `skip-worktree` 的文件，不能只依赖 `.gitignore`；确认提交中的配置仍为占位符，不暂存本地密钥配置。
- **暂存后复核：** 在仓库根目录执行 `git diff --cached --name-status -M` 和 `git diff --cached --check`，确认旧路径与归档对应，且不存在实验产物或本地密钥文件的改动。

建议提交标题：`feat: add routed A-share research and factor backtesting`。
