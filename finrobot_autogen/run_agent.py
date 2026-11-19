"""FinRobot V0 快速启动脚本（用 finrobot conda 环境运行）。

默认按问题自动分派；使用 --mode single 或仅指定 --role 可运行单角色。

用法:
    # 自动分派，交互模式
    python run_agent.py

    # 单次模式
    python run_agent.py "综合分析 600519 的基本面、近期新闻和波动率"

    # 其他角色：Market_Analyst（市场/情绪）、Expert_Investor（研究报告）
    python run_agent.py "600519 最近为什么涨" --role Market_Analyst
"""
import argparse
import os
import sys
from pathlib import Path

import autogen

from finrobot.agents.research_router import AutoResearch, DEFAULT_ROLE
from finrobot.agents.workflow import SingleAssistant
from finrobot.data_source import describe_provider
from finrobot.utils import register_keys_from_json

BASE_DIR = Path(__file__).resolve().parent

TERMINATE_HINT = "\n\n（完成回答后，请在最后一行单独输出 TERMINATE 以结束本次对话。）"

EXIT_COMMANDS = {"exit", "quit", ":q", "/exit"}


def with_terminate_hint(prompt: str) -> str:
    if "TERMINATE" in prompt.upper():
        return prompt
    return prompt + TERMINATE_HINT


def ask(assistant: SingleAssistant, message: str, clear_history: bool = False):
    # 直接调用 initiate_chat 并保留历史，不用 chat()（它结束后会 reset 清空上下文）
    return assistant.user_proxy.initiate_chat(
        assistant.assistant,
        message=with_terminate_hint(message),
        clear_history=clear_history,
    )


def interactive_questions():
    while True:
        try:
            message = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not message:
            continue
        if message.lower() in EXIT_COMMANDS:
            break
        yield message


def repl(assistant: SingleAssistant):
    print(f"已进入交互模式（角色: {assistant.assistant.name}）。输入 exit / quit / :q 退出。")
    first_turn = True
    for message in interactive_questions():
        ask(assistant, message, clear_history=first_turn)
        first_turn = False


def run_auto(llm_config, prompt):
    research = AutoResearch(llm_config)
    if prompt is None:
        print("已进入交互模式（自动分派）。输入 exit / quit / :q 退出。")
    for question in interactive_questions() if prompt is None else (prompt,):
        roles = research.plan(question)
        print("分派角色：" + "、".join(roles), file=sys.stderr, flush=True)
        print(research.answer(question, roles))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a FinRobot V0 agent")
    parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="单次提问；省略则进入交互模式",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "single"],
        help="auto：按问题分派（默认）；single：固定单角色。仅指定 --role 时沿用 single。",
    )
    parser.add_argument(
        "--role",
        help="指定单角色：A_Share_Analyst / A_Share_Factor_Researcher / A_Share_Factor_Ideator / Market_Analyst / Expert_Investor",
    )
    parser.add_argument("--model", default="gpt-6.2-sol")
    parser.add_argument(
        "--provider",
        choices=["auto", "akshare", "tushare", "baostock"],
        default=None,
        help="A 股数据源：auto（默认）/ akshare / tushare / baostock（个股日线）",
    )
    args = parser.parse_args(argv)
    if args.mode == "auto" and args.role:
        parser.error("--mode auto 不能与 --role 一起使用；固定角色请使用 --mode single。")
    mode = args.mode or ("single" if args.role else "auto")

    register_keys_from_json(str(BASE_DIR / "config_api_keys"))
    if args.provider:
        os.environ["CN_DATA_PROVIDER"] = args.provider
    print(describe_provider())

    llm_config = {
        "config_list": autogen.config_list_from_json(
            str(BASE_DIR / "OAI_CONFIG_LIST"),
            filter_dict={"model": [args.model]},
        ),
        "timeout": 120,
        "temperature": 0,
    }

    if mode == "auto":
        run_auto(llm_config, args.prompt)
        return

    assistant = SingleAssistant(
        args.role or DEFAULT_ROLE,
        llm_config=llm_config,
        # AutoGen 约定：收到含 TERMINATE 的回复即结束本轮对话
        is_termination_msg=lambda x: "TERMINATE" in (x.get("content") or ""),
    )

    if args.prompt:
        ask(assistant, args.prompt, clear_history=True)
    else:
        repl(assistant)


if __name__ == "__main__":
    main()
