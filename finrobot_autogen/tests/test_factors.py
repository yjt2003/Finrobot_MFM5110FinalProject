import json
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from finrobot.agents.workflow import SingleAssistant
from finrobot.functional.factors import CNStockUtils, compute_price_factors, get_a_share_factors
from finrobot.functional.momentum_backtest import (
    DEFAULT_MOMENTUM_RULE,
    build_strategy_signals,
    simulate_momentum,
    simulate_signals,
    validate_strategy_rule,
)


def example_bars():
    return pd.DataFrame(
        {"Close": np.arange(100.0, 163.0), "Volume": np.arange(1000.0, 1063.0)},
        index=pd.bdate_range("2024-01-01", periods=63),
    )


class PriceFactorTests(unittest.TestCase):
    def test_backtest_executes_at_next_open(self):
        dates = pd.bdate_range("2024-01-01", periods=24)
        bars = pd.DataFrame(
            {"Open": [100] * 22 + [110, 121], "Close": [100] * 21 + [120, 80, 80]},
            index=dates,
        )
        result = simulate_momentum(bars, str(dates[21].date()), str(dates[23].date()), 0)
        self.assertAlmostEqual(result["strategy"]["total_return"], 0.10)
        self.assertAlmostEqual(result["buy_and_hold"]["total_return"], 0.21)
        self.assertEqual(result["strategy"]["trades"], 1)

        index_open = pd.Series([200, 180, 220], index=dates[21:])
        compared = simulate_momentum(
            bars, str(dates[21].date()), str(dates[23].date()), 10, index_open
        )
        self.assertAlmostEqual(compared["csi300"]["total_return"], 0.10)
        self.assertAlmostEqual(compared["csi300"]["max_drawdown"], 0.10)
        with self.assertRaisesRegex(ValueError, "CSI 300"):
            simulate_momentum(
                bars, str(dates[21].date()), str(dates[23].date()), 10, index_open.iloc[1:]
            )

    def test_general_engine_preserves_legacy_momentum_results(self):
        dates = pd.bdate_range("2024-01-01", periods=24)
        bars = pd.DataFrame(
            {"Open": [100] * 22 + [110, 121], "Close": [100] * 21 + [120, 80, 80]},
            index=dates,
        )
        legacy = simulate_momentum(bars, str(dates[21].date()), str(dates[23].date()), 10)
        generic = simulate_signals(
            bars,
            build_strategy_signals(bars, DEFAULT_MOMENTUM_RULE),
            str(dates[21].date()),
            str(dates[23].date()),
            10,
        )
        self.assertEqual(legacy["strategy"], generic["strategy"])
        self.assertEqual(legacy["buy_and_hold"], generic["buy_and_hold"])

    def test_volume_confirmed_momentum_rule_changes_positions(self):
        dates = pd.bdate_range("2024-01-01", periods=25)
        bars = pd.DataFrame(
            {
                "Open": np.linspace(100, 124, 25),
                "Close": np.linspace(100, 124, 25),
                "Volume": [1000] * 22 + [2000] * 3,
            },
            index=dates,
        )
        rule = {
            "name": "volume_confirmed_momentum",
            "signal": {"all": [
                {"indicator": "momentum", "window": 20, "operator": ">", "value": 0},
                {"indicator": "average_volume_ratio", "short_window": 5,
                 "long_window": 20, "operator": ">", "value": 1},
            ]},
        }
        signals = build_strategy_signals(bars, rule)
        self.assertFalse(bool(signals.iloc[21]))
        self.assertTrue(bool(signals.iloc[22]))
        self.assertTrue(bool(signals.iloc[24]))
        result = simulate_signals(bars, signals, str(dates[22].date()), str(dates[24].date()), 0)
        self.assertEqual(result["intervals"], 2)
        self.assertEqual(result["strategy"]["trades"], 1)

    def test_rule_validation_rejects_unsupported_or_ambiguous_rules(self):
        invalid_rules = [
            {"signal": {"all": [{"indicator": "python", "operator": ">", "value": 0}]}},
            {"signal": {"all": [{"indicator": "momentum", "window": 20,
                                   "operator": ">", "value": float("nan")}]}},
            {"signal": {"all": [{"indicator": "momentum", "window": 20,
                                   "operator": ">", "value": 0, "expression": "x"}]}},
            {"execution": "same_close", "signal": {"all": [
                {"indicator": "momentum", "window": 20, "operator": ">", "value": 0}]}},
        ]
        for rule in invalid_rules:
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                validate_strategy_rule(rule)

    def test_market_regime_and_execution_boundary_simulations(self):
        dates = pd.bdate_range("2024-01-01", periods=30)
        cases = {
            "rising": np.linspace(100, 130, len(dates)),
            "sideways": np.full(len(dates), 100.0),
            "falling": np.linspace(130, 100, len(dates)),
        }
        outcomes = {}
        for name, close in cases.items():
            bars = pd.DataFrame({"Open": close, "Close": close}, index=dates)
            signal = build_strategy_signals(bars, DEFAULT_MOMENTUM_RULE)
            outcomes[name] = simulate_signals(
                bars, signal, str(dates[22].date()), str(dates[-1].date()), cost_bps=0
            )["strategy"]
        self.assertGreater(outcomes["rising"]["total_return"], 0)
        self.assertEqual(outcomes["sideways"]["total_return"], 0)
        self.assertEqual(outcomes["sideways"]["trades"], 0)
        self.assertEqual(outcomes["falling"]["total_return"], 0)

        gap_dates = pd.bdate_range("2024-02-01", periods=24)
        gap_close = np.r_[np.full(21, 100.0), 110.0, 110.0, 110.0]
        gap_open = np.r_[np.full(22, 100.0), 150.0, 160.0]
        gap_bars = pd.DataFrame({"Open": gap_open, "Close": gap_close}, index=gap_dates)
        gap_result = simulate_momentum(
            gap_bars, str(gap_dates[21].date()), str(gap_dates[23].date()), cost_bps=0
        )
        self.assertAlmostEqual(gap_result["strategy"]["total_return"], 160 / 150 - 1)

    def test_combined_cost_and_slippage_cannot_exceed_full_capital(self):
        dates = pd.bdate_range("2024-01-01", periods=24)
        bars = pd.DataFrame({"Open": [100.0] * 24, "Close": [100.0] * 24}, index=dates)
        signals = build_strategy_signals(bars, DEFAULT_MOMENTUM_RULE)
        with self.assertRaisesRegex(ValueError, "combined rate"):
            simulate_signals(
                bars, signals, str(dates[21].date()), str(dates[-1].date()),
                cost_bps=6000, slippage_bps=4000,
            )

    def test_formulas_and_warmup(self):
        bars = example_bars()
        factors = compute_price_factors(bars.iloc[::-1])
        row = factors.iloc[-1]
        self.assertAlmostEqual(row["momentum_20d"], 162 / 142 - 1)
        self.assertAlmostEqual(row["momentum_60d"], 162 / 102 - 1)
        expected_returns = 1 / np.arange(142.0, 162.0)
        self.assertAlmostEqual(row["volatility_20d"], expected_returns.std(ddof=1) * np.sqrt(252))
        self.assertAlmostEqual(row["avg_volume_20d"], 1052.5)
        self.assertTrue(factors.iloc[:20]["momentum_20d"].isna().all())
        self.assertTrue(factors.iloc[:20]["volatility_20d"].isna().all())
        self.assertTrue(factors.iloc[:60]["momentum_60d"].isna().all())
        self.assertTrue(pd.isna(factors.iloc[18]["avg_volume_20d"]))

    def test_missing_inputs_are_not_filled(self):
        bars = example_bars().drop(columns="Volume")
        bars.iloc[-2, 0] = np.nan
        factors = compute_price_factors(bars)
        self.assertTrue(factors["avg_volume_20d"].isna().all())
        self.assertTrue(pd.isna(factors.iloc[-1]["volatility_20d"]))
        self.assertAlmostEqual(factors.iloc[-1]["momentum_20d"], 162 / 142 - 1)

    def test_duplicate_dates_rejected(self):
        bars = example_bars()
        with self.assertRaisesRegex(ValueError, "unique"):
            compute_price_factors(pd.concat([bars, bars.iloc[-1:]]))

    def test_as_of_excludes_later_bars_and_reports_actual_date(self):
        with patch.object(CNStockUtils, "get_stock_data", return_value=example_bars()) as fetch:
            result = json.loads(get_a_share_factors("000001", "2024-03-24"))
        fetch.assert_called_once_with("000001", "2023-09-26", "2024-03-24", adjust="qfq")
        self.assertEqual(result["data_date"], "2024-03-22")
        self.assertEqual(result["bar_count"], 60)
        self.assertAlmostEqual(result["factors"]["momentum_20d"]["value"], 159 / 139 - 1)
        self.assertIsNone(result["factors"]["momentum_60d"]["value"])

    def test_empty_data_is_explicit(self):
        with patch.object(CNStockUtils, "get_stock_data", return_value=pd.DataFrame()):
            with self.assertRaisesRegex(ValueError, "No daily bars"):
                get_a_share_factors("000001", "2024-03-24")

    def test_bad_inputs_do_not_fetch(self):
        with patch.object(CNStockUtils, "get_stock_data") as fetch:
            for symbol, date in [("1", "2024-03-24"), ("000001", "20240324")]:
                with self.subTest(symbol=symbol, date=date), self.assertRaises(ValueError):
                    get_a_share_factors(symbol, date)
        fetch.assert_not_called()

    def test_agent_executor_can_call_factor_tool(self):
        agent = SingleAssistant(
            "A_Share_Factor_Researcher",
            llm_config={"config_list": [{"model": "unused-in-test", "api_key": "not-a-real-key"}]},
            code_execution_config=False,
        )
        with patch.object(CNStockUtils, "get_stock_data", return_value=example_bars()):
            success, reply = agent.user_proxy.execute_function(
                {
                    "name": "get_a_share_factors",
                    "arguments": json.dumps({"symbol": "000001", "as_of": "2024-03-24"}),
                }
            )
        self.assertTrue(success, reply)
        self.assertEqual(json.loads(reply["content"])["data_date"], "2024-03-22")

    def test_ideator_executor_can_backtest_a_validated_rule(self):
        dates = pd.bdate_range("2024-01-01", periods=63)
        bars = pd.DataFrame(
            {"Open": np.arange(100.0, 163.0), "Close": np.arange(100.0, 163.0),
             "Volume": np.arange(1000.0, 1063.0)},
            index=dates,
        )
        rule = {
            "name": "volume_confirmed_momentum",
            "signal": {"all": [
                {"indicator": "momentum", "window": 20, "operator": ">", "value": 0},
                {"indicator": "average_volume_ratio", "short_window": 5,
                 "long_window": 20, "operator": ">", "value": 1},
            ]},
        }
        start, end = dates[40].date().isoformat(), dates[50].date().isoformat()
        agent = SingleAssistant(
            "A_Share_Factor_Ideator",
            llm_config={"config_list": [{"model": "unused-in-test", "api_key": "not-a-real-key"}]},
            code_execution_config={"use_docker": False},
        )
        self.assertIs(agent.user_proxy._code_execution_config, False)
        self.assertEqual(set(agent.user_proxy.function_map), {"backtest_a_share_strategy"})
        benchmark = pd.Series(np.arange(200.0, 211.0), index=dates[40:51])
        arguments = {
            "symbol": "000001",
            "start": start,
            "end": end,
            "rule_json": json.dumps(rule),
            "cost_bps": 10,
            "slippage_bps": 0,
        }
        with (
            patch.object(CNStockUtils, "get_stock_data", return_value=bars),
            patch("finrobot.functional.momentum_backtest._csi300_open", return_value=benchmark),
        ):
            success, reply = agent.user_proxy.execute_function(
                {"name": "backtest_a_share_strategy", "arguments": json.dumps(arguments)}
            )
        self.assertTrue(success, reply)
        result = json.loads(reply["content"])
        self.assertEqual(result["rule"]["name"], "volume_confirmed_momentum")
        self.assertEqual(result["csi300"]["symbol"], "000300")
        self.assertIn("strategy", result)

    def test_custom_backtest_rejects_unsupported_rule_before_fetching(self):
        from finrobot.functional.momentum_backtest import backtest_a_share_strategy

        with patch.object(CNStockUtils, "get_stock_data") as fetch:
            with self.assertRaisesRegex(ValueError, "Unsupported indicator"):
                backtest_a_share_strategy(
                    "000001", "2024-01-01", "2024-02-01",
                    json.dumps({"signal": {"all": [
                        {"indicator": "arbitrary_python", "operator": ">", "value": 0}
                    ]}}),
                )
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
