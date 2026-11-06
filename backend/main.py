
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from threading import Lock
import akshare as ak
import pandas as pd

app = FastAPI(title="A股量化行情服务")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

CACHE_SECONDS = 60
FAILURE_COOLDOWN_SECONDS = 300

_cache_lock = Lock()
_last_items = None
_last_updated_at = None
_last_fetch_at = None
_last_error = None


def number(value):
    try:
        if pd.isna(value):
            return 0
        return float(value)
    except (TypeError, ValueError):
        return 0


def text(value):
    if pd.isna(value):
        return ""
    return str(value)


def fetch_items():
    df = ak.stock_zh_a_spot_em()
    items = []

    for _, row in df.iterrows():
        code = text(row.get("代码", ""))
        name = text(row.get("名称", ""))

        if not code:
            continue

        market = (
            "北交所" if code.startswith(("4", "8"))
            else "科创板" if code.startswith("688")
            else "创业板" if code.startswith(("300", "301"))
            else "沪深A股"
        )

        items.append({
            "code": code,
            "name": name,
            "market": market,
            "price": number(row.get("最新价")),
            "change_pct": number(row.get("涨跌幅")),
            "change_amount": number(row.get("涨跌额")),
            "volume": number(row.get("成交量")),
            "amount": number(row.get("成交额")),
            "amplitude": number(row.get("振幅")),
            "high": number(row.get("最高")),
            "low": number(row.get("最低")),
            "open": number(row.get("今开")),
            "prev_close": number(row.get("昨收")),
            "volume_ratio": number(row.get("量比")),
            "turnover_rate": number(row.get("换手率")),
            "market_cap": number(row.get("总市值")),
            "float_market_cap": number(row.get("流通市值")),
        })

    if not items:
        raise RuntimeError("行情接口返回空数据")

    return items


@app.get("/")
def home():
    return {
        "status": "ok",
        "message": "A股量化后台运行正常"
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/stocks")
def get_stocks():
    global _last_items
    global _last_updated_at
    global _last_fetch_at
    global _last_error

    now = datetime.now()

    if _last_items is not None and _last_fetch_at is not None:
        if now - _last_fetch_at < timedelta(seconds=CACHE_SECONDS):
            return {
                "success": True,
                "count": len(_last_items),
                "items": _last_items,
                "updated_at": _last_updated_at,
                "source": "cache",
                "stale": False,
                "warning": "使用60秒缓存，免费行情可能存在延迟"
            }

    with _cache_lock:
        now = datetime.now()

        if _last_items is not None and _last_fetch_at is not None:
            if now - _last_fetch_at < timedelta(seconds=CACHE_SECONDS):
                return {
                    "success": True,
                    "count": len(_last_items),
                    "items": _last_items,
                    "updated_at": _last_updated_at,
                    "source": "cache",
                    "stale": False,
                    "warning": "使用60秒缓存，免费行情可能存在延迟"
                }

        if _last_error and _last_fetch_at is not None:
            if now - _last_fetch_at < timedelta(
                seconds=FAILURE_COOLDOWN_SECONDS
            ):
                if _last_items is not None:
                    return {
                        "success": True,
                        "count": len(_last_items),
                        "items": _last_items,
                        "updated_at": _last_updated_at,
                        "source": "last_success",
                        "stale": True,
                        "warning": "行情源暂时不可用，当前显示最近一次成功数据"
                    }

                return {
                    "success": False,
                    "detail": _last_error,
                    "items": [],
                    "count": 0,
                    "stale": True,
                    "warning": "行情源处于失败冷却期，请稍后再试"
                }

        _last_fetch_at = now

        try:
            items = fetch_items()

            _last_items = items
            _last_updated_at = datetime.now().isoformat()
            _last_error = None

            return {
                "success": True,
                "count": len(items),
                "items": items,
                "updated_at": _last_updated_at,
                "source": "live",
                "stale": False,
                "warning": "免费行情数据可能存在延迟"
            }

        except Exception as e:
            _last_error = str(e)

            if _last_items is not None:
                return {
                    "success": True,
                    "count": len(_last_items),
                    "items": _last_items,
                    "updated_at": _last_updated_at,
                    "source": "last_success",
                    "stale": True,
                    "warning": "行情源暂时不可用，当前显示最近一次成功数据"
                }

            return {
                "success": False,
                "detail": _last_error,
                "items": [],
                "count": 0,
                "stale": True,
                "warning": "行情源暂时不可用，系统将在冷却后重试"
            }

