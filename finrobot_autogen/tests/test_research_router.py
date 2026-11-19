import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

import run_agent
from experiments.evaluate_factor_agent import market_fixture
from finrobot.agents.research_router import AutoResearch, DEFAULT_ROLE, FACTOR_IDEA_ROLE, SCOPES, route_research
from finrobot.agents.workflow import FinRobot, SingleAssistant
from finrobot.functional.factors import CNStockUtils


MODEL_CONFIG = {"config_list": [{"model": "unused-in-test", "api_key": "not-a-real-key"}]}
QUANT = "A_Share_Factor_Researcher"
MARKET = "Market_Analyst"


class ResearchRouterTests(unittest.TestCase):
    def test_factor_ideation_is_distinct_from_factor_calculation_and_backtest(self):
        for question, role in (
            ("因子创意生成", FACTOR_IDEA_ROLE),
            ("能不能用成交量改进现有动量策略？", FACTOR_IDEA_ROLE),
            ("给我几个因子创意", FACTOR_IDEA_ROLE),
            ("设计一个新因子，并回测它", FACTOR_IDEA_ROLE),
            ("Suggest a volume-based factor", FACTOR_IDEA_ROLE),
            ("计算 600519 截至 2024-06-28 的 20 日动量因子", QUANT),
            ("回测刚才的新因子", QUANT),
            ("回测现有的 20 日动量策略", QUANT),
            ("Backtest the factor idea", QUANT),
            ("生成一份因子研究报告", "Expert_Investor"),
        ):
            with self.subTest(question=question):
                self.assertEqual(route_research(question), (role,))
        self.assertEqual(route_research("还有别的吗？", (FACTOR_IDEA_ROLE,)), (FACTOR_IDEA_ROLE,))
        self.assertEqual(
            route_research("回测刚才那个规则，用 600519 的区间数据", (FACTOR_IDEA_ROLE,)),
            (FACTOR_IDEA_ROLE,),
        )

    def test_factor_ideator_only_has_constrained_backtest_tool_and_no_code_execution(self):
        agent = SingleAssistant(
            FACTOR_IDEA_ROLE,
            llm_config=MODEL_CONFIG,
            code_execution_config={"use_docker": False},
        )
        self.assertEqual([tool.__name__ for tool in agent.assistant.toolkits], ["backtest_a_share_strategy"])
        self.assertEqual(set(agent.user_proxy.function_map), {"backtest_a_share_strategy"})
        self.assertIs(agent.user_proxy._code_execution_config, False)

    def test_single_and_composite_routing(self):
        for question, expected in (
            ("计算 600519 截至 2024-06-28 的动量和波动率", (QUANT,)),
            ("解释动量回测为什么跑输沪深 300", (QUANT,)),
            ("600519 最近为什么涨？", (MARKET,)),
            ("请分析财报、ROE 和估值", (DEFAULT_ROLE,)),
            ("综合分析 600519", tuple(SCOPES)),
            ("综合分析 600519 的基本面、近期新闻和波动率", tuple(SCOPES)),
            ("综合分析基本面和近期公告", (DEFAULT_ROLE, MARKET)),
            ("生成一份含估值和公告的研究报告", ("Expert_Investor",)),
            ("帮我看看 600519", (DEFAULT_ROLE,)),
        ):
            with self.subTest(question=question):
                self.assertEqual(route_research(question), expected)

    def test_single_uses_no_synthesizer_and_retains_follow_up_context(self):
        research = AutoResearch(MODEL_CONFIG)
        question = "计算 600519 截至 2024-06-28 的动量"
        with patch.object(research, "_reply", return_value={"answer": "动量证据"}) as reply:
            self.assertEqual(research.answer(question, research.plan(question)), "动量证据")
            reply.assert_called_once()
            self.assertEqual(reply.call_args.args[0], QUANT)
            self.assertEqual(research.plan("那 000001 呢？"), (QUANT,))
            research.answer("那 000001 呢？", (QUANT,))
            self.assertIn(question, reply.call_args.args[1])

    def test_specialists_and_synthesizer_use_separate_autogen_chats(self):
        called = []

        def model(agent, client, messages, cache):
            called.append(agent.name)
            prompt = messages[-1]["content"]
            if agent.name == "Research_Synthesizer":
                self.assertEqual(agent.toolkits, [])
                self.assertIn("财务证据", prompt)
                self.assertIn("市场证据", prompt)
                return "综合结论\nTERMINATE"
            self.assertIn("600519", prompt)
            self.assertIn("2024-06-28", prompt)
            return ("财务证据" if agent.name == DEFAULT_ROLE else "市场证据") + "\nTERMINATE"

        question = "分析 600519 截至 2024-06-28 的基本面和新闻"
        research = AutoResearch(MODEL_CONFIG)
        with patch.object(FinRobot, "_generate_oai_reply_from_client", autospec=True, side_effect=model):
            self.assertEqual(research.answer(question, research.plan(question)), "综合结论")
        self.assertEqual(called, [DEFAULT_ROLE, MARKET, "Research_Synthesizer"])

    def test_quant_route_executes_registered_tool_on_fixed_data(self):
        bars, _ = market_fixture()
        question = "计算 600519 截至 2024-02-01 的动量"

        def model(agent, client, messages, cache):
            self.assertEqual(agent.name, QUANT)
            if messages[-1]["role"] == "tool":
                evidence = json.loads(messages[-1]["tool_responses"][0]["content"])
                momentum = evidence["factors"]["momentum_20d"]["value"]
                return f"20 日动量：{momentum:.2%}\nTERMINATE"
            return {"tool_calls": [{
                "id": "fixture-call",
                "type": "function",
                "function": {
                    "name": "get_a_share_factors",
                    "arguments": json.dumps({"symbol": "600519", "as_of": "2024-02-01"}),
                },
            }]}

        research = AutoResearch(MODEL_CONFIG)
        with (
            patch.object(CNStockUtils, "get_stock_data", return_value=bars) as fetch,
            patch.object(FinRobot, "_generate_oai_reply_from_client", autospec=True, side_effect=model) as llm,
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(research.answer(question, research.plan(question)), "20 日动量：-20.00%")
        fetch.assert_called_once()
        self.assertEqual(llm.call_count, 2)

    def test_partial_failure_reported_even_when_summary_succeeds(self):
        research = AutoResearch(MODEL_CONFIG)
        question = "分析基本面和新闻"
        replies = [{"answer": "财务证据"}, {"error": "TimeoutError"}, {"answer": "汇总回答"}]
        with patch.object(research, "_reply", side_effect=replies) as reply:
            answer = research.answer(question, research.plan(question))
        self.assertIn("财务证据", reply.call_args.args[1])
        self.assertIn("汇总回答", answer)
        self.assertIn("Market_Analyst（TimeoutError）", answer)

    def test_failed_summary_falls_back_to_successful_sections(self):
        research = AutoResearch(MODEL_CONFIG)
        question = "分析基本面和新闻"
        replies = [{"answer": "财务证据"}, {"error": "TimeoutError"}, {"error": "APIError"}]
        with patch.object(research, "_reply", side_effect=replies):
            answer = research.answer(question, research.plan(question))
        self.assertIn("未完成汇总", answer)
        self.assertIn("财务证据", answer)
        self.assertIn("Market_Analyst（TimeoutError）", answer)

    def test_all_failed_skips_summary(self):
        research = AutoResearch(MODEL_CONFIG)
        with patch.object(research, "_reply", return_value={"error": "TimeoutError"}) as reply:
            answer = research.answer("分析基本面和新闻", (DEFAULT_ROLE, MARKET))
        self.assertEqual(reply.call_count, 2)
        self.assertIn("本轮没有可用的分项结果", answer)


class ResearchCliTests(unittest.TestCase):
    def test_ideation_runs_through_auto_cli_and_explicit_role(self):
        question = "设计一个新因子，并回测它"
        answer = "候选：量能确认动量。可执行规则已列出，验证状态：未回测。"

        def model(agent, client, messages, cache):
            self.assertEqual(agent.name, FACTOR_IDEA_ROLE)
            self.assertEqual([tool.__name__ for tool in agent.toolkits], ["backtest_a_share_strategy"])
            self.assertIn(question, messages[-1]["content"])
            self.assertIn("backtest_a_share_strategy", agent.system_message)
            return answer + "\nTERMINATE"

        for flags in ([], ["--role", FACTOR_IDEA_ROLE]):
            with (
                self.subTest(flags=flags),
                patch.object(run_agent, "register_keys_from_json"),
                patch.object(run_agent.autogen, "config_list_from_json", return_value=MODEL_CONFIG["config_list"]),
                patch.object(FinRobot, "_generate_oai_reply_from_client", autospec=True, side_effect=model) as llm,
                redirect_stdout(StringIO()) as stdout,
                redirect_stderr(StringIO()) as stderr,
            ):
                run_agent.main([question, *flags])
                llm.assert_called_once()
                self.assertIn(answer, stdout.getvalue())
                if not flags:
                    self.assertIn(FACTOR_IDEA_ROLE, stderr.getvalue())

    def test_ideator_can_generate_and_execute_rule_in_one_research_turn(self):
        bars, benchmark = market_fixture()
        question = "设计并回测 600519 的量能确认动量策略，区间 2024-01-30 至 2024-02-01"
        rule = {
            "name": "volume_confirmed_momentum",
            "signal": {"all": [
                {"indicator": "momentum", "window": 20, "operator": ">", "value": 0},
                {"indicator": "average_volume_ratio", "short_window": 5,
                 "long_window": 20, "operator": ">", "value": 1},
            ]},
        }
        tool_call = {
            "id": "strategy-call",
            "type": "function",
            "function": {
                "name": "backtest_a_share_strategy",
                "arguments": json.dumps({
                    "symbol": "600519", "start": "2024-01-30", "end": "2024-02-01",
                    "rule_json": json.dumps(rule), "cost_bps": 10, "slippage_bps": 0,
                }),
            },
        }

        def model(agent, client, messages, cache):
            self.assertEqual(agent.name, FACTOR_IDEA_ROLE)
            if messages[-1]["role"] == "tool":
                result = json.loads(messages[-1]["tool_responses"][0]["content"])
                self.assertEqual(result["rule"]["name"], rule["name"])
                self.assertIn("csi300", result)
                return "该规则已按原规则完成回测，历史表现不代表未来收益。\nTERMINATE"
            return {"tool_calls": [tool_call]}

        research = AutoResearch(MODEL_CONFIG)
        with (
            patch.object(CNStockUtils, "get_stock_data", return_value=bars),
            patch("finrobot.functional.momentum_backtest._csi300_open", return_value=benchmark),
            patch.object(FinRobot, "_generate_oai_reply_from_client", autospec=True, side_effect=model),
            redirect_stdout(StringIO()),
        ):
            answer = research.answer(question, research.plan(question))
        self.assertIn("已按原规则完成回测", answer)

    def test_default_auto_and_explicit_single_compatibility(self):
        with (
            patch.object(run_agent, "register_keys_from_json"),
            patch.object(run_agent.autogen, "config_list_from_json", return_value=MODEL_CONFIG["config_list"]),
            patch.object(run_agent, "run_auto") as run_auto,
            patch.object(run_agent, "SingleAssistant") as single,
            patch.object(run_agent, "ask") as ask,
            redirect_stdout(StringIO()),
        ):
            run_agent.main(["分析基本面和新闻"])
            run_auto.assert_called_once()
            single.assert_not_called()
            run_agent.main(["问句", "--mode", "single"])
            self.assertEqual(single.call_args.args[0], DEFAULT_ROLE)
            ask.assert_called_once_with(single.return_value, "问句", clear_history=True)
            run_agent.main(["问句", "--role", QUANT])
            self.assertEqual(single.call_args.args[0], QUANT)
            run_auto.assert_called_once()

    def test_auto_rejects_fixed_role(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as exit:
            run_agent.main(["--mode", "auto", "--role", QUANT])
        self.assertEqual(exit.exception.code, 2)
