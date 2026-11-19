"""Tushare Pro 数据适配层（可选，需要 TUSHARE_TOKEN）。

与 ``AkShareUtils`` 互为补充：

- ``AkShareUtils``：免费、无需 key，默认推荐使用。
- ``TushareUtils``：需要 token，接口结构化程度高、可稳定复现，适合生产/回测。

通常不需要直接调用本类：``CNStockUtils`` 会根据 ``CN_DATA_PROVIDER``
（``auto`` / ``akshare`` / ``tushare``）自动选择数据源，Tushare 积分不足时
回退 AkShare。Tushare 的接口按积分开放（行情约 120 分，财务报表约 2000 分）。

配置方式（token 在 https://tushare.pro/register 注册后获取）::

    # finrobot_autogen/config_api_keys
    {"TUSHARE_TOKEN": "你的 token", "CN_DATA_PROVIDER": "auto", ...}

    from finrobot.utils import register_keys_from_json
    register_keys_from_json("config_api_keys")

    from finrobot.data_source import TushareUtils
    TushareUtils.get_stock_data("600519", "2024-01-01", "2024-12-31")

Tushare 代码格式为 ``600519.SH`` / ``000001.SZ``，本适配器也接受
``600519``、``SH600519``、``sh.600519`` 等写法。
"""

import os
import re
from datetime import datetime
from functools import wraps
from typing import Annotated, List, Optional

import pandas as pd

from ..utils import decorate_all_methods

ts_pro = None
_ts_token = None

_PERMISSION_HINT = (
    "Tushare 接口权限不足：当前 token 的积分/权限无法访问该接口。"
    "请到 https://tushare.pro/user/token 查看积分（行情约 120 分、财务报表约 2000 分），"
    "或改用 CN_DATA_PROVIDER=akshare / auto 回退到免费的 AkShare。"
)


def is_permission_error(exc: BaseException) -> bool:
    """判断异常是否为 Tushare 的积分/权限不足。"""
    text = str(exc)
    lowered = text.lower()
    return any(
        key in text for key in ("权限", "没有接口", "积分")
    ) or "permission" in lowered or "privilege" in lowered


def init_tushare_api(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        global ts_pro, _ts_token
        token = os.environ.get("TUSHARE_TOKEN")
        if not token:
            raise RuntimeError(
                "未配置 TUSHARE_TOKEN，无法使用 Tushare Pro。"
                "请在 config_api_keys 中填入 token，或使用 CN_DATA_PROVIDER=akshare。"
            )
        if ts_pro is None or _ts_token != token:
            import tushare as ts

            ts_pro = ts.pro_api(token)
            _ts_token = token
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if is_permission_error(exc):
                raise PermissionError(_PERMISSION_HINT) from exc
            raise

    return wrapper


def _ts_symbol(symbol: str) -> str:
    """把各种代码写法统一成 Tushare 的 ``600519.SH`` 格式。"""
    raw = str(symbol).strip().upper().replace(" ", "")
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", raw):
        return raw

    digits = re.sub(r"\D", "", raw)
    if len(digits) != 6:
        raise ValueError(
            f"无法识别 A 股代码: {symbol!r}，应为 6 位数字，如 600519 / 000001"
        )
    if digits.startswith(("6", "9")):
        return f"{digits}.SH"
    if digits.startswith(("0", "2", "3")):
        return f"{digits}.SZ"
    return f"{digits}.BJ"


def _date8(value: str) -> str:
    return pd.to_datetime(value).strftime("%Y%m%d")


@decorate_all_methods(init_tushare_api)
class TushareUtils:
    """A 股数据适配器（Tushare Pro）。"""

    # ------------------------------ 行情 ------------------------------

    def get_stock_data(
        symbol: Annotated[str, "A 股代码，如 600519 / 600519.SH"],
        start_date: Annotated[str, "开始日期，YYYY-mm-dd"],
        end_date: Annotated[str, "结束日期，YYYY-mm-dd"],
        adjust: Annotated[str, "复权方式: qfq 前复权 / hfq 后复权 / 空字符串 不复权"] = "qfq",
        save_path: Optional[str] = None,
    ) -> pd.DataFrame:
        """日线行情。默认前复权；成交量单位：手，成交额单位：元。"""
        ts_code = _ts_symbol(symbol)
        start, end = _date8(start_date), _date8(end_date)
        if adjust:
            import tushare as ts

            df = ts.pro_bar(
                ts_code=ts_code,
                api=ts_pro,
                start_date=start,
                end_date=end,
                adj=adjust,
            )
        else:
            df = ts_pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        if df is None or df.empty:
            return df

        df = df.rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "vol": "Volume",
                "amount": "Amount",
                "pct_chg": "PctChange",
                "change": "Change",
            }
        )
        # 与 AkShareUtils 对齐：成交额由千元换算成元，列名统一为 Open/High/Low/Close。
        if "Amount" in df.columns:
            df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce") * 1000
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date").set_index("trade_date")
        df.index.name = "Date"
        df = df.drop(columns=["ts_code"], errors="ignore")

        if save_path:
            df.to_csv(save_path)
            print(f"Stock data saved to {save_path}")
        return df

    def get_stock_info(
        symbol: Annotated[str, "A 股代码，如 600519"],
    ) -> Optional[dict]:
        """个股基础信息（名称、行业、地区、上市日期、交易所等）。"""
        code = _ts_symbol(symbol)
        try:
            df = ts_pro.stock_basic(
                ts_code=code,
                fields="ts_code,symbol,name,area,industry,market,exchange,list_date,list_status",
            )
        except Exception:
            return {"symbol": code[:6], "shortName": code[:6], "longName": code[:6], "currency": "CNY"}
        if df is None or df.empty:
            return {"symbol": code[:6], "shortName": code[:6], "longName": code[:6], "currency": "CNY"}
        info = df.iloc[0].to_dict()
        # 与 AkShareUtils / YFinanceUtils 的字段名对齐，方便上层工具复用。
        info.setdefault("symbol", info.get("symbol") or _ts_symbol(symbol)[:6])
        info.setdefault("shortName", info.get("name", info["symbol"]))
        info.setdefault("longName", info.get("name", info["symbol"]))
        info.setdefault("industry", info.get("industry", ""))
        info.setdefault("currency", "CNY")
        return info

    # ------------------------------ 财务报表 ------------------------------

    def get_income_stmt(
        symbol: Annotated[str, "A 股代码，如 600519"],
        start_date: Annotated[Optional[str], "报告期开始日期 YYYY-mm-dd"] = None,
        end_date: Annotated[Optional[str], "报告期结束日期 YYYY-mm-dd"] = None,
        save_path: Optional[str] = None,
    ) -> pd.DataFrame:
        """利润表（按报告期）。"""
        kwargs = {"ts_code": _ts_symbol(symbol)}
        if start_date:
            kwargs["start_date"] = _date8(start_date)
        if end_date:
            kwargs["end_date"] = _date8(end_date)
        df = ts_pro.income(**kwargs)
        if save_path and df is not None:
            df.to_csv(save_path, index=False)
        return df

    def get_balance_sheet(
        symbol: Annotated[str, "A 股代码，如 600519"],
        start_date: Annotated[Optional[str], "报告期开始日期 YYYY-mm-dd"] = None,
        end_date: Annotated[Optional[str], "报告期结束日期 YYYY-mm-dd"] = None,
        save_path: Optional[str] = None,
    ) -> pd.DataFrame:
        """资产负债表（按报告期）。"""
        kwargs = {"ts_code": _ts_symbol(symbol)}
        if start_date:
            kwargs["start_date"] = _date8(start_date)
        if end_date:
            kwargs["end_date"] = _date8(end_date)
        df = ts_pro.balancesheet(**kwargs)
        if save_path and df is not None:
            df.to_csv(save_path, index=False)
        return df

    def get_cash_flow(
        symbol: Annotated[str, "A 股代码，如 600519"],
        start_date: Annotated[Optional[str], "报告期开始日期 YYYY-mm-dd"] = None,
        end_date: Annotated[Optional[str], "报告期结束日期 YYYY-mm-dd"] = None,
        save_path: Optional[str] = None,
    ) -> pd.DataFrame:
        """现金流量表（按报告期）。"""
        kwargs = {"ts_code": _ts_symbol(symbol)}
        if start_date:
            kwargs["start_date"] = _date8(start_date)
        if end_date:
            kwargs["end_date"] = _date8(end_date)
        df = ts_pro.cashflow(**kwargs)
        if save_path and df is not None:
            df.to_csv(save_path, index=False)
        return df

    # ------------------------------ 财务指标/估值 ------------------------------

    def get_financial_metrics(
        ticker_symbol: Annotated[str, "A 股代码，如 600519"],
        years: Annotated[int, "回溯年数，默认 4"] = 4,
    ) -> pd.DataFrame:
        """财务指标（ROE、毛利率、净利率、资产负债率等），按报告期升序。"""
        end = pd.Timestamp.now()
        start = end - pd.DateOffset(years=years + 1)
        df = ts_pro.fina_indicator(
            ts_code=_ts_symbol(ticker_symbol),
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return df
        return df.sort_values("end_date").reset_index(drop=True)

    def get_stock_dividends(
        symbol: Annotated[str, "A 股代码，如 600519"],
        save_path: Optional[str] = None,
    ) -> pd.DataFrame:
        """分红送转方案。"""
        df = ts_pro.dividend(ts_code=_ts_symbol(symbol))
        if save_path and df is not None:
            df.to_csv(save_path, index=False)
        return df

    def get_historical_market_cap(
        ticker_symbol: Annotated[str, "A 股代码，如 600519"],
        date: Annotated[Optional[str], "指定日期 YYYY-mm-dd；为空则返回历史序列"] = None,
        start_date: Annotated[Optional[str], "历史序列开始日期"] = None,
        end_date: Annotated[Optional[str], "历史序列结束日期"] = None,
    ) -> Optional[float | pd.DataFrame]:
        """历史总市值（单位：亿元）。指定 date 时返回该日最接近的市值。

        与 ``AkShareUtils.get_historical_market_cap`` 保持同一口径：
        DataFrame 含 ``date`` / ``value``（总市值，亿元），另附流通市值与估值列。
        """
        end_date = end_date or datetime.now().strftime("%Y-%m-%d")
        if date is not None:
            target = pd.to_datetime(date)
            start_date = (target - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
            end_date = (target + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        else:
            start_date = start_date or (
                pd.to_datetime(end_date) - pd.DateOffset(years=10)
            ).strftime("%Y-%m-%d")

        df = ts_pro.daily_basic(
            ts_code=_ts_symbol(ticker_symbol),
            start_date=_date8(start_date),
            end_date=_date8(end_date),
            fields="ts_code,trade_date,total_mv,circ_mv,pe,pe_ttm,pb,total_share,float_share",
        )
        if df is None or df.empty:
            return None

        df = df.copy()
        df["date"] = pd.to_datetime(df["trade_date"])
        # Tushare 市值单位是万元，换算为亿元。
        df["value"] = pd.to_numeric(df["total_mv"], errors="coerce") / 1e4
        df["circ_value"] = pd.to_numeric(df["circ_mv"], errors="coerce") / 1e4
        df = df.sort_values("date").reset_index(drop=True)

        if date is not None:
            idx = (df["date"] - pd.to_datetime(date)).abs().idxmin()
            return float(df.loc[idx, "value"])

        cols = [
            "date",
            "value",
            "circ_value",
            "pe",
            "pe_ttm",
            "pb",
            "total_share",
            "float_share",
        ]
        return df[[c for c in cols if c in df.columns]]

    def get_competitor_financial_metrics(
        ticker_symbol: Annotated[str, "A 股代码，如 600519"],
        competitors: Annotated[List[str], "可比公司代码列表"],
        years: Annotated[int, "回溯年数，默认 4"] = 4,
    ) -> dict:
        """公司及可比公司的财务指标字典。"""
        symbols = [ticker_symbol] + competitors
        result = {}
        for sym in symbols:
            try:
                result[sym] = TushareUtils.get_financial_metrics(sym, years)
            except Exception as exc:
                print(f"[TushareUtils] {sym} 财务指标获取失败: {exc}")
                result[sym] = None
        return result


if __name__ == "__main__":
    from finrobot.utils import register_keys_from_json

    register_keys_from_json("config_api_keys")
    print(TushareUtils.get_stock_data("600519", "2024-01-01", "2024-12-31").tail())
    print(TushareUtils.get_financial_metrics("600519", years=4).head())
