"""Signal-driven A-share backtesting with a small, validated rule language."""

import json
import re
from datetime import date, timedelta
from typing import Annotated, Any, Mapping

import numpy as np
import pandas as pd

from .factors import CNStockUtils


DEFAULT_MOMENTUM_RULE = {
    "name": "positive_20d_momentum",
    "signal": {
        "all": [
            {"indicator": "momentum", "window": 20, "operator": ">", "value": 0},
        ]
    },
}
_OPERATORS = {">", ">=", "<", "<=", "=="}
_INDICATORS = {"momentum", "average_volume_ratio", "moving_average_cross"}
_FIELDS = {"close": "Close", "volume": "Volume"}
_MAX_WINDOW = 252
_MAX_CONDITIONS = 12


def _prepare_bars(bars: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(bars, pd.DataFrame):
        raise ValueError("Daily bars must be a pandas DataFrame.")
    if "Open" not in bars or "Close" not in bars:
        raise ValueError("Daily bars must include Open and Close columns.")
    dates = pd.DatetimeIndex(pd.to_datetime(bars.index, errors="raise")).normalize()
    if dates.hasnans or dates.has_duplicates:
        raise ValueError("Daily bar dates must be nonmissing and unique.")
    return bars.copy().set_axis(dates.rename("Date")).sort_index()


def _positive_window(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_WINDOW:
        raise ValueError(f"{name} must be an integer between 1 and {_MAX_WINDOW}.")
    return value


def _validate_condition(condition: Mapping[str, Any]) -> int:
    if not isinstance(condition, Mapping):
        raise ValueError("Each signal condition must be an object.")
    if "all" in condition or "any" in condition:
        keys = [key for key in ("all", "any") if key in condition]
        if len(keys) != 1 or not isinstance(condition[keys[0]], list) or not condition[keys[0]]:
            raise ValueError("A logical signal must contain a nonempty all or any list.")
        if len(condition[keys[0]]) > _MAX_CONDITIONS:
            raise ValueError(f"A signal may contain at most {_MAX_CONDITIONS} conditions.")
        if set(condition) != {keys[0]}:
            raise ValueError("Logical signal objects may contain only all or any.")
        windows = [_validate_condition(item) for item in condition[keys[0]]]
        return max(windows)
    indicator = condition.get("indicator")
    if indicator not in _INDICATORS:
        raise ValueError(f"Unsupported indicator: {indicator!r}.")
    allowed = {
        "momentum": {"indicator", "window", "operator", "value"},
        "average_volume_ratio": {"indicator", "short_window", "long_window", "operator", "value"},
        "moving_average_cross": {
            "indicator", "field", "short_window", "long_window", "operator", "value"
        },
    }[indicator]
    if not set(condition).issubset(allowed) or not {"indicator", "operator"}.issubset(condition):
        raise ValueError(f"Invalid fields for {indicator} condition.")
    operator = condition.get("operator")
    if operator not in _OPERATORS:
        raise ValueError(f"Unsupported operator: {operator!r}.")
    value = condition.get("value", 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
        raise ValueError("Signal condition value must be a finite number.")
    if indicator == "momentum":
        return _positive_window(condition.get("window"), "window")
    if indicator == "average_volume_ratio":
        short = _positive_window(condition.get("short_window"), "short_window")
        long = _positive_window(condition.get("long_window"), "long_window")
        if short > long:
            raise ValueError("short_window must not exceed long_window.")
        return long
    field = condition.get("field", "close")
    if field not in _FIELDS:
        raise ValueError("moving_average_cross field must be close or volume.")
    short = _positive_window(condition.get("short_window"), "short_window")
    long = _positive_window(condition.get("long_window"), "long_window")
    if short > long:
        raise ValueError("short_window must not exceed long_window.")
    return long


def validate_strategy_rule(rule: Mapping[str, Any]) -> dict:
    """Validate and copy the supported strategy DSL before it reaches the engine."""
    if not isinstance(rule, Mapping):
        raise ValueError("Strategy rule must be a JSON object.")
    if not set(rule).issubset({"name", "signal", "execution"}):
        raise ValueError("Strategy rule contains unsupported top-level fields.")
    signal = rule.get("signal")
    if not isinstance(signal, Mapping):
        raise ValueError("Strategy rule must contain a signal object.")
    if set(signal) not in ({"all"}, {"any"}) or not isinstance(signal[next(iter(signal))], list):
        raise ValueError("The top-level signal must contain all or any.")
    _validate_condition(signal)
    if "name" in rule and (not isinstance(rule["name"], str) or not rule["name"].strip()):
        raise ValueError("Strategy name must be a nonempty string.")
    if "execution" in rule and rule["execution"] != "next_open":
        raise ValueError("Only next_open execution is supported.")
    normalized = json.loads(json.dumps(dict(rule), ensure_ascii=False))
    normalized["name"] = normalized.get("name", "custom_strategy")
    normalized["execution"] = "next_open"
    return normalized


def _clean_series(bars: pd.DataFrame, field: str) -> pd.Series:
    if field == "volume" and "Volume" not in bars:
        raise ValueError("This strategy requires the Volume column.")
    values = pd.to_numeric(bars[_FIELDS[field]], errors="coerce")
    if field == "volume":
        return values.where(np.isfinite(values) & (values >= 0))
    return values.where(np.isfinite(values) & (values > 0))


def _indicator_values(bars: pd.DataFrame, condition: Mapping[str, Any]) -> pd.Series:
    indicator = condition["indicator"]
    if indicator == "momentum":
        close = _clean_series(bars, "close")
        window = condition["window"]
        return close.div(close.shift(window)).sub(1).replace([np.inf, -np.inf], np.nan)
    if indicator == "average_volume_ratio":
        volume = _clean_series(bars, "volume")
        short = volume.rolling(condition["short_window"]).mean()
        long = volume.rolling(condition["long_window"]).mean()
        return short.div(long).replace([np.inf, -np.inf], np.nan)
    field = condition.get("field", "close")
    values = _clean_series(bars, field)
    short = values.rolling(condition["short_window"]).mean()
    long = values.rolling(condition["long_window"]).mean()
    return short.sub(long)


def _compare(values: pd.Series, operator: str, threshold: Any) -> tuple[pd.Series, pd.Series]:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("Signal condition value must be numeric.")
    valid = values.notna()
    if operator == ">":
        result = values > threshold
    elif operator == ">=":
        result = values >= threshold
    elif operator == "<":
        result = values < threshold
    elif operator == "<=":
        result = values <= threshold
    else:
        result = values == threshold
    return result.fillna(False), valid


def _condition_signal(bars: pd.DataFrame, condition: Mapping[str, Any]) -> tuple[pd.Series, pd.Series]:
    if "all" in condition or "any" in condition:
        key = "all" if "all" in condition else "any"
        parts = [_condition_signal(bars, item) for item in condition[key]]
        values = pd.concat([part[0] for part in parts], axis=1)
        valid = pd.concat([part[1] for part in parts], axis=1).all(axis=1)
        result = values.all(axis=1) if key == "all" else values.any(axis=1)
        return result.where(valid, False), valid
    values = _indicator_values(bars, condition)
    return _compare(values, condition["operator"], condition.get("value", 0))


def build_strategy_signals(bars: pd.DataFrame, rule: Mapping[str, Any]) -> pd.Series:
    """Build close-time target positions from a validated strategy rule."""
    bars = _prepare_bars(bars)
    rule = validate_strategy_rule(rule)
    signal, valid = _condition_signal(bars, rule["signal"])
    # Keep unavailable warm-up values explicit so the executor cannot silently trade them as cash.
    return signal.where(valid).astype("boolean")


def build_momentum_signals(bars: pd.DataFrame, window: int = 20, threshold: float = 0) -> pd.Series:
    rule = {
        "name": f"positive_{window}d_momentum",
        "signal": {"all": [{"indicator": "momentum", "window": window,
                              "operator": ">", "value": threshold}]},
    }
    return build_strategy_signals(bars, rule)


def _metrics(positions, interval_returns, cost_bps, slippage_bps):
    positions = np.asarray(positions, dtype=int)
    trades = np.abs(np.diff(np.r_[0, positions]))
    trade_cost = (cost_bps + slippage_bps) / 10000
    equity = np.r_[1.0, np.cumprod(
        (1 - trades * trade_cost) * np.where(positions, interval_returns, 1.0)
    )]
    return {
        "total_return": float(equity[-1] - 1),
        "max_drawdown": float((1 - equity / np.maximum.accumulate(equity)).max()),
        "trades": int(trades.sum()),
    }


def simulate_signals(
    bars: pd.DataFrame,
    target_position: pd.Series,
    start: str,
    end: str,
    cost_bps: float = 10,
    slippage_bps: float = 0,
    benchmark_open: pd.Series | None = None,
):
    """Execute close-time target positions at the next observed open."""
    if (
        isinstance(cost_bps, bool)
        or isinstance(slippage_bps, bool)
        or not np.isfinite(cost_bps)
        or not np.isfinite(slippage_bps)
        or cost_bps < 0
        or slippage_bps < 0
        or cost_bps + slippage_bps >= 10000
    ):
        raise ValueError("cost_bps and slippage_bps must be nonnegative with a combined rate below 10000.")
    bars = _prepare_bars(bars).loc[:end]
    signal = pd.Series(target_position).copy()
    signal.index = pd.DatetimeIndex(pd.to_datetime(signal.index, errors="raise")).normalize()
    if signal.index.has_duplicates:
        raise ValueError("Strategy signal dates must be unique.")
    signal = signal.sort_index().reindex(bars.index)
    if not signal.dropna().isin([True, False, 0, 1]).all():
        raise ValueError("Strategy signals must be boolean or 0/1.")
    prices = pd.to_numeric(bars.loc[start:end, "Open"], errors="coerce")
    if len(prices) < 2 or not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("At least two valid opening prices are required.")
    position = signal.shift(1).loc[start:end]
    if len(position) != len(prices) or position.isna().any():
        raise ValueError("Missing prior-close signals; check rule warm-up history and inputs.")
    position = position.iloc[:-1].astype(int).to_numpy()
    returns = prices.shift(-1).div(prices).iloc[:-1].to_numpy()
    result = {
        "first_open": prices.index[0].date().isoformat(),
        "last_open": prices.index[-1].date().isoformat(),
        "intervals": len(returns),
        "cost_bps": cost_bps,
        "slippage_bps": slippage_bps,
        "strategy": _metrics(position, returns, cost_bps, slippage_bps),
        "buy_and_hold": _metrics(np.ones(len(returns)), returns, cost_bps, slippage_bps),
    }
    if benchmark_open is not None:
        index_prices = pd.to_numeric(benchmark_open.reindex(prices.index), errors="coerce")
        if not np.isfinite(index_prices).all() or (index_prices <= 0).any():
            raise ValueError("CSI 300 opening prices are missing or invalid on backtest dates.")
        index_returns = index_prices.shift(-1).div(index_prices).iloc[:-1].to_numpy()
        index_metrics = _metrics(np.ones(len(returns)), index_returns, 0, 0)
        index_metrics.pop("trades")
        result["csi300"] = {"symbol": "000300", **index_metrics}
    return result


def simulate_momentum(bars, start, end, cost_bps=10, benchmark_open=None):
    """Backward-compatible wrapper for the original positive 20-day rule."""
    signals = build_momentum_signals(bars, 20, 0)
    return simulate_signals(bars, signals, start, end, cost_bps, 0, benchmark_open)


def _csi300_open(start, end):
    import akshare as ak

    index = ak.stock_zh_index_daily(symbol="sh000300")
    dates = pd.to_datetime(index["date"])
    return index.set_index(dates)["open"].sort_index().loc[start:end]


def _load_backtest_inputs(symbol: str, start: str, end: str, rule: Mapping[str, Any]):
    if not re.fullmatch(r"[0-9]{6}", symbol):
        raise ValueError("Use a six-digit A-share code.")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if start != first.isoformat() or end != last.isoformat() or first >= last:
        raise ValueError("Use YYYY-MM-DD with start earlier than end.")
    rule = validate_strategy_rule(rule)
    lookback = _validate_condition(rule["signal"])
    bars = CNStockUtils.get_stock_data(
        symbol, (first - timedelta(days=max(180, lookback * 3))).isoformat(), end, adjust="qfq"
    )
    if bars is None or bars.empty:
        raise ValueError("No daily bars returned.")
    return first, last, bars


def backtest_a_share_strategy(
    symbol: Annotated[str, "Six-digit A-share code"],
    start: Annotated[str, "First eligible opening date, YYYY-MM-DD"],
    end: Annotated[str, "Last eligible opening date, YYYY-MM-DD"],
    rule_json: Annotated[str, "JSON rule with supported indicators and next_open execution"],
    cost_bps: Annotated[float, "Cost per position change in basis points"] = 10,
    slippage_bps: Annotated[float, "Slippage per position change in basis points"] = 0,
) -> str:
    """Backtest a validated strategy rule against A-share daily bars."""
    try:
        rule = json.loads(rule_json) if isinstance(rule_json, str) else rule_json
    except json.JSONDecodeError as exc:
        raise ValueError("rule_json must be valid JSON.") from exc
    rule = validate_strategy_rule(rule)
    _, _, bars = _load_backtest_inputs(symbol, start, end, rule)
    result = simulate_signals(
        bars,
        build_strategy_signals(bars, rule),
        start,
        end,
        cost_bps,
        slippage_bps,
        _csi300_open(start, end),
    )
    return json.dumps({
        "symbol": symbol,
        "rule": rule,
        "method": "Validated close-time signals executed at the next observed open. "
                  "Both portfolios are marked at the final open without liquidation; "
                  "cost_bps and slippage_bps apply on position changes.",
        **result,
    }, allow_nan=False)


def backtest_a_share_momentum(
    symbol: Annotated[str, "Six-digit A-share code"],
    start: Annotated[str, "First eligible opening date, YYYY-MM-DD"],
    end: Annotated[str, "Last eligible opening date, YYYY-MM-DD"],
    cost_bps: Annotated[float, "Cost per position change in basis points"] = 10,
) -> str:
    """Compare the original fixed 20-bar positive-momentum strategy."""
    rule = DEFAULT_MOMENTUM_RULE
    _, _, bars = _load_backtest_inputs(symbol, start, end, rule)
    result = simulate_signals(
        bars,
        build_strategy_signals(bars, rule),
        start,
        end,
        cost_bps,
        0,
        _csi300_open(start, end),
    )
    return json.dumps({
        "symbol": symbol,
        "method": "Positive 20-bar close momentum: long at next observed open; otherwise cash. "
                  "Both portfolios marked at final open without liquidation; no slippage or "
                  "limit-up/limit-down execution checks. CSI 300 is a price-index benchmark "
                  "with no transaction costs, sampled on the same opening dates. "
                  "Returns and drawdowns are decimals.",
        **result,
    }, allow_nan=False)
