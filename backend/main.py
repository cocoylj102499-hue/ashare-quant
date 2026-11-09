
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
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
MIN_MARKET_ROWS = 4000
BATCH_SIZE = 80
WORKERS = 6
HTTP_TIMEOUT = 12

_cache_lock = Lock()
_last_items = None
_last_updated_at = None
_last_fetch_at = None
_last_error = None
_last_source = None


def number(value):
    try:
        if value is None or pd.isna(value):
            return 0.0
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def txt(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def get_market(code):
    if code.startswith(("4", "8")):
        return "北交所"
    if code.startswith("688"):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    return "沪深A股"


def normalize_item(row):
    code = txt(row.get("代码"))
    if not code:
        return None

    return {
        "code": code,
        "name": txt(row.get("名称")),
        "market": get_market(code),
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
    }


def fetch_eastmoney():
    df = ak.stock_zh_a_spot_em()
    if df is None or df.empty:
        raise RuntimeError("东方财富返回空数据")

    items = []
    for _, row in df.iterrows():
        item = normalize_item(row)
        if item:
            items.append(item)

    if len(items) < MIN_MARKET_ROWS:
        raise RuntimeError(
            f"东方财富返回数量不足：{len(items)}"
        )

    return items


def tencent_symbol(code):
    if code.startswith(("4", "8")):
        return "bj" + code
    if code.startswith(("6", "9")):
        return "sh" + code
    return "sz" + code


def fetch_tencent_batch(batch):
    symbols = [tencent_symbol(code) for code, name in batch]
    url = "https://qt.gtimg.cn/q=" + ",".join(symbols)

    response = requests.get(
        url,
        timeout=HTTP_TIMEOUT,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/"
        }
    )
    response.raise_for_status()
    response.encoding = "gbk"

    result = {}

    for line in response.text.split(";"):
        line = line.strip()
        if '="' not in line:
            continue

        raw = line.split('="', 1)[1].rsplit('"', 1)[0]
        fields = raw.split("~")

        if len(fields) < 44:
            continue

        code = txt(fields[2]).zfill(6)
        name = txt(fields[1])

        if not code or not name:
            continue

        result[code] = {
            "code": code,
            "name": name,
            "market": get_market(code),
            "price": number(fields[3]),
            "change_pct": number(fields[32]),
            "change_amount": number(fields[31]),
            "volume": number(fields[36]),
            "amount": number(fields[37]) * 10000,
            "amplitude": number(fields[43]),
            "high": number(fields[33]),
            "low": number(fields[34]),
            "open": number(fields[5]),
            "prev_close": number(fields[4]),
            "volume_ratio": 0,
            "turnover_rate": number(fields[38]),
            "market_cap": number(fields[45]) * 100000000
                if len(fields) > 45 else 0,
            "float_market_cap": number(fields[44]) * 100000000
                if len(fields) > 44 else 0,
        }

    return result


def get_stock_universe():
    df = ak.stock_info_a_code_name()

    if df is None or df.empty:
        raise RuntimeError("沪深京股票代码目录获取失败")

    code_col = "code" if "code" in df.columns else "证券代码"
    name_col = "name" if "name" in df.columns else "证券简称"

    if code_col not in df.columns or name_col not in df.columns:
        raise RuntimeError("股票代码目录字段不匹配")

    universe = {}
    for _, row in df.iterrows():
        code = txt(row[code_col]).zfill(6)
        name = txt(row[name_col])
        if code.isdigit() and len(code) == 6:
            universe[code] = name

    if len(universe) < MIN_MARKET_ROWS:
        raise RuntimeError(
            f"股票代码目录数量不足：{len(universe)}"
        )

    return list(universe.items())


def fetch_tencent():
    stocks = get_stock_universe()

    batches = [
        stocks[i:i + BATCH_SIZE]
        for i in range(0, len(stocks), BATCH_SIZE)
    ]

    merged = {}
    errors = []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [
            pool.submit(fetch_tencent_batch, batch)
            for batch in batches
        ]

        for future in as_completed(futures):
            try:
                merged.update(future.result())
            except Exception as exc:
                errors.append(str(exc))

    if len(merged) < MIN_MARKET_ROWS:
        detail = errors[0] if errors else "腾讯行情返回数量不足"
        raise RuntimeError(
            f"腾讯只返回{len(merged)}只，"
            f"目录有{len(stocks)}只；{detail}"
        )

    return list(merged.values())


def fetch_items():
    try:
        return fetch_eastmoney(), "东方财富"
    except Exception as east_error:
        try:
            return fetch_tencent(), "腾讯备用"
        except Exception as tencent_error:
            raise RuntimeError(
                f"东方财富失败：{east_error}；"
                f"腾讯失败：{tencent_error}"
            )


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
    global _last_source

    now = datetime.now()

    if _last_items is not None and _last_fetch_at is not None:
        if now - _last_fetch_at < timedelta(seconds=CACHE_SECONDS):
            return {
                "success": True,
                "count": len(_last_items),
                "items": _last_items,
                "updated_at": _last_updated_at,
                "source": _last_source,
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
                    "source": _last_source,
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
                        "source": _last_source,
                        "stale": True,
                        "warning": "行情源暂时不可用，显示最近成功数据"
                    }

                return {
                    "success": False,
                    "detail": _last_error,
                    "items": [],
                    "count": 0,
                    "stale": True,
                    "warning": "行情源处于失败冷却期"
                }

        _last_fetch_at = now

        try:
            items, source = fetch_items()

            _last_items = items
            _last_source = source
            _last_updated_at = datetime.now().isoformat()
            _last_error = None

            return {
                "success": True,
                "count": len(items),
                "items": items,
                "updated_at": _last_updated_at,
                "source": source,
                "stale": False,
                "warning": "免费行情可能延迟，请以券商交易端为准"
            }

        except Exception as exc:
            _last_error = str(exc)

            if _last_items is not None:
                return {
                    "success": True,
                    "count": len(_last_items),
                    "items": _last_items,
                    "updated_at": _last_updated_at,
                    "source": _last_source,
                    "stale": True,
                    "warning": "行情源暂时不可用，显示最近成功数据"
                }

            return {
                "success": False,
                "detail": _last_error,
                "items": [],
                "count": 0,
                "stale": True,
                "warning": "行情源暂时不可用，请稍后重试"
            }
