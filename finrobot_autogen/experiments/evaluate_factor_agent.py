"""Evaluate the real factor Agent against fixed, hand-checkable market data."""

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
import json
import math
from pathlib import Path
from unittest.mock import patch

import autogen
import pandas as pd

from finrobot.agents.workflow import SingleAssistant
from finrobot.functional.factors import CNStockUtils
from finrobot.functional import momentum_backtest
from run_agent import BASE_DIR, ask


@dataclass(frozen=True)
class Case:
    name: str
    question: str
    tool: str
    arguments: dict
    expected: dict
    fixture: str = "normal"


def market_fixture():
    dates = pd.bdate_range("2024-01-01", periods=24)
    bars = pd.DataFrame(
        {"Open": [100] * 22 + [110, 121], "Close": [100] * 21 + [120, 80, 80],
         "Volume": [1000] * 24},
        index=dates,
    )
    return bars, pd.Series([200, 180, 220], index=dates[-3:])


def acceptance_cases():
    # The 20-return window contains only two nonzero returns: +1/5 and -1/3.
    variance = ((1 / 5) ** 2 + (1 / 3) ** 2 - ((1 / 5 - 1 / 3) ** 2 / 20)) / 19
    factors = {
        "status": "ok", "symbol": "600519", "date": "2024-02-01",
        "momentum_20d_pct": -20.0, "momentum_60d_pct": None,
        "volatility_20d_annualized_pct": math.sqrt(variance * 252) * 100,
        "avg_volume_20d_lots_per_day": 1000.0,
    }
    backtest = {
        "status": "ok", "symbol": "600519", "first_open": "2024-01-30",
        "last_open": "2024-02-01", "cost_bps": 10,
        "strategy_return_pct": 9.89, "strategy_drawdown_pct": 0.0,
        "buy_and_hold_return_pct": 20.879, "buy_and_hold_drawdown_pct": 0.0,
        "csi300_return_pct": 10.0, "csi300_drawdown_pct": 10.0,
        "best_return": "buy_and_hold", "lowest_drawdown": ["strategy", "buy_and_hold"],
    }
    factor_args = {"symbol": "600519", "as_of": "2024-02-01"}
    backtest_args = {"symbol": "600519", "start": "2024-01-30", "end": "2024-02-01"}
    return (
        Case("factors", "计算 600519 截至 2024-02-01 的动量、年化波动率与成交活跃度。",
             "get_a_share_factors", factor_args, factors),
        Case("factors_paraphrase", "以 2024 年 2 月 1 日收盘为界，600519 涨跌趋势、风险和成交量如何？请给出 20、60 期动量、20 期年化波动率和平均成交量。",
             "get_a_share_factors", factor_args, factors),
        Case("backtest", "比较 600519 从 2024-01-30 到 2024-02-01 的动量策略、买入持有和沪深 300，按默认交易费。",
             "backtest_a_share_momentum", backtest_args, backtest),
        Case("invalid_date", "计算 600519 截至 2024-02-30 的因子。不要替我修改日期。",
             "get_a_share_factors", {"symbol": "600519", "as_of": "2024-02-30"},
             {"status": "error", "reason": "invalid_date"}, "invalid_date"),
        Case("missing_bars", "计算 600519 截至 2024-02-01 的因子。",
             "get_a_share_factors", factor_args, {"status": "error", "reason": "missing_bars"}, "missing_bars"),
        Case("missing_benchmark", "回测 600519 从 2024-01-30 到 2024-02-01 的动量策略并比较沪深 300。",
             "backtest_a_share_momentum", backtest_args,
             {"status": "error", "reason": "missing_benchmark"}, "missing_benchmark"),
    )


def case_prompt(case):
    if case.tool == "get_a_share_factors":
        schema = ("成功字段：status, symbol, date, momentum_20d_pct, momentum_60d_pct, "
                  "volatility_20d_annualized_pct, avg_volume_20d_lots_per_day。")
    else:
        schema = ("成功字段：status, symbol, first_open, last_open, cost_bps, strategy_return_pct, "
                  "strategy_drawdown_pct, buy_and_hold_return_pct, buy_and_hold_drawdown_pct, "
                  "csi300_return_pct, csi300_drawdown_pct, best_return, lowest_drawdown。"
                  "best_return 填 strategy、buy_and_hold、csi300 之一；"
                  "lowest_drawdown 填所有并列最小回撤者的数组，按上述顺序。")
    return (
        f"{case.question}\n最终答复仅输出一个 JSON 对象，然后单独一行 TERMINATE；不要加说明。"
        f"{schema}成功时 status=ok。"
        "*_pct 用百分数的数值（例如 3.14 表示 3.14%），无足够历史计算的单项写 null。"
        "失败时只返回 status=error 及 reason，reason 从 invalid_date、missing_bars、"
        "missing_benchmark、other 中选。不要用估算替代工具。"
    )


def assess(case, calls, answer):
    failures = []
    expect_ok = case.expected["status"] == "ok"
    if not calls and case.fixture != "invalid_date":
        failures.append("no tool called")
    for call in calls:
        args = call["arguments"]
        if isinstance(args, dict):
            args = dict(args)
            if case.tool == "backtest_a_share_momentum" and args.get("cost_bps") == 10:
                args.pop("cost_bps")
        if call["name"] != case.tool or args != case.arguments:
            failures.append("unexpected tool or arguments")
        if call["success"] != expect_ok:
            failures.append("unexpected tool outcome")

    lines = answer.strip().splitlines()
    if not lines or lines[-1].strip() != "TERMINATE":
        return failures + ["final answer did not terminate"]
    body = "\n".join(lines[:-1]).strip()
    if body.startswith("```json") and body.endswith("```"):
        body = body[7:-3].strip()
    try:
        parsed = json.loads(body)
    except ValueError:
        return failures + ["final answer is not JSON"]
    if not isinstance(parsed, dict) or parsed.keys() != case.expected.keys():
        return failures + ["unexpected final answer fields"]
    for key, expected in case.expected.items():
        actual = parsed[key]
        if type(expected) is float:
            match = type(actual) in (float, int) and math.isclose(actual, expected, abs_tol=0.0051)
        elif type(expected) is int:
            match = type(actual) in (float, int) and actual == expected
        else:
            match = type(actual) is type(expected) and actual == expected
        if not match:
            failures.append(f"incorrect answer field: {key}")
    return failures


def run_case(case, llm_config):
    bars, index = market_fixture()
    if case.fixture == "missing_bars":
        bars = bars.iloc[:0]
    if case.fixture == "missing_benchmark":
        index = index.iloc[1:]
    calls = []
    answer = ""
    try:
        agent = SingleAssistant("A_Share_Factor_Researcher", llm_config=llm_config,
                                max_consecutive_auto_reply=5)
        execute = agent.user_proxy.execute_function

        def record(call, *args, **kwargs):
            success, result = execute(call, *args, **kwargs)
            try:
                arguments = json.loads(call.get("arguments", "{}"))
            except ValueError:
                arguments = None
            calls.append({"name": call.get("name"), "arguments": arguments,
                          "success": success, "output": result["content"]})
            return success, result

        with (
            patch.object(CNStockUtils, "get_stock_data", return_value=bars),
            patch.object(momentum_backtest, "_csi300_open", return_value=index),
            patch.object(agent.user_proxy, "execute_function", side_effect=record),
            redirect_stdout(StringIO()),
        ):
            chat = ask(agent, case_prompt(case), clear_history=True)
        answer = chat.chat_history[-1].get("content") or ""
        failures = assess(case, calls, answer)
    except Exception as exc:
        # API exceptions can include request details; retain only their type.
        failures = [f"evaluation failed: {type(exc).__name__}"]
    return {"case": case.name, "passed": not failures, "failures": failures,
            "calls": calls, "answer": answer, "expected": case.expected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    models = autogen.config_list_from_json(str(BASE_DIR / "OAI_CONFIG_LIST"),
                                          filter_dict={"model": [args.model]})
    if not models:
        parser.error("No matching model in OAI_CONFIG_LIST.")
    config = {"config_list": models, "temperature": 0, "timeout": 90, "cache_seed": None}
    results = []
    for case in acceptance_cases():
        result = run_case(case, config)
        results.append(result)
        print(case.name, "PASS" if result["passed"] else f"FAIL: {result['failures']}", flush=True)
    passed = sum(r["passed"] for r in results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"model": args.model, "passed": passed, "cases": results},
                                      indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                           encoding="utf-8")
    print(f"{passed}/{len(results)} passed. Wrote {args.output}")
    if passed != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
