import asyncio
import httpx
import websockets
import logging
import os
import signal
from fastapi import FastAPI, WebSocket
import uvicorn
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Logging config
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# Configuration
API_PROXY_HOST = os.getenv("API_PROXY_HOST", "localhost")
API_PROXY_PORT = os.getenv("API_PROXY_PORT", "8000")
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "5"))
WS_RECONNECT_INTERVAL = int(os.getenv("WS_RECONNECT_INTERVAL", "5"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "5"))
WS_TIMEOUT = int(os.getenv("WS_TIMEOUT", "30"))
MAX_BACKOFF = int(os.getenv("MAX_BACKOFF", "60"))
HEARTBEAT_PORT = int(os.getenv("HEARTBEAT_PORT", 8001))

API_PROXY_HEALTH_URL = f"http://{API_PROXY_HOST}:{API_PROXY_PORT}/health"
API_PROXY_WS_URL = f"ws://{API_PROXY_HOST}:{API_PROXY_PORT}/ws/heartbeat"

# Create FastAPI app
app = FastAPI(title="HeartBeat Service")

# Shutdown flag
should_shutdown = asyncio.Event()

async def ping_health_endpoint():
    backoff = 1
    while not should_shutdown.is_set():
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                response = await client.get(API_PROXY_HEALTH_URL)
                if response.status_code == 200:
                    logging.info("API Proxy is healthy. Info: %s", response.json())
                    backoff = 1  # Reset backoff
                else:
                    logging.warning("Unexpected status from API Proxy: %s", response.status_code)
                    backoff = min(backoff * 2, MAX_BACKOFF)
        except httpx.TimeoutException:
            logging.error("Timeout while pinging API Proxy")
            backoff = min(backoff * 2, MAX_BACKOFF)
        except httpx.ConnectError:
            logging.error("Connection error to API Proxy")
            logging.warning("[ALERT] API Proxy is unresponsive. Notifying admin (mock Slack)")
            backoff = min(backoff * 2, MAX_BACKOFF)
        except Exception as e:
            logging.error("Health check failed: %s", str(e))
            logging.warning("[ALERT] API Proxy is unresponsive. Notifying admin (mock Slack)")
            backoff = min(backoff * 2, MAX_BACKOFF)

        try:
            await asyncio.wait_for(should_shutdown.wait(), timeout=backoff)
        except asyncio.TimeoutError:

            pass

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Message received: {data}")
    except Exception as e:
        logging.error(f"WebSocket error: {str(e)}")
    finally:
        await websocket.close()

async def listen_to_websocket():
    backoff = 1
    while not should_shutdown.is_set():
        try:
            async with websockets.connect(
                API_PROXY_WS_URL,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=WS_TIMEOUT
            ) as websocket:
                logging.info("Connected to API Proxy WebSocket.")
                backoff = 1  # Reset backoff

                while not should_shutdown.is_set():
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=WS_TIMEOUT)
                        logging.info("Real-time update from API Proxy: %s", message)
                    except asyncio.TimeoutError:
                        logging.debug("No message received. Still alive...")
                        continue
                    except websockets.exceptions.ConnectionClosed:
                        logging.warning("WebSocket connection closed")
                        break
        except websockets.exceptions.WebSocketException as e:
            logging.warning("WebSocket connection error: %s", str(e))
            backoff = min(backoff * 2, MAX_BACKOFF)
        except Exception as e:
            logging.warning("Unexpected WebSocket error: %s", str(e))
            backoff = min(backoff * 2, MAX_BACKOFF)

        if not should_shutdown.is_set():
            logging.info("Reconnecting to WebSocket in %s seconds...", backoff)
            try:
                await asyncio.wait_for(should_shutdown.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass

def handle_shutdown(sig=None, frame=None):
    logging.info("Shutdown signal received, initiating cleanup...")
    asyncio.create_task(shutdown())

async def shutdown():
    should_shutdown.set()
    await asyncio.sleep(2)
    logging.info("Shutdown complete")

@app.on_event("startup")
async def startup_event():
    logging.info("Starting HeartBeat Service...")
    asyncio.create_task(ping_health_endpoint())
    asyncio.create_task(listen_to_websocket())

@app.on_event("shutdown")
async def shutdown_event():
    logging.info("Shutting down HeartBeat Service...")
    await shutdown()

async def main():
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_shutdown)
    try:
        await asyncio.gather(
            ping_health_endpoint(),
            listen_to_websocket()
        )
    except asyncio.CancelledError:
        logging.info("Tasks cancelled")
    finally:
        await shutdown()

if __name__ == "__main__":
    logging.info("Starting HeartBeat Service with FastAPI...")
    uvicorn.run("main:app", host="0.0.0.0", port=HEARTBEAT_PORT)
