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

# # Config for service
API_PROXY_PORT = int(os.getenv("HEARTBEAT_PORT", 8000))

app = FastAPI()

# Config for external APIs
WEATHERAPI_URL = "https://api.weatherapi.com/v1/current.json"
WEATHERSTACK_URL = "https://api.weatherstack.com/current"
WEATHERAPI_KEY = os.getenv("WEATHERAPI_KEY", "5c39c0204ebc850224352252705")
WEATHERSTACK_KEY = os.getenv("WEATHERSTACK_KEY", "eb2d62553e9e79c66c201b0c3850c338")

# Active API Tracker
active_api = "weatherapi"

# Circuit breaker simulation
failure_counts = {"weatherapi": 0, "weatherstack": 0}
MAX_FAILURES = 3
RESET_TIMEOUT = 300  # in seconds
last_failure_time = {"weatherapi": None, "weatherstack": None}

# WebSocket clients
websocket_clients = []

async def notify_heartbeat(event: str):
    """Send heartbeat event to all connected WebSocket clients"""
    disconnected_clients = []

    for ws in websocket_clients:
        try:
            await ws.send_text(event)
        except Exception as e:
            # Mark client for removal
            disconnected_clients.append(ws)

    # Remove disconnected clients
    for ws in disconnected_clients:
        if ws in websocket_clients:
            websocket_clients.remove(ws)

def check_circuit_breaker(api_name: str):
    """Check if circuit breaker should be open (block requests) for the given API"""
    # If failure count exceeds threshold, check if reset timeout has passed
    if failure_counts[api_name] >= MAX_FAILURES:
        if last_failure_time[api_name]:
            elapsed = (datetime.datetime.now() - last_failure_time[api_name]).total_seconds()
            if elapsed < RESET_TIMEOUT:
                return False  # Circuit is open, don't allow requests
            else:
                # Reset after timeout
                failure_counts[api_name] = 0
                last_failure_time[api_name] = None
    return True  # Circuit is closed, allow requests

async def fetch_from_weatherapi(city: str):
    if not check_circuit_breaker("weatherapi"):
        raise HTTPException(status_code=503, detail="WeatherAPI circuit breaker open")

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(WEATHERAPI_URL, params={"q": city, "key": WEATHERAPI_KEY})
            if response.status_code == 429:
                raise httpx.HTTPStatusError("Rate limit", request=response.request, response=response)
            elif response.status_code in [401, 403]:
                # Authentication error - increment failure count instead of just adding to inactive_apis
                failure_counts["weatherapi"] += 1
                last_failure_time["weatherapi"] = datetime.datetime.now()
                raise httpx.HTTPStatusError(f"Authentication error: {response.status_code}", 
                                           request=response.request, response=response)
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                # API returned an error in the response body
                failure_counts["weatherapi"] += 1
                last_failure_time["weatherapi"] = datetime.datetime.now()
                raise Exception(f"API Error: {data['error']}")
            # Reset failure count on success
            failure_counts["weatherapi"] = 0
            return {"source": "weatherapi", "city": data["location"]["name"], "temperature": data["current"]["temp_c"], "condition": data["current"]["condition"]["text"]}
    except httpx.HTTPStatusError as e:
        failure_counts["weatherapi"] += 1
        last_failure_time["weatherapi"] = datetime.datetime.now()
        raise e

async def fetch_from_weatherstack(city: str):
    if not check_circuit_breaker("weatherstack"):
        raise HTTPException(status_code=503, detail="Weatherstack circuit breaker open")

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(WEATHERSTACK_URL, params={"access_key": WEATHERSTACK_KEY, "query": city})
            if response.status_code == 429:
                raise httpx.HTTPStatusError("Rate limit", request=response.request, response=response)
            elif response.status_code in [401, 403]:
                # Authentication error - increment failure count instead of just adding to inactive_apis
                failure_counts["weatherstack"] += 1
                last_failure_time["weatherstack"] = datetime.datetime.now()
                raise httpx.HTTPStatusError(f"Authentication error: {response.status_code}", 
                                           request=response.request, response=response)
            response.raise_for_status()
            data = response.json()
            # Check for error in response
            if "error" in data:
                # API returned an error in the response body
                failure_counts["weatherstack"] += 1
                last_failure_time["weatherstack"] = datetime.datetime.now()
                raise Exception(f"API Error: {data['error']}")
            # Reset failure count on success
            failure_counts["weatherstack"] = 0
            return {"source": "weatherstack", "city": data["location"]["name"], "temperature": data["current"]["temperature"], "condition": data["current"]["weather_descriptions"][0]}
    except httpx.HTTPStatusError as e:
        failure_counts["weatherstack"] += 1
        last_failure_time["weatherstack"] = datetime.datetime.now()
        raise e

@app.get("/weather")
async def get_weather(city: str):
    global active_api

    # Try primary API (weatherapi)
    try:
        # Only skip if circuit breaker is open
        if check_circuit_breaker("weatherapi"):
            data = await fetch_from_weatherapi(city)
            active_api = "weatherapi"
            await notify_heartbeat(f"Primary API active: WeatherAPI for {city}")
            return data
        else:
            await notify_heartbeat(f"WeatherAPI circuit breaker open, skipping")
    except (httpx.HTTPStatusError, httpx.RequestError, HTTPException) as e:
        await notify_heartbeat(f"WeatherAPI failed: {str(e)}")
        # Continue to fallback API
    except Exception as e:
        await notify_heartbeat(f"Unexpected error with WeatherAPI: {str(e)}")
        # Continue to fallback API

    # Try fallback API (weatherstack)
    try:
        # Only skip if circuit breaker is open
        if check_circuit_breaker("weatherstack"):
            data = await fetch_from_weatherstack(city)
            active_api = "weatherstack"
            await notify_heartbeat(f"Fallback API active: Weatherstack for {city}")
            return data
        else:
            await notify_heartbeat(f"Weatherstack circuit breaker open, skipping")
    except (httpx.HTTPStatusError, httpx.RequestError, HTTPException) as e:
        await notify_heartbeat(f"Weatherstack failed: {str(e)}")
    except Exception as e:
        await notify_heartbeat(f"Unexpected error with Weatherstack: {str(e)}")

    # All APIs failed
    await notify_heartbeat("All APIs failed. Returning stub response.")
    return JSONResponse(status_code=503, content={
        "city": city,
        "temperature": None,
        "condition": "Unavailable",
        "message": "Both weather services are down. This is a stubbed response.",
        "timestamp": datetime.datetime.utcnow().isoformat()
    })

@app.get("/health")
def health():
    # Check circuit breaker status
    circuit_status = {
        "weatherapi": "open" if failure_counts["weatherapi"] >= MAX_FAILURES else "closed",
        "weatherstack": "open" if failure_counts["weatherstack"] >= MAX_FAILURES else "closed"
    }

    # Determine inactive APIs based on circuit breaker status
    inactive_api_list = []
    if circuit_status["weatherapi"] == "open":
        inactive_api_list.append("weatherapi")
    if circuit_status["weatherstack"] == "open":
        inactive_api_list.append("weatherstack")

    return {
        "status": "ok",
        "active_api": active_api,
        "inactive_apis": inactive_api_list,
        "circuit_breaker": circuit_status,
        "failure_counts": failure_counts,
        "time": datetime.datetime.utcnow().isoformat()
    }

@app.websocket("/ws/heartbeat")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_clients.append(websocket)

    # Send initial connection message
    await websocket.send_text(f"Connected to weather service heartbeat at {datetime.datetime.utcnow().isoformat()}")

    try:
        while True:
            await websocket.receive_text()  # Just to keep connection alive
    except Exception as e:
        # Handle disconnection
        if websocket in websocket_clients:
            websocket_clients.remove(websocket)
    finally:
        # Ensure client is removed even if an unexpected error occurs
        if websocket in websocket_clients:
            websocket_clients.remove(websocket)

if __name__ == "__main__":
    logging.info("Starting HeartBeat Service with FastAPI...")
    uvicorn.run("main:app", host="0.0.0.0", port=API_PROXY_PORT)
