import base64
import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from finrobot.data_source.akshare_utils import AkShareUtils
from finrobot.functional.charting import ReportChartUtils
from finrobot.functional.reportlab import ReportLabUtils


class _FakeAkShare:
    def __init__(self, income, balance, cashflow):
        self.income = income
        self.balance = balance
        self.cashflow = cashflow

    def stock_profit_sheet_by_report_em(self, symbol):
        return self.income

    def stock_balance_sheet_by_report_em(self, symbol):
        return self.balance

    def stock_cash_flow_sheet_by_report_em(self, symbol):
        return self.cashflow


class DataContractTests(unittest.TestCase):
    def test_sec_report_matches_report_year_and_prefers_formal_report(self):
        announcements = pd.DataFrame(
            [
                {
                    "公告标题": "贵州茅台2023年年度报告",
                    "公告链接": "https://example.test/2023.pdf",
                    "公告时间": "2024-04-03",
                },
                {
                    "公告标题": "贵州茅台2024年年度报告摘要",
                    "公告链接": "https://example.test/2024-summary.pdf",
                    "公告时间": "2025-04-03",
                },
                {
                    "公告标题": "贵州茅台2024年年度报告英文版",
                    "公告链接": "https://example.test/2024-en.pdf",
                    "公告时间": "2025-04-03",
                },
                {
                    "公告标题": "贵州茅台2024年年度报告",
                    "公告链接": "https://example.test/2024.pdf",
                    "公告时间": "2025-04-03",
                },
            ]
        )
        with patch.object(AkShareUtils, "get_announcements", return_value=announcements):
            result = AkShareUtils.get_sec_report("600519", "2024")

        self.assertIn("贵州茅台2024年年度报告", result)
        self.assertIn("https://example.test/2024.pdf", result)
        self.assertIn("2025-04-03", result)
        self.assertNotIn("2023年年度报告", result)
        self.assertNotIn("摘要", result)

    def test_financial_metrics_separates_operating_and_total_income(self):
        dates = ["2023-12-31", "2024-12-31", "2025-12-31"]
        income = pd.DataFrame(
            {
                "REPORT_DATE": dates,
                "OPERATE_INCOME": [147.694e9, 170.899e9, 168.838e9],
                "TOTAL_OPERATE_INCOME": [150.560e9, 174.144e9, 172.054e9],
                "OPERATE_COST": [50e9, 55e9, 55e9],
                "PARENT_NETPROFIT": [74.734e9, 86.228e9, 82.320e9],
                "NETPROFIT": [74.734e9, 86.228e9, 82.320e9],
                "BASIC_EPS": [5.94, 6.86, 6.56],
            }
        )
        balance = pd.DataFrame(
            {
                "REPORT_DATE": dates,
                "TOTAL_ASSETS": [200e9, 220e9, 225e9],
                "TOTAL_LIABILITIES": [40e9, 44e9, 45e9],
                "TOTAL_EQUITY": [160e9, 176e9, 180e9],
            }
        )
        cashflow = pd.DataFrame(
            {
                "REPORT_DATE": dates,
                "NETCASH_OPERATE": [80e9, 90e9, 85e9],
                "CONSTRUCT_LONG_ASSET": [10e9, 12e9, 11e9],
            }
        )
        fake_ak = _FakeAkShare(income, balance, cashflow)
        with patch("finrobot.data_source.akshare_utils._require_ak", return_value=fake_ak):
            result = AkShareUtils.get_financial_metrics("600519", years=2, as_of="2025-04-03")

        self.assertEqual(list(result.columns), ["2023", "2024"])
        self.assertEqual(result.loc["Operating Revenue (亿元)", "2024"], 1708.99)
        self.assertEqual(result.loc["Total Operating Income (亿元)", "2024"], 1741.44)
        self.assertAlmostEqual(result.loc["Gross Margin (%)", "2024"], (170.899 - 55) / 170.899 * 100, places=1)
        self.assertNotIn("Revenue (亿元)", result.index)

    def test_index_data_falls_back_to_tencent(self):
        class FakeAk:
            def index_zh_a_hist(self, **kwargs):
                raise ConnectionError("eastmoney unavailable")

            def stock_zh_index_daily_tx(self, symbol):
                return pd.DataFrame(
                    {"date": ["2024-01-02"], "open": [100], "high": [101], "low": [99], "close": [100.5]}
                )

        with patch("finrobot.data_source.akshare_utils._require_ak", return_value=FakeAk()):
            result = AkShareUtils.get_index_data("000300", "2024-01-01", "2024-01-03")

        self.assertEqual(float(result.iloc[0]["Close"]), 100.5)

    def test_stock_info_returns_code_when_optional_endpoint_fails(self):
        class BrokenAk:
            def stock_individual_info_em(self, symbol):
                raise ConnectionError("endpoint unavailable")

        with patch("finrobot.data_source.akshare_utils._require_ak", return_value=BrokenAk()):
            result = AkShareUtils.get_stock_info("600519")

        self.assertEqual(result["shortName"], "600519")
        self.assertEqual(result["symbol"], "600519")

    def test_competitor_metrics_forward_as_of_to_each_symbol(self):
        calls = []
        frame = pd.DataFrame([[1.0]], index=["EPS (元)"], columns=["2024"])

        def fake_metrics(symbol, years=4, as_of=None):
            calls.append((symbol, years, as_of))
            return frame

        with patch.object(AkShareUtils, "get_financial_metrics", side_effect=fake_metrics):
            result = AkShareUtils.get_competitor_financial_metrics(
                "600519", ["000858", "000568"], years=1, as_of="2025-04-03"
            )

        self.assertEqual(set(result), {"600519", "000858", "000568"})
        self.assertEqual(
            calls,
            [
                ("600519", 1, "2025-04-03"),
                ("000858", 1, "2025-04-03"),
                ("000568", 1, "2025-04-03"),
            ],
        )


class ChartAndPdfTests(unittest.TestCase):
    def setUp(self):
        self.bars = pd.DataFrame(
            {"Close": [100.0, 101.0, 99.0, 102.0, 103.0]},
            index=pd.bdate_range("2024-12-25", periods=5),
        )

    def test_charts_use_symbol_when_stock_name_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("finrobot.functional.charting.CNStockUtils.get_stock_data", return_value=self.bars),
                patch("finrobot.functional.charting.CNStockUtils.get_index_data", return_value=self.bars),
                patch("finrobot.functional.charting.CNStockUtils.get_stock_info", return_value={"symbol": "600519"}),
                patch("finrobot.functional.charting.CNStockUtils.get_financial_metrics", return_value=pd.DataFrame(
                    [[5.0, 5.5, 6.0]], index=["EPS (元)"], columns=["2022", "2023", "2024"]
                )),
            ):
                share_path = os.path.join(temp_dir, "share.png")
                pe_path = os.path.join(temp_dir, "pe.png")
                ReportChartUtils.get_share_performance("600519", "2024-12-31", share_path)
                ReportChartUtils.get_pe_eps_performance("600519", "2024-12-31", years=2, save_path=pe_path)
                self.assertGreater(os.path.getsize(share_path), 0)
                self.assertGreater(os.path.getsize(pe_path), 0)

    def test_pdf_returns_real_path_and_fails_for_missing_image(self):
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        metrics = pd.DataFrame(
            [[1476.94, 1708.99, 1688.38], [5.94, 6.86, 6.56]],
            index=["Operating Revenue (亿元)", "EPS (元)"],
            columns=["2023", "2024", "2025"],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            share = os.path.join(temp_dir, "share.png")
            pe = os.path.join(temp_dir, "pe.png")
            with open(share, "wb") as stream:
                stream.write(png)
            with open(pe, "wb") as stream:
                stream.write(png)

            with (
                patch("finrobot.functional.reportlab.is_cn_symbol", return_value=True),
                patch("finrobot.functional.reportlab.CNStockUtils.get_stock_info", return_value={"symbol": "600519"}),
                patch("finrobot.functional.reportlab.CNStockUtils.get_financial_metrics", return_value=metrics),
                patch(
                    "finrobot.functional.reportlab.ReportAnalysisUtils.get_key_data",
                    return_value={
                        "评级": "买入",
                        "目标价（元，研报推算）": "1800",
                        "EPS": "6.86",
                    },
                ),
            ):
                pdf_path = ReportLabUtils.build_annual_report(
                    "600519", temp_dir, "经营结果", "市场地位", "业务概览", "风险", "同业", share, pe, "2025-04-03"
                )
                self.assertTrue(os.path.isfile(pdf_path))
                self.assertGreater(os.path.getsize(pdf_path), 0)
                from pypdf import PdfReader
                pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf_path).pages)
                self.assertIn("2024", pdf_text)
                self.assertNotIn("1800", pdf_text)
                self.assertNotIn("1688.38", pdf_text)
                with self.assertRaises(FileNotFoundError):
                    ReportLabUtils.build_annual_report(
                        "600519", temp_dir, "经营结果", "市场地位", "业务概览", "风险", "同业", share, "missing.png", "2025-04-03"
                    )


if __name__ == "__main__":
    unittest.main()
