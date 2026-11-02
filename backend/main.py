from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import akshare as ak
import pandas as pd

app = FastAPI(title='A股量化行情服务')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)

@app.get('/')
def home():
    return {'status': 'ok', 'message': 'A股量化后台运行正常'}

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.get('/api/stocks')
def get_stocks():
    try:
        df = ak.stock_zh_a_spot_em()
        df = df.replace({float('nan'): None})
        return {
            'success': True,
            'count': len(df),
            'data': df.to_dict(orient='records')
        }
    except Exception as e:
        return {'success': False, 'error': str(e), 'data': []}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
