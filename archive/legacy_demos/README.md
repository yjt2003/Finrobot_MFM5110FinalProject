# 旧投研演示归档

这里保留三个独立演示的源码和专用配置，不属于 A 股投研主入口，也不会由 `run_agent.py` 自动调用。文件保留了移动前的工作区内容，包括未提交的修改。

| 案例 | 归档文件 | 原路径（相对于仓库根目录） |
| --- | --- | --- |
| 贵州茅台走势预测 | [入口](agent_builder_demo.py)、[建组配置](configs/save_config_forecaster.json) | `finrobot_autogen/agent_builder_demo.py`、`finrobot_autogen/configs/save_config_forecaster.json` |
| 道琼斯 30 股多因子 | [入口](experiments/multi_factor_agents.py)、[角色配置](experiments/quantitative_investment_group_config.json) | `finrobot_autogen/experiments/multi_factor_agents.py`、`finrobot_autogen/experiments/quantitative_investment_group_config.json` |
| 拼多多目标价 | [入口](experiments/portfolio_optimization.py)、[投研组配置](experiments/investment_group.py) | `finrobot_autogen/experiments/portfolio_optimization.py`、`finrobot_autogen/experiments/investment_group.py` |

## 恢复与运行条件

将需要的案例文件按表中路径恢复到 `finrobot_autogen` 下，保留配套文件的相对位置。旧脚本尚未在归档目录内验证运行，且相对路径依赖启动时的工作目录：

- 茅台演示从 `finrobot_autogen` 启动，读取该目录下的 `OAI_CONFIG_LIST` 和 `configs/save_config_forecaster.json`。
- 道琼斯脚本从原 `experiments` 目录读取同目录的角色配置；脚本也从当前目录查找 `OAI_CONFIG_LIST`，如要恢复运行，应调整其配置路径指向主项目的本地配置。
- 拼多多脚本从原 `experiments` 目录启动，读取上一级的 `OAI_CONFIG_LIST`、`config_api_keys`，并使用文档检索环境访问 SEC 文件。

没有将本地模型或数据源密钥文件移入归档。主项目的启动方式见[运行说明](../../finrobot_autogen/CONFIG_README.md)。
