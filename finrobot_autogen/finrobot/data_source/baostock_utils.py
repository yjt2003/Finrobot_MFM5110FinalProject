"""BaoStock daily bars in the CNStockUtils schema."""

import re

import pandas as pd


def get_stock_data(symbol, start_date, end_date, adjust="qfq", save_path=None):
    try:
        import baostock as bs
    except ImportError as exc:
        raise ImportError("Install BaoStock with: python -m pip install baostock") from exc

    match = re.fullmatch(r"(?:(sh|sz)[.]?)?([036][0-9]{5})(?:[.](sh|sz))?", symbol.lower())
    if not match:
        raise ValueError("BaoStock daily adapter accepts Shanghai/Shenzhen A-share codes.")
    prefix, code, suffix = match.groups()
    exchange = "sh" if code.startswith("6") else "sz"
    if any(part and part != exchange for part in (prefix, suffix)):
        raise ValueError("Exchange does not match stock code.")
    flags = {"qfq": "2", "hfq": "1", "": "3"}
    if adjust not in flags:
        raise ValueError("adjust must be qfq, hfq, or empty.")
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    try:
        fields = "date,open,high,low,close,volume,amount,tradestatus"
        result = bs.query_history_k_data_plus(
            f"{exchange}.{code}", fields, start_date=start_date, end_date=end_date,
            frequency="d", adjustflag=flags[adjust],
        )
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock query failed: {result.error_msg}")
        bars = result.get_data()
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock query failed: {result.error_msg}")
    finally:
        bs.logout()
    bars = bars.loc[bars["tradestatus"].eq("1")].drop(columns="tradestatus")
    bars = bars.rename(columns=str.capitalize)
    bars["Date"] = pd.to_datetime(bars["Date"], errors="raise")
    bars = bars.set_index("Date").sort_index().apply(pd.to_numeric, errors="coerce")
    bars["Volume"] /= 100
    if save_path:
        bars.to_csv(save_path)
    return bars
