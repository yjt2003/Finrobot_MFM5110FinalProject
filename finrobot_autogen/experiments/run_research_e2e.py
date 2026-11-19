"""Exercise live CLI research scenarios and save a trace for manual acceptance."""

import argparse
import json
import os
import time
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from autogen import UserProxyAgent
from autogen.oai.client import OpenAIWrapper

import run_agent
from finrobot.agents.research_router import AutoResearch


CASES = {
    "fundamentals": ("A_Share_Analyst", [
        "请调工具核验贵州茅台（600519）2023、2024年度基本面。列营收、归母净利润、经营现金流，"
        "标明报告期、单位、来源；比较现金流对利润的覆盖。缺项直说，不用当前估值。",
    ]),
    "market": (None, [
        "复盘贵州茅台600519在2024-06-24至2024-06-28的股价异动和新闻/公告。"
        "用工具核验首尾收盘和涨跌；核验至少一条同窗口的有日期事件，区分事实与推测。缺项直说，不用其他日期替代。",
    ]),
    "market_recent": (None, [
        "复盘贵州茅台600519在2026-11-16至2026-11-18的股价和新闻/公告。"
        "用工具核验不复权首尾收盘和对应涨跌，列至少一条窗口内有日期和来源链接的事件。"
        "区分事实与推测；若找不到窗口内证据，不做归因。",
    ]),
    "factors": ("A_Share_Factor_Researcher", [
        "调工具计算600519截至2024-06-28的20/60日动量、20日年化波动率和20日日均成交量。列实际日期、公式和单位。",
        "同一股票，再回测2024-01-02至2024-06-28的固定20日动量策略，费用10bp。"
        "比策略、买入持有、沪深300的累计收益与最大回撤，注明信号及执行时点。",
    ]),
    "ideation": (None, [
        "为A股提出一个成交量确认的动量因子：20日动量>0且5日/20日日均成交量之比>1时持股。"
        "解释假设、缺失值、零分母、可执行rule_json及样本外验证。先不回测。",
        "请回测刚才这个因子，用600519和2024-01-02至2024-06-28，费用10bp、滑点0。原样执行规则，"
        "列策略、买入持有、沪深300的累计收益及最大回撤，说明风险。",
    ]),
    "report": (None, [
        "请生成贵州茅台600519的2024年报中文PDF研究报告。以年报披露日为截止，核验年报与关键财务数据，"
        "生成股价走势图、PE/EPS图并调用工具输出PDF。标明来源、报告期、风险和不构成投资建议。"
        "不能核实的项直说。只在 {case_dir} 保存PDF与图表，回复实际文件路径与完成状态。",
    ]),
    "auto_composite": (None, [
        "为组会综合分析600519，截至2024-06-28，分别核验当时已披露的基本面（优先2023年报）、"
        "2024-06-24至06-28新闻或公告、截止日的20日动量和20日年化波动率。按三项汇总，注明日期、单位和来源。"
        "缺数据就列缺口，不得引用截止日之后的信息作为当时已知证据。",
    ]),
}


def run_case(name, output, model, provider):
    role, prompts = CASES[name]
    case_dir = (output / name).resolve()
    case_dir.mkdir(parents=True, exist_ok=False)
    prompts = [prompt.format(case_dir=case_dir.as_posix()) for prompt in prompts]
    stdout, stderr = StringIO(), StringIO()
    calls, replies, api_errors = [], [], []
    execute = UserProxyAgent.execute_function
    reply = AutoResearch._reply
    create = OpenAIWrapper.create

    def record_tool(agent, call, *args, **kwargs):
        start = time.monotonic()
        ok, result = execute(agent, call, *args, **kwargs)
        content = str(result.get("content", ""))
        calls.append({"turn": turn + 1, "tool": call.get("name"), "arguments": call.get("arguments"),
                      "execution_ok": ok, "seconds": round(time.monotonic() - start, 2),
                      "result_chars": len(content), "result_excerpt": content[:5000],
                      "result_end": content[-600:] if len(content) > 5000 else ""})
        return ok, result

    def record_reply(research, specialist, prompt):
        answer = reply(research, specialist, prompt)
        replies.append({"turn": turn + 1, "role": specialist if isinstance(specialist, str)
                        else specialist["name"], **answer})
        return answer

    def fresh_request(wrapper, **kwargs):
        kwargs.update(cache_seed=None, cache=None)
        try:
            return create(wrapper, **kwargs)
        except Exception as exc:
            api_errors.append({"type": type(exc).__name__, "status": getattr(exc, "status_code", None)})
            raise

    turn = -1
    items = iter([*prompts, "exit"])

    def next_prompt(_):
        nonlocal turn
        turn += 1
        return next(items)

    args = ["--model", model, "--provider", provider]
    if role:
        args.extend(["--role", role])
    if len(prompts) == 1:
        args.append(prompts[0])
        turn = 0
    os.environ.setdefault("MPLBACKEND", "Agg")
    start = time.monotonic()
    error = None
    with (patch.object(UserProxyAgent, "execute_function", record_tool),
          patch.object(OpenAIWrapper, "create", fresh_request),
          patch.object(AutoResearch, "_reply", record_reply),
          patch("builtins.input", next_prompt), redirect_stdout(stdout), redirect_stderr(stderr)):
        try:
            run_agent.main(args)
        except Exception as exc:
            error = type(exc).__name__
    result = {
        "case": name, "role": role or "auto", "model": model, "provider": provider,
        "seconds": round(time.monotonic() - start, 2), "prompts": prompts,
        "error": error, "api_errors": api_errors, "calls": calls, "role_replies": replies,
        "routing_log": [line for line in stderr.getvalue().splitlines() if line.startswith("分派角色：")],
        "stdout_tail": stdout.getvalue()[-18000:],
        "artifacts": [{"name": p.name, "bytes": p.stat().st_size}
                      for p in sorted(case_dir.iterdir()) if p.is_file()],
    }
    (case_dir / "trace.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{name}: {result['seconds']}s, tools={len(calls)}, roles={result['routing_log']}, "
          f"error={error}, api_errors={api_errors}, artifacts={result['artifacts']}", flush=True)
    return error is None and not any("error" in item for item in replies)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=CASES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", default="auto", choices=["auto", "akshare", "baostock", "tushare"])
    args = parser.parse_args()
    return 0 if run_case(args.case, args.output, args.model, args.provider) else 1


if __name__ == "__main__":
    raise SystemExit(main())
