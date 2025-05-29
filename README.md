# Weather API Proxy with Heartbeat Monitoring

This project consists of two microservices that work together to provide a resilient weather data API with real-time monitoring:

1. **API Proxy Service**: Acts as a proxy for external weather APIs with circuit breaker pattern implementation
2. **HeartBeat Service**: Monitors the health of the API Proxy Service and provides real-time status updates

## Project Overview

The system is designed to demonstrate resilient microservice architecture with the following features:

- **API Failover**: Automatically switches between multiple weather data providers when one fails
- **Circuit Breaker Pattern**: Prevents cascading failures by temporarily disabling failing APIs
- **Real-time Monitoring**: WebSocket connections provide real-time updates on service health
- **Containerized Deployment**: Docker and Docker Compose for easy deployment
- **Health Checks**: Continuous monitoring of service health with alerts

## Prerequisites

- [Docker](https://www.docker.com/get-started) and [Docker Compose](https://docs.docker.com/compose/install/)
- Weather API keys:
  - [WeatherAPI](https://www.weatherapi.com/) (free tier available)
  - [Weatherstack](https://weatherstack.com/) (free tier available)

## Environment Setup

1. Create `.env` files for both services:

### API Proxy Service/.env

```
WEATHERAPI_KEY=your_weatherapi_key
WEATHERSTACK_KEY=your_weatherstack_key
HEARTBEAT_PORT=8000
```

### HeartBeat Service/.env

```
API_PROXY_HOST=api-proxy
API_PROXY_PORT=8000
HEALTH_CHECK_INTERVAL=5
WS_RECONNECT_INTERVAL=5
HTTP_TIMEOUT=5
WS_TIMEOUT=30
MAX_BACKOFF=60
HEARTBEAT_PORT=8001
```

## Installation and Running

1. Clone this repository:
   ```
   git clone <repository-url>
   cd <repository-directory>
   ```

2. Set up environment variables as described above

3. Build and start the services using Docker Compose:
   ```
   docker-compose up -d
   ```

4. To stop the services:
   ```
   docker-compose down
   ```

## Service Descriptions

### API Proxy Service

The API Proxy Service (port 8000) provides the following endpoints:

- **GET /weather**: Returns weather data for London from either WeatherAPI or Weatherstack
- **GET /health**: Returns the health status of the service and its connections to external APIs
- **WebSocket /ws/heartbeat**: Provides real-time updates about the service status

The service implements a circuit breaker pattern that will temporarily disable an API after multiple failures, automatically switching to the backup API. After a timeout period, it will attempt to use the primary API again.

### HeartBeat Service

The HeartBeat Service (port 8001) monitors the API Proxy Service and provides:

- Regular health checks of the API Proxy Service
- WebSocket connection to receive real-time updates from the API Proxy
- Exponential backoff for reconnection attempts when connections fail
- Alerts when the API Proxy Service is unresponsive
- **WebSocket /ws**: Connect to receive monitoring updates

## Development

### Running Locally (Without Docker)

To run the services locally for development:

1. Install Python 3.10 or later
2. Set up virtual environments for each service:

```bash
# For API Proxy Service
cd "API Proxy Service"
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py

# For HeartBeat Service (in a separate terminal)
cd "HeartBeat Service"
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Testing the Services

1. Check the API Proxy Service:
   ```
   curl http://localhost:8000/weather
   ```

2. Check the health endpoint:
   ```
   curl http://localhost:8000/health
   ```

3. Connect to the WebSocket endpoints using a WebSocket client like [websocat](https://github.com/vi/websocat) or browser-based tools.

## Architecture

The system uses a microservices architecture:

- **API Proxy Service**: Handles external API communication with failover capabilities
- **HeartBeat Service**: Monitors the API Proxy Service and provides alerts
- Both services communicate via HTTP and WebSockets
- Docker Compose orchestrates the containers and networking

## Troubleshooting

- If services fail to start, check the Docker logs:
  ```
  docker-compose logs
  ```

- Ensure the environment variables are correctly set in the .env files

- Verify that your API keys for the weather services are valid

## License

[MIT License](LICENSE)