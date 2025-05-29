# Code file begins here
# FastAPI-based resilient API proxy with fallback logic and heartbeat integration

from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.responses import JSONResponse
import httpx
import datetime
import os
import logging
import uvicorn
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Load environment variables
load_dotenv()

API_PROXY_PORT = int(os.getenv("HEARTBEAT_PORT", 8000))

app = FastAPI()

# External API configuration
WEATHERAPI_URL = "https://api.weatherapi.com/v1/current.json"
WEATHERSTACK_URL = "https://api.weatherstack.com/current"
WEATHERAPI_KEY = os.getenv("WEATHERAPI_KEY", "default-weatherapi-key")
WEATHERSTACK_KEY = os.getenv("WEATHERSTACK_KEY", "default-weatherstack-key")

active_api = "weatherapi"
failure_counts = {"weatherapi": 0, "weatherstack": 0}
MAX_FAILURES = 3
RESET_TIMEOUT = 300
last_failure_time = {"weatherapi": None, "weatherstack": None}

websocket_clients = []

async def notify_heartbeat(event: str):
    disconnected_clients = []
    for ws in websocket_clients:
        try:
            await ws.send_text(event)
        except:
            disconnected_clients.append(ws)
    for ws in disconnected_clients:
        if ws in websocket_clients:
            websocket_clients.remove(ws)

def check_circuit_breaker(api_name: str):
    if failure_counts[api_name] >= MAX_FAILURES:
        if last_failure_time[api_name]:
            elapsed = (datetime.datetime.now() - last_failure_time[api_name]).total_seconds()
            if elapsed < RESET_TIMEOUT:
                return False
            failure_counts[api_name] = 0
            last_failure_time[api_name] = None
    return True

async def fetch_from_weatherapi(city: str):
    if not check_circuit_breaker("weatherapi"):
        raise HTTPException(status_code=503, detail="WeatherAPI circuit breaker open")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(WEATHERAPI_URL, params={"q": city, "key": WEATHERAPI_KEY})
            if response.status_code == 429:
                raise httpx.HTTPStatusError("Rate limit", request=response.request, response=response)
            elif response.status_code in [401, 403]:
                failure_counts["weatherapi"] += 1
                last_failure_time["weatherapi"] = datetime.datetime.now()
                raise httpx.HTTPStatusError("Auth error", request=response.request, response=response)
            data = response.json()
            if "error" in data:
                failure_counts["weatherapi"] += 1
                last_failure_time["weatherapi"] = datetime.datetime.now()
                raise Exception("API Error")
            failure_counts["weatherapi"] = 0
            return {"source": "weatherapi", "city": data["location"]["name"], "temperature": data["current"]["temp_c"], "condition": data["current"]["condition"]["text"]}
    except:
        failure_counts["weatherapi"] += 1
        last_failure_time["weatherapi"] = datetime.datetime.now()
        raise

async def fetch_from_weatherstack(city: str):
    if not check_circuit_breaker("weatherstack"):
        raise HTTPException(status_code=503, detail="Weatherstack circuit breaker open")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(WEATHERSTACK_URL, params={"access_key": WEATHERSTACK_KEY, "query": city})
            if response.status_code == 429:
                raise httpx.HTTPStatusError("Rate limit", request=response.request, response=response)
            elif response.status_code in [401, 403]:
                failure_counts["weatherstack"] += 1
                last_failure_time["weatherstack"] = datetime.datetime.now()
                raise httpx.HTTPStatusError("Auth error", request=response.request, response=response)
            data = response.json()
            if "error" in data:
                failure_counts["weatherstack"] += 1
                last_failure_time["weatherstack"] = datetime.datetime.now()
                raise Exception("API Error")
            failure_counts["weatherstack"] = 0
            return {"source": "weatherstack", "city": data["location"]["name"], "temperature": data["current"]["temperature"], "condition": data["current"]["weather_descriptions"][0]}
    except:
        failure_counts["weatherstack"] += 1
        last_failure_time["weatherstack"] = datetime.datetime.now()
        raise

@app.get("/weather")
async def get_weather(city: str):
    global active_api
    try:
        if check_circuit_breaker("weatherapi"):
            data = await fetch_from_weatherapi(city)
            active_api = "weatherapi"
            await notify_heartbeat(f"Primary API active: WeatherAPI for {city}")
            return data
        else:
            await notify_heartbeat("WeatherAPI circuit breaker open, skipping")
    except:
        await notify_heartbeat("WeatherAPI failed")

    try:
        if check_circuit_breaker("weatherstack"):
            data = await fetch_from_weatherstack(city)
            active_api = "weatherstack"
            await notify_heartbeat(f"Fallback API active: Weatherstack for {city}")
            return data
        else:
            await notify_heartbeat("Weatherstack circuit breaker open, skipping")
    except:
        await notify_heartbeat("Weatherstack failed")

    await notify_heartbeat("All APIs failed. Returning stub response.")
    return JSONResponse(status_code=503, content={
        "city": city,
        "temperature": None,
        "condition": "Unavailable",
        "message": "Both services are down. Stubbed response.",
        "timestamp": datetime.datetime.utcnow().isoformat()
    })

@app.get("/health")
def health():
    circuit_status = {
        "weatherapi": "open" if failure_counts["weatherapi"] >= MAX_FAILURES else "closed",
        "weatherstack": "open" if failure_counts["weatherstack"] >= MAX_FAILURES else "closed"
    }
    inactive_apis = [api for api, status in circuit_status.items() if status == "open"]
    return {
        "status": "ok",
        "active_api": active_api,
        "inactive_apis": inactive_apis,
        "circuit_breaker": circuit_status,
        "failure_counts": failure_counts,
        "time": datetime.datetime.utcnow().isoformat()
    }

@app.websocket("/ws/heartbeat")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_clients.append(websocket)
    await websocket.send_text(f"Connected at {datetime.datetime.utcnow().isoformat()}")
    try:
        while True:
            await websocket.receive_text()
    except:
        pass
    finally:
        if websocket in websocket_clients:
            websocket_clients.remove(websocket)

if __name__ == "__main__":
    logging.info("Starting Resilient API Proxy...")
    uvicorn.run("main:app", host="0.0.0.0", port=API_PROXY_PORT, reload=True)
