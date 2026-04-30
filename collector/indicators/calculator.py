import httpx
import pandas as pd
import logging

log = logging.getLogger(__name__)

HTX_REST_URL = "https://api.huobi.pro"

async def fetch_history(symbol: str, period: str = "15min", size: int = 100) -> pd.DataFrame:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{HTX_REST_URL}/market/history/kline",
                params={"symbol": symbol, "period": period, "size": size}
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "ok":
                # HTX returns data newest to oldest. We need oldest to newest for indicators.
                klines = data.get("data", [])
                klines.reverse()
                df = pd.DataFrame(klines)
                if not df.empty:
                    df['close'] = pd.to_numeric(df['close'])
                return df
    except Exception as e:
        log.error(f"Error fetching history for {symbol}: {e}")
    return pd.DataFrame()

async def calculate_indicators(symbol: str) -> dict:
    df = await fetch_history(symbol)
    if df.empty or len(df) < 30: # Need enough data for MACD(26)
        return {}
        
    # Calculate RSI (14) natively
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # Calculate MACD (12, 26, 9) natively
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['Signal_Line']
    
    # Get last row
    last_row = df.iloc[-1]
    
    indicators = {}
    if not pd.isna(last_row.get('RSI')):
        indicators["rsi_14"] = float(last_row['RSI'])
    if not pd.isna(last_row.get('MACD')):
        indicators["macd"] = float(last_row['MACD'])
    if not pd.isna(last_row.get('Signal_Line')):
        indicators["macd_signal"] = float(last_row['Signal_Line'])
    if not pd.isna(last_row.get('MACD_Hist')):
        indicators["macd_hist"] = float(last_row['MACD_Hist'])
        
    return indicators
