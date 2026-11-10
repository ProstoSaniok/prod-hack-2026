from httpx import AsyncClient


async def test_healthcheck(http_client: AsyncClient) -> None:
    response = await http_client.get("/healthcheck")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_metrics(http_client: AsyncClient) -> None:
    await http_client.get("/healthcheck")
    response = await http_client.get("/metrics")
    assert response.status_code == 200
    assert 'handler="/healthcheck"' in response.text
