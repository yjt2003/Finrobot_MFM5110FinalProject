# 配置文件说明（运行前必读）

本项目的两个配置文件就在本目录（`finrobot_autogen/`）下：

| 文件 | 作用 | 是否必须填写 |
| --- | --- | --- |
| `OAI_CONFIG_LIST` | 大模型（LLM）的 API Key | **必须**（至少填一个模型） |
| `config_api_keys` | A 股数据源（Tushare）等 API Key | 可选（不填自动用免费的 AkShare） |

> 注意：这两个文件是 JSON 格式，**JSON 不支持 `#` 或 `//` 注释**。因此每项需要配置的内容都用
> `REPLACE_ME_...` 占位符写在了对应字段里，把占位符替换成你自己的 key 即可。本文档就是详细说明。

---

## 1. `OAI_CONFIG_LIST` —— 大模型 API

文件内容是一个数组，每个对象是一路可用的模型服务：

```json
{
    "model": "模型名",
    "api_key": "你的 API Key",
    "base_url": "服务地址（官方 OpenAI 可省略或写 https://api.openai.com/v1）"
}
```

### 1.1 DeepSeek（推荐，便宜）

- 网站：https://platform.deepseek.com
- 步骤：注册/登录 → 左侧「API Keys」→「创建 API Key」→ 复制 `sk-` 开头的字符串
- 把 `deepseek-flash` 那一项的 `api_key` 替换成你的 key 即可，`base_url` 保持不变
- 用 DeepSeek 运行时需要指定模型：
  ```bash
  python run_agent.py "分析一下 600519" --role A_Share_Analyst --model deepseek-flash
  ```

### 1.2 gpt-4o / gpt-4o-mini（项目共享中转）

- 文件里这两项的 `base_url` 是项目组共用的 OpenAI 兼容中转地址
  （`http://117.72.29.113:4000/v1`），对应的 key 请**找项目负责人 / 课程群里索取**，
  把 `REPLACE_ME_PROJECT_PROXY_KEY` 替换掉。
- 如果你自己用官方 OpenAI，把这两项改成：
  ```json
  {
      "model": "gpt-4o",
      "api_key": "sk-你的OpenAIKey",
      "base_url": "https://api.openai.com/v1"
  }
  ```
  官方 key 获取：https://platform.openai.com/api-keys
- `run_agent.py` 默认使用 `--model gpt-4o`，所以想用默认参数直接跑，需要先配好这一项。

### 1.3 最小配置

只填 `deepseek-flash` 一项即可运行，只是启动时要加 `--model deepseek-flash`：

```bash
cd finrobot_autogen
python run_agent.py --role A_Share_Analyst --model deepseek-flash
```

---

## 2. `config_api_keys` —— 数据源 API（可选）

```json
{
    "TUSHARE_TOKEN": "",
    "CN_DATA_PROVIDER": "auto"
}
```

- **`TUSHARE_TOKEN`（可选）**：留空即可，程序会自动使用免费的 **AkShare** 拉取 A 股数据。
  想用 Tushare Pro（数据更全、有积分限制）时：
  1. 注册：https://tushare.pro/register
  2. 登录后在个人主页复制 token：https://tushare.pro/user/token
  3. 粘贴到 `TUSHARE_TOKEN` 的值里（保留引号）
- **`CN_DATA_PROVIDER`**：
  - `auto`（默认）：优先 Tushare，失败或积分不足自动回退 AkShare
  - `akshare`：只用免费的 AkShare
  - `tushare`：只用 Tushare，失败直接报错（排查问题时用）
- 文件里以 `_README_` 开头的键只是说明文字，程序会把它们当作普通环境变量，不影响运行。

---

## 3. 安装与运行

```bash
# 1) 创建环境（Python 3.10）
conda create -n finrobot python=3.10 -y
conda activate finrobot

# 2) 安装依赖（在仓库根目录）
pip install -r finrobot_autogen/requirements.txt
pip install -e .

# 3) 填好上面两个配置文件后进入交互模式
cd finrobot_autogen
python run_agent.py --model deepseek-flash
```

### 自动分工与单角色模式

- `--mode auto`（不指定角色时的默认模式）：显式研报请求交给 `Expert_Investor`；因子创意请求直接交给 `A_Share_Factor_Ideator`。其他问题按关键词匹配基本面、市场事件和量化因子/回测。匹配多个方向时，由相应角色依次分析，再交给不取数的汇总 Agent。无具体方向的「综合分析」会派给这三个研究角色；普通模糊问题回退到基本面角色。
- `--mode single`：固定一个角色，保留原交互方式。可用角色：`A_Share_Analyst`（single 默认）、`A_Share_Factor_Researcher`、`A_Share_Factor_Ideator`、`Market_Analyst`、`Expert_Investor`。只指定 `--role` 也仍然走单角色；显式 `--mode auto` 不与 `--role` 同用。

```bash
python run_agent.py "综合分析 600519 的基本面、近期新闻和波动率" --model deepseek-flash
python run_agent.py "综合分析 600519 的基本面、近期新闻和波动率" --mode single --role A_Share_Analyst --model deepseek-flash
```

自动模式会显示分派的角色，并在分项失败时列出缺失项；汇总失败时保留已得到的分项结果。自动分派不额外调用 LLM，但多个角色与汇总分别调用模型，综合问题通常更耗时。自动模式只保留最近三轮的简短文本供追问参考。路由依赖关键词，不支持任意分工；历史综合分析也不保证所有新闻和财报源具备时点快照。

### A 股因子创意生成

在 `finrobot_autogen/` 目录中，用已配置的模型运行：

```bash
python run_agent.py "能不能用成交量改进现有动量策略？" --model deepseek-flash
python run_agent.py "设计并回测 600519 的量能确认动量策略，区间 2024-01-02 至 2024-06-28" --model deepseek-flash
python run_agent.py "设计一个基于日线价量的因子" --role A_Share_Factor_Ideator --model deepseek-flash
```

创意角色默认提出 1 至 2 个候选，说明研究假设、公式、信号规则和验证计划。可执行规则使用受限 JSON：支持动量、均量比、收盘价/成交量均线交叉及 `all`/`any` 条件组合。要求回测且提供股票代码和区间时，角色会把候选规则交给 `backtest_a_share_strategy`；例如可以验证「20 日动量为正且 5 日均量高于 20 日均量」的规则。规则经过白名单校验，不执行模型生成的 Python。信号使用收盘数据，按下一可用开盘执行，并与买入持有和沪深 300 比较；没有要求回测时只给方案，不擅自取数。

也可以分两轮进行：先提出创意，再追问“回测刚才那个规则”，自动模式会保留创意角色和前轮规则作为上下文。追问回测时仍需提供股票代码和起止日期。

通用执行器按每次仓位变化收取统一费用（默认 10 bps），支持显式设置统一滑点（默认 0 bps）；旧 `backtest_a_share_momentum` 工具保留，作为固定 20 日动量兼容入口。

#### 回测引擎边界

- **输入与范围**：单只 A 股、日线 OHLCV（所用规则需要的字段必须存在）；仅支持 0/1 仓位，即做多或空仓，不支持多股票组合、仓位权重、做空、杠杆或融资融券。
- **策略规则**：仅接受经白名单校验的 JSON DSL：动量、均量比、收盘价或成交量的短长均线差，以及 `all`/`any` 条件组合和数值比较。不执行模型生成的 Python，也不支持任意表达式、止损止盈、持有期、仓位规模或订单规则。
- **信号和成交时序**：指标在收盘后计算，目标仓位于下一条可用日线的开盘生效；收益以连续开盘价之间的区间收益计量。回测开始前的行情仅用于指标预热，缺少可用的前收盘信号时会报错；样本最后一个开盘只作为估值点，不强制平仓，也没有其后的收益区间。
- **费用和成交假设**：每次空仓/持仓变化扣除同一费率和滑点，不区分买卖方向。当前不检查涨跌停是否可成交、停牌、部分成交、成交量容量、市场冲击、盘中路径或订单类型；费率/滑点是简化参数，不是实际券商费用或真实冲击模型。不同步的沪深 300 价格指数只按个股回测日期取开盘价比较，基准不扣费用。
- **公司行为与绩效**：没有单独建模现金分红、配股、退市或公司行动现金流；行情使用数据源返回的前复权日线，因此结果依赖数据源的复权口径。当前报告总收益、最大回撤和交易次数，不计算年化收益、Sharpe、胜率、换手率或组合风险暴露。

#### 已覆盖的离线模拟场景

单元测试使用合成日线检查上涨、横盘、下跌、成交量确认、均线规则、下一开盘执行与跳空、成本/滑点影响、预热不足、缺少必需字段、非法规则、基准日期缺失及超限费用参数。这些测试验证计算与边界处理，不代表真实行情下的策略有效性。项目另有跨股票/跨年份验证脚本，但需要行情服务；未运行该脚本不应视作真实行情验证已完成。真实模型加行情的端到端调用也需另行配置并运行。

### A 股因子研究

可选免费日线源 BaoStock：在项目 Python 环境运行 `python -m pip install baostock`，
然后启动时加 `--provider baostock`，或在 `config_api_keys` 中设 `CN_DATA_PROVIDER=baostock`。
仅沪深个股日线切换到 BaoStock；其他接口继续用 AkShare。默认路由不变。
动量回测同时返回沪深 300（`000300`）价格指数基准，按个股同一组开盘日期取样，不扣交易费。

在 `finrobot_autogen/` 目录中，用你已配置的模型运行：

```bash
python run_agent.py "计算 600519 截至 2024-06-28 的动量、波动率和成交活跃度" --role A_Share_Factor_Researcher --model deepseek-flash
```

因子由代码计算，LLM 只负责调用和解释。`get_a_share_factors` 接收六位股票代码和截止日，使用 A 股前复权日线，返回实际行情日期、公式、单位及数值：

| 因子 | 计算口径 |
| --- | --- |
| 20 / 60 期动量 | 当日收盘价 / 20 或 60 条日线前的收盘价 - 1 |
| 20 期年化波动率 | 最近 20 个日收益率的样本标准差 × √252 |
| 20 期日均成交量 | 最近 20 条日线的平均成交量，单位为手/日 |

窗口按实际可用日线计算，而非交易所日历；指标在行情日期收盘后才可用。数据不足或缺失的指标返回 `null`。日均成交量是成交活跃度代理，不是跨股票标准化的流动性得分。研究代码可以直接调用 `compute_price_factors` 计算输入日线的历史序列，不需要 LLM。

量化角色也可继续使用固定 20 日动量工具：“回测 600519 在 2024-01-02 至 2024-06-28 的 20 日动量策略，与买入持有比较。”其兼容入口和新策略执行器采用相同的下一开盘、费用和最后开盘计价约定。

离线测试（不请求行情或 LLM）：`python -m unittest discover -s tests -p test_factors.py`。

### 因子 Agent 验收

在 `finrobot_autogen/` 目录运行。离线测试用固定的 24 条合成日线核对数值，并检查因子角色只能调用注册的因子、回测工具，不能执行通用代码：

```bash
python -m unittest discover -s tests -p "test_*.py"
```

真实模型验收用同一份固定行情，不请求行情接口；需要在 `OAI_CONFIG_LIST` 中配置模型和有效密钥。将 `MODEL_NAME` 换成已配置的模型名：

```bash
python -m experiments.evaluate_factor_agent --model MODEL_NAME --output experiments/results/factor_agent_acceptance.json
```

脚本用普通问法和改写问法测试因子查询，另外测试三方回测比较、无效日期、缺少个股日线和缺少基准开盘价。它记录工具及参数、工具结果、最终回答和对照值；任一用例失败时仍保存结果，并返回非零退出码。

### 动量策略批量验证

在 `finrobot_autogen/` 目录中运行，无需 LLM 密钥。默认使用 BaoStock 前复权个股日线（需安装 `baostock`）和 AkShare 沪深 300 指数日线：

```bash
python -m experiments.validate_momentum --output experiments/results/momentum_2022_2025.json
```

脚本对 5 只股票分别运行 2022–2025 年的回测。每年独立从空仓开始，可用此前的日线计算开年信号；各组按年内首个开盘至最后一个开盘计算，仓位变化扣 10 基点。个股与指数的交易日期不完全匹配时会报错，避免混入缩短区间的结果。输出包含逐组指标以及按年份和全样本汇总；均值是各组收益的简单平均，不是一个可交易组合的收益。

用同一规则验证 2026 年上半年的独立样本，无需 LLM 密钥：

```bash
python -m experiments.validate_momentum_holdout --output experiments/results/momentum_holdout_2026_h1.json
```

样本从 BaoStock 截至 2025-12-31 的沪深 300 成分名单中选取，排除前一轮的五只股票，各市场按完整股票代码的 SHA-256 排序取前十只。行情缺失或日期不齐的股票会记在 `failures`，不补抽；汇总仅包含成功案例。策略、买入持有及沪深 300 都按区间内首尾开盘计价，沿用原先的费用和成交假设。

---

## 4. 安全提示

- 仓库里的这两个文件只是**模板占位符**，请不要把填写了真实 key 的版本提交上去。
- 填好 key 后，可以在本地执行下面命令，让 git 忽略这两个文件的改动，避免误提交：
  ```bash
  git update-index --skip-worktree finrobot_autogen/OAI_CONFIG_LIST finrobot_autogen/config_api_keys
  ```
- 如果以后需要重新提交模板（或解决拉取冲突），先取消标记：
  ```bash
  git update-index --no-skip-worktree finrobot_autogen/OAI_CONFIG_LIST finrobot_autogen/config_api_keys
  ```
