import json
import unittest
from unittest.mock import patch

from experiments.evaluate_factor_agent import acceptance_cases, assess, market_fixture
from finrobot.agents.workflow import SingleAssistant
from finrobot.functional.factors import CNStockUtils, get_a_share_factors
from finrobot.functional.momentum_backtest import backtest_a_share_momentum


class AcceptanceTests(unittest.TestCase):
    def test_fixed_market_data_matches_manual_expectations(self):
        factors, _, backtest, *_ = acceptance_cases()
        bars, index = market_fixture()
        with (
            patch.object(CNStockUtils, "get_stock_data", return_value=bars),
            patch("finrobot.functional.momentum_backtest._csi300_open", return_value=index),
        ):
            factor = json.loads(get_a_share_factors(**factors.arguments))
            result = json.loads(backtest_a_share_momentum(**backtest.arguments))
        self.assertAlmostEqual(
            factor["factors"]["volatility_20d"]["value"] * 100,
            factors.expected["volatility_20d_annualized_pct"],
        )
        self.assertAlmostEqual(result["strategy"]["total_return"] * 100, 9.89)
        self.assertAlmostEqual(result["buy_and_hold"]["max_drawdown"] * 100, 0.0)
        self.assertAlmostEqual(result["csi300"]["max_drawdown"] * 100, 10.0)

    def test_grader_catches_wrong_arguments_and_percent_units(self):
        case = acceptance_cases()[0]
        call = {"name": case.tool, "arguments": case.arguments, "success": True}
        self.assertEqual(assess(case, [call], json.dumps(case.expected) + "\nTERMINATE"), [])
        wrong = {**case.expected, "momentum_20d_pct": -0.2}
        call["arguments"] = {**case.arguments, "symbol": "000001"}
        self.assertEqual(
            assess(case, [call], json.dumps(wrong) + "\nTERMINATE"),
            ["unexpected tool or arguments", "incorrect answer field: momentum_20d_pct"],
        )

    def test_error_answer_cannot_report_guessed_numbers(self):
        case = acceptance_cases()[-1]
        call = {"name": case.tool, "arguments": case.arguments, "success": False}
        self.assertEqual(assess(case, [call], json.dumps(case.expected) + "\nTERMINATE"), [])
        wrong = {**case.expected, "csi300_return_pct": 0}
        self.assertIn("unexpected final answer fields", assess(case, [call], json.dumps(wrong) + "\nTERMINATE"))

    def test_factor_role_disables_general_code_execution(self):
        agent = SingleAssistant(
            "A_Share_Factor_Researcher",
            llm_config={"config_list": [{"model": "unused-in-test", "api_key": "not-a-real-key"}]},
            code_execution_config={"use_docker": False},
        )
        self.assertIs(agent.user_proxy._code_execution_config, False)
        self.assertEqual(set(agent.user_proxy.function_map),
                         {"get_a_share_factors", "backtest_a_share_momentum"})

    def test_ideator_only_has_constrained_strategy_backtest_tool(self):
        agent = SingleAssistant(
            "A_Share_Factor_Ideator",
            llm_config={"config_list": [{"model": "unused-in-test", "api_key": "not-a-real-key"}]},
        )
        self.assertIs(agent.user_proxy._code_execution_config, False)
        self.assertEqual(set(agent.user_proxy.function_map), {"backtest_a_share_strategy"})


if __name__ == "__main__":
    unittest.main()
