"""Check the unchanged momentum rule on new stocks and a later period."""

import argparse
import hashlib
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from finrobot.data_source.baostock_utils import get_stock_data
from finrobot.functional.momentum_backtest import _csi300_open, simulate_momentum

from .validate_momentum import COST_BPS, SYMBOLS, summarize


AS_OF = "2025-12-31"
START, END = "2026-01-01", "2026-06-30"
PER_EXCHANGE = 10


def select_symbols(codes):
    """Take the first ten by SHA-256(code) on each exchange."""
    eligible = {
        code for code in codes
        if re.fullmatch(r"(?:sh\.6\d{5}|sz\.[03]\d{5})", code)
        and code[3:] not in SYMBOLS
    }
    selected = []
    for exchange in ("sh", "sz"):
        market = sorted(
            (code for code in eligible if code.startswith(exchange + ".")),
            key=lambda code: (hashlib.sha256(code.encode("ascii")).hexdigest(), code),
        )
        if len(market) < PER_EXCHANGE:
            raise ValueError(f"Insufficient eligible {exchange} constituents.")
        selected.extend(market[:PER_EXCHANGE])
    return selected


def load_constituents():
    import baostock as bs

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    try:
        response = bs.query_hs300_stocks(date=AS_OF)
        if response.error_code != "0":
            raise RuntimeError(f"BaoStock constituent query failed: {response.error_msg}")
        frame = response.get_data()
        if response.error_code != "0":
            raise RuntimeError(f"BaoStock constituent query failed: {response.error_msg}")
    finally:
        bs.logout()
    if len(frame) != 300 or frame["code"].nunique() != 300:
        raise ValueError("Expected 300 unique CSI 300 constituents.")
    dates = frame["updateDate"].unique()
    if len(dates) != 1 or date.fromisoformat(dates[0]) > date.fromisoformat(AS_OF):
        raise ValueError("Expected a single constituent snapshot dated at or before the cutoff.")
    return str(dates[0]), frame["code"].tolist()


def run_holdout():
    snapshot_date, codes = load_constituents()
    selected = select_symbols(codes)
    print(f"Fixed holdout sample: {', '.join(selected)}", file=sys.stderr, flush=True)
    warmup = (date.fromisoformat(START) - timedelta(days=180)).isoformat()
    index_open = _csi300_open(START, END)
    if index_open.empty or not index_open.index.is_unique:
        raise ValueError("Missing or duplicate CSI 300 opening dates.")

    rows, failures = [], []
    for code in selected:
        print(f"Fetching {code}", file=sys.stderr, flush=True)
        try:
            bars = get_stock_data(code, warmup, END, adjust="qfq").sort_index()
            actual = bars.loc[START:END].index
            missing = index_open.index.difference(actual)
            extra = actual.difference(index_open.index)
            if len(missing) or len(extra):
                raise ValueError(f"Trading dates differ: missing={len(missing)}, extra={len(extra)}")
            result = simulate_momentum(bars, START, END, COST_BPS, index_open)
        except (ValueError, RuntimeError) as exc:
            failures.append({"symbol": code, "error": str(exc)})
            continue
        rows.append({"symbol": code, **result})

    return {
        "constituents_requested_as_of": AS_OF,
        "constituents_snapshot_date": snapshot_date,
        "selection": "Exclude previous symbols; rank full exchange.code by SHA-256; "
                     "take ten per exchange without replacement.",
        "selected": selected,
        "period": {"start": START, "end": END},
        "provider": "BaoStock (qfq)",
        "benchmark_provider": "AkShare stock_zh_index_daily (sh000300)",
        "cost_bps": COST_BPS,
        "overall": summarize(rows) if rows else None,
        "failures": failures,
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_holdout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")
    if report["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
