# Модуль для работы с WebSocket HTX

import asyncio
import websockets

async def connect(url):
    async with websockets.connect(url) as websocket:
        await websocket.send('Hello, HTX!')
        response = await websocket.recv()
        print(f'Received: {response}')
