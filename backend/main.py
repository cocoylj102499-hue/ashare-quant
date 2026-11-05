

from fastapi import FastAPI

from fastapi.middleware.cors import CORSMiddleware

from datetime import datetime

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

    try:

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

                "float_market_cap": number(row.get("流通市值"))

            })

        return {

            "success": True,

            "count": len(items),

            "items": items,

            "updated_at": datetime.now().isoformat(),

            "warning": "免费行情数据可能存在延迟或限流"

        }

    except Exception as e:

        return {

            "success": False,

            "detail": str(e),

            "items": [],

            "count": 0

        }
