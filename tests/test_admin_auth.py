"""The data APIs must not be readable from the open internet.

Before this, GET /api/v1/family returned the whole family roster and
GET /api/v1/conversations returned the full text of Cordia's private messages
to anyone who asked — and POST/PATCH/DELETE on those routers accepted writes.
"""
import pytest

SECRET = "test-admin-secret"

PROTECTED = [
    "/api/v1/family",
    "/api/v1/conversations",
    "/api/v1/trips",
    "/api/v1/leases",
    "/health/config",
    "/health/data",
]

PUBLIC = ["/health", "/consent", "/privacy", "/terms"]


@pytest.fixture
def admin_secret(mocker):
    mocker.patch("app.config.settings.admin_api_secret", SECRET)
    return SECRET


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PROTECTED)
async def test_protected_paths_reject_missing_secret(client, admin_secret, path):
    assert (await client.get(path)).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PROTECTED)
async def test_protected_paths_reject_wrong_secret(client, admin_secret, path):
    assert (await client.get(path, headers={"X-Admin-Secret": "nope"})).status_code == 401


@pytest.mark.asyncio
async def test_header_secret_grants_access(client, admin_secret):
    resp = await client.get("/api/v1/family", headers={"X-Admin-Secret": SECRET})
    assert resp.status_code == 200
    assert "family" in resp.json()


@pytest.mark.asyncio
async def test_query_secret_grants_access(client, admin_secret):
    assert (await client.get(f"/api/v1/family?secret={SECRET}")).status_code == 200


@pytest.mark.asyncio
async def test_writes_are_gated_too(client, admin_secret):
    body = {"name": "Intruder", "relationship": "stranger"}
    assert (await client.post("/api/v1/family", json=body)).status_code == 401


@pytest.mark.asyncio
async def test_unset_secret_fails_closed(client, mocker):
    # A deploy that forgets the env var must deny, not serve the data openly.
    mocker.patch("app.config.settings.admin_api_secret", "")
    assert (await client.get("/api/v1/family")).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PUBLIC)
async def test_public_paths_need_no_secret(client, admin_secret, path):
    # /health is Railway's healthcheck; the consent/privacy/terms pages must
    # stay reachable for carrier review.
    assert (await client.get(path)).status_code == 200


@pytest.mark.asyncio
async def test_openapi_schema_is_not_published(client, admin_secret):
    assert (await client.get("/openapi.json")).status_code == 404
    assert (await client.get("/docs")).status_code == 404
