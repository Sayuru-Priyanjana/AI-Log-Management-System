import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
from app.prometheus.client import PrometheusClient

@pytest.fixture
def prom_client():
    return PrometheusClient(base_url="http://mock:9090")

@pytest.mark.asyncio
async def test_prometheus_query_success(prom_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "status": "success",
        "data": {"resultType": "vector", "result": []}
    }
    
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        result = await prom_client.query("up")
        
        assert "resultType" in result
        mock_get.assert_called_once()

@pytest.mark.asyncio
async def test_prometheus_query_range_success(prom_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "status": "success",
        "data": {"resultType": "matrix", "result": []}
    }
    
    start = datetime(2023, 1, 1, 12, 0, 0)
    end = datetime(2023, 1, 1, 13, 0, 0)
    
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        result = await prom_client.query_range("up", start, end, "60s")
        
        assert "resultType" in result
        mock_get.assert_called_once()

@pytest.mark.asyncio
async def test_prometheus_query_failure(prom_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "status": "error",
        "error": "invalid query"
    }
    
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        with pytest.raises(ValueError, match="Prometheus query failed: invalid query"):
            await prom_client.query("invalid")
