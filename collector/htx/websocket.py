import asyncio
import websockets
import json
import gzip
import logging
from storage.redis_client import get_redis

log = logging.getLogger(__name__)

HTX_WS_URL = "wss://api.huobi.pro/ws"

async def handle_message(message, redis):
    try:
        data = json.loads(message)
    except Exception as e:
        log.error(f"Failed to parse json: {e}")
        return None

    if "ping" in data:
        return {"pong": data["ping"]}

    if "ch" in data and "tick" in data:
        # data format for market.$symbol.ticker
        ch = data["ch"]
        parts = ch.split(".")
        if len(parts) >= 3 and parts[2] == "ticker":
            symbol = parts[1]
            tick = data["tick"]
            close = float(tick.get("close", 0) or tick.get("lastPrice", 0))
            open_price = float(tick.get("open", 0) or tick.get("openPrice", 0))
            volume = float(tick.get("vol", 0))
            
            if open_price > 0:
                change = ((close - open_price) / open_price) * 100
            else:
                change = 0
                
            ticker_data = {
                "symbol": symbol,
                "price": close,
                "volume": volume,
                "change_pct": change
            }
            # Save to redis
            await redis.set(f"ticker:{symbol}", json.dumps(ticker_data), ex=300)

    return None

async def ws_listen(watchlist=None):
    if not watchlist:
        import os
        watchlist = os.getenv("WATCHLIST", "btcusdt,ethusdt").split(",")
        
    redis = get_redis()
    while True:
        try:
            async with websockets.connect(HTX_WS_URL) as ws:
                log.info("Connected to HTX WebSocket")
                
                # Subscribe to tickers
                for sym in watchlist:
                    sub_msg = json.dumps({
                        "sub": f"market.{sym.lower()}.ticker",
                        "id": f"id_{sym}"
                    })
                    await ws.send(sub_msg)
                
                while True:
                    msg = await ws.recv()
                    # HTX sends gzip compressed data
                    unzipped = gzip.decompress(msg).decode('utf-8')
                    
                    response = await handle_message(unzipped, redis)
                    if response:
                        await ws.send(json.dumps(response))
                        
        except Exception as e:
            log.warning(f"WebSocket disconnected: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)
