from httpx import AsyncClient

PASSWORD = "test-password-123"  # noqa: S105


async def register(client: AsyncClient) -> dict[str, str]:
    response = await client.post("/auth/sign_up/email", json={"email": "user@example.com", "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"  # noqa: S105
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
