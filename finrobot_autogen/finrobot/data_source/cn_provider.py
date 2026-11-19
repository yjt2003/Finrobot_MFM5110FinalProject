"""A 股数据源路由层：在 AkShare 与 Tushare Pro 之间按配置自动选择。

``CNStockUtils`` 暴露与 ``AkShareUtils`` 完全相同的类方法，因此 agent 工具、
图表和报告代码只需要改一次 import，就能整体切换数据源。

配置::

    # config_api_keys（register_keys_from_json 会把所有键写入环境变量）
    {
      "TUSHARE_TOKEN": "...",
      "CN_DATA_PROVIDER": "auto"
    }

``CN_DATA_PROVIDER`` 可选值：

- ``auto``（默认）：优先 Tushare Pro；接口失败或积分不足时自动回退 AkShare，
  并在当前进程内停用 Tushare，避免反复请求失败接口。
- ``akshare``：始终使用免费的 AkShare。
- ``tushare``：只用 Tushare Pro，失败直接报错（便于排查权限/积分问题）。

Tushare 优先的方法：行情、基础信息、分红、三表、历史市值。
财务指标（``get_financial_metrics`` 等）、公告、新闻、研报、机构预期仍由
AkShare 提供——这类数据被上层图表/报告直接消费，统一在 AkShare 里计算可以
保证单位与口径一致；Tushare 对应接口也需要更高积分。
"""

import inspect
import os
from functools import update_wrapper
from typing import Optional

from .akshare_utils import AkShareUtils
from . import baostock_utils
from .tushare_utils import TushareUtils

PROVIDER_ENV = "CN_DATA_PROVIDER"
PROVIDER_VALUES = ("auto", "akshare", "tushare", "baostock")

# 这些接口 Tushare 能提供且已与 AkShare 的输出口径对齐。
_TUSHARE_METHODS = frozenset(
    {
        "get_stock_data",
        "get_stock_info",
        "get_stock_dividends",
        "get_income_stmt",
        "get_balance_sheet",
        "get_cash_flow",
        "get_historical_market_cap",
    }
)

# 这些方法的工具描述里不能写死单一数据源（原始 docstring 提到东财字段名）。
_DOC_OVERRIDES = {
    "get_stock_data": "获取 A 股日线行情（默认前复权）。返回以 Date 为索引、包含 Open/High/Low/Close/Volume/Amount 等列的 DataFrame。",
    "get_stock_info": "返回个股基础信息（名称、行业、总市值、股本等），并补充 shortName/currency 等兼容字段。",
    "get_stock_dividends": "返回历史分红送配明细。",
    "get_income_stmt": "返回按报告期的利润表；AkShare 与 Tushare 的字段命名不同。",
    "get_balance_sheet": "返回按报告期的资产负债表；AkShare 与 Tushare 的字段命名不同。",
    "get_cash_flow": "返回按报告期的现金流量表；AkShare 与 Tushare 的字段命名不同。",
    "get_historical_market_cap": "返回历史总市值（单位：亿元）；指定 date 时返回该日最接近的市值。",
}

_ts_disabled_reason: Optional[str] = None
_invalid_provider_warned = False


def _provider() -> str:
    global _invalid_provider_warned
    value = os.environ.get(PROVIDER_ENV, "auto").strip().lower()
    if value not in PROVIDER_VALUES:
        if not _invalid_provider_warned:
            print(
                f"[CNStockUtils] 未识别的 {PROVIDER_ENV}={value!r}，"
                "可选 auto / akshare / tushare / baostock，已按 auto 处理。"
            )
            _invalid_provider_warned = True
        return "auto"
    return value


def describe_provider() -> str:
    """返回当前数据源状态，供启动脚本打印。"""
    provider = _provider()
    has_token = bool(os.environ.get("TUSHARE_TOKEN"))

    if provider == "baostock":
        return "A股数据源：BaoStock（日线）；其他数据仍用 AkShare"
    if provider == "akshare":
        return "A股数据源：AkShare（免费）"
    if provider == "tushare":
        if has_token:
            return "A股数据源：Tushare Pro（严格模式，失败直接报错）"
        return "A股数据源：Tushare Pro（未检测到 TUSHARE_TOKEN，调用会失败）"
    if not has_token:
        return "A股数据源：auto（未配置 TUSHARE_TOKEN，使用 AkShare）"
    if _ts_disabled_reason:
        return f"A股数据源：auto（Tushare 不可用，使用 AkShare：{_ts_disabled_reason}）"
    return "A股数据源：auto（优先 Tushare Pro，失败自动回退 AkShare）"


def _brief(exc: BaseException, limit: int = 120) -> str:
    text = " ".join(str(exc).split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _disable_tushare(reason: str) -> None:
    global _ts_disabled_reason
    if _ts_disabled_reason is None:
        _ts_disabled_reason = reason
        print(f"[CNStockUtils] Tushare 已停用，后续调用直接使用 AkShare：{reason}")


def _is_empty(result) -> bool:
    if result is None:
        return True
    if isinstance(result, dict):
        return not result
    empty = getattr(result, "empty", None)
    return bool(empty) if empty is not None else False


def _call_tushare(name: str, args: tuple, kwargs: dict):
    """按 AkShare 的签名绑定参数，再过滤出 Tushare 能接受的参数。"""
    ak_sig = inspect.signature(getattr(AkShareUtils, name))
    ts_sig = inspect.signature(getattr(TushareUtils, name))
    bound = ak_sig.bind(*args, **kwargs)
    bound.apply_defaults()
    call_kwargs = {
        key: value
        for key, value in bound.arguments.items()
        if key in ts_sig.parameters
    }
    return getattr(TushareUtils, name)(**call_kwargs)


def _dispatch(name: str, args: tuple, kwargs: dict):
    provider = _provider()
    ak_func = getattr(AkShareUtils, name)

    if provider == "baostock":
        if name == "get_stock_data":
            return baostock_utils.get_stock_data(*args, **kwargs)
        return ak_func(*args, **kwargs)

    if provider == "akshare":
        return ak_func(*args, **kwargs)

    if (
        name not in _TUSHARE_METHODS
        or not os.environ.get("TUSHARE_TOKEN")
        or _ts_disabled_reason is not None
    ):
        if provider == "tushare" and name in _TUSHARE_METHODS:
            raise RuntimeError(
                "CN_DATA_PROVIDER=tushare，但 Tushare 不可用："
                + (_ts_disabled_reason or "未配置 TUSHARE_TOKEN")
                + "。可改用 CN_DATA_PROVIDER=auto 或 akshare。"
            )
        return ak_func(*args, **kwargs)

    try:
        result = _call_tushare(name, args, kwargs)
    except Exception as exc:
        if provider == "tushare":
            raise
        if isinstance(exc, (PermissionError, ImportError)) or "TUSHARE_TOKEN" in str(exc):
            _disable_tushare(_brief(exc))
        else:
            print(
                f"[CNStockUtils] Tushare {name} 调用失败，本次回退 AkShare：{_brief(exc)}"
            )
        return ak_func(*args, **kwargs)

    if provider == "auto" and _is_empty(result):
        print(f"[CNStockUtils] Tushare {name} 返回空数据，回退 AkShare。")
        return ak_func(*args, **kwargs)
    return result


def _make_method(name: str):
    base = getattr(AkShareUtils, name)

    def method(*args, **kwargs):
        return _dispatch(name, args, kwargs)

    # 保留 AkShare 的签名/类型注解，AutoGen 依赖它生成工具 schema。
    update_wrapper(method, base)
    doc = _DOC_OVERRIDES.get(name) or inspect.getdoc(base) or f"A 股数据接口 {name}"
    method.__doc__ = (
        doc
        + "\n\n数据源由 CN_DATA_PROVIDER 在 AkShare 与 Tushare Pro 之间自动路由；"
        "Tushare 权限不足时自动回退 AkShare。"
    )
    return staticmethod(method)


class CNStockUtils:
    """A 股数据适配器（AkShare / Tushare Pro 自动路由）。"""


for _name in dir(AkShareUtils):
    _attr = getattr(AkShareUtils, _name)
    if _name.startswith("_") or not callable(_attr):
        continue
    setattr(CNStockUtils, _name, _make_method(_name))

del _name, _attr
