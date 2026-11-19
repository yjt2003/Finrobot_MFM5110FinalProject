"""Run the existing momentum strategy on a fixed stock-by-year sample."""

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

from finrobot.data_source import CNStockUtils
from finrobot.functional.momentum_backtest import _csi300_open, simulate_momentum


SYMBOLS = ("600519", "600036", "000333", "300750", "601318")
YEARS = (2022, 2023, 2024, 2025)
COST_BPS = 10
BENCHMARKS = ("buy_and_hold", "csi300")


def summarize(rows):
    return {
        "cases": len(rows),
        "positive_strategy_cases": sum(r["strategy"]["total_return"] > 0 for r in rows),
        "mean_return": {
            key: mean(r[key]["total_return"] for r in rows)
            for key in ("strategy", *BENCHMARKS)
        },
        "comparison": {
            key: {
                "higher_return_cases": sum(
                    r["strategy"]["total_return"] - r[key]["total_return"] > 1e-10
                    for r in rows
                ),
                "lower_drawdown_cases": sum(
                    r[key]["max_drawdown"] - r["strategy"]["max_drawdown"] > 1e-10
                    for r in rows
                ),
            }
            for key in BENCHMARKS
        },
    }


def run_validation(provider):
    first = date(YEARS[0], 1, 1)
    last = date(YEARS[-1], 12, 31).isoformat()
    warmup = (first - timedelta(days=180)).isoformat()
    index_open = _csi300_open(first.isoformat(), last)
    rows = []
    for symbol in SYMBOLS:
        bars = CNStockUtils.get_stock_data(symbol, warmup, last, adjust="qfq")
        if bars is None or bars.empty:
            raise ValueError(f"{symbol}: no daily bars returned")
        for year in YEARS:
            start, end = f"{year}-01-01", f"{year}-12-31"
            expected_dates = index_open.loc[start:end].index
            actual_dates = bars.sort_index().loc[start:end].index
            missing_dates = expected_dates.difference(actual_dates)
            extra_dates = actual_dates.difference(expected_dates)
            if expected_dates.empty or len(missing_dates) or len(extra_dates):
                raise ValueError(
                    f"{symbol}/{year}: expected full CSI 300 trading-date coverage; "
                    f"index={len(expected_dates)}, missing={len(missing_dates)}, "
                    f"extra={len(extra_dates)}"
                )
            result = simulate_momentum(bars, start, end, COST_BPS, index_open)
            rows.append({"symbol": symbol, "year": year, **result})

    return {
        "provider": provider,
        "benchmark_provider": "AkShare stock_zh_index_daily (sh000300)",
        "adjustment": "qfq",
        "cost_bps": COST_BPS,
        "design": "20-bar close momentum, next-open trades, long or cash; each year "
                  "starts independently, with prior bars available for signal warm-up. "
                  "Returns and drawdowns are decimals. Means are unweighted across cases.",
        "overall": summarize(rows),
        "by_year": {year: summarize([r for r in rows if r["year"] == year]) for year in YEARS},
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("baostock", "akshare"), default="baostock")
    parser.add_argument("--output", type=Path, help="Write JSON results to this path.")
    args = parser.parse_args()
    os.environ["CN_DATA_PROVIDER"] = args.provider
    report = json.dumps(run_validation(args.provider), indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
