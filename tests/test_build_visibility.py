"""Which build is actually serving.

"Is the latest version live?" had no answer from outside. /health says the app
is up, not what it is running, and a merge that never deployed looks exactly
like one that did — so the question was being settled by inference, which is
how you end up confidently testing yesterday's code.

/health/config now carries the commit Railway built from, when the process
started, and the model with its real token budgets.
"""
import pytest


@pytest.mark.asyncio
async def test_the_running_commit_is_reported(client, monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "de50e123c26de53d72c147f4faf6f15470ec581b")
    monkeypatch.setenv("RAILWAY_GIT_BRANCH", "main")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_api_secret", "s3cret", raising=False)

    r = await client.get("/health/config", headers={"X-Admin-Secret": "s3cret"})

    assert r.status_code == 200
    build = r.json()["build"]
    assert build["commit"] == "de50e12"          # short, comparable to a merge sha
    assert build["branch"] == "main"
    assert build["uptime_minutes"] >= 0
    assert build["started_at"]


@pytest.mark.asyncio
async def test_an_unbuilt_environment_says_unknown_rather_than_guessing(client, monkeypatch):
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("RAILWAY_GIT_BRANCH", raising=False)
    from app.config import settings
    monkeypatch.setattr(settings, "admin_api_secret", "s3cret", raising=False)

    r = await client.get("/health/config", headers={"X-Admin-Secret": "s3cret"})

    assert r.json()["build"]["commit"] == "(unknown)"


@pytest.mark.asyncio
async def test_the_model_and_its_budgets_are_visible(client, monkeypatch):
    """The legacy 2,048/8,192 defaults are what starved it into truncating.
    Seeing the real numbers is how you catch a silent fall back to them."""
    from app.config import settings
    monkeypatch.setattr(settings, "admin_api_secret", "s3cret", raising=False)

    r = await client.get("/health/config", headers={"X-Admin-Secret": "s3cret"})

    model = r.json()["model"]
    assert model["name"] == settings.claude_model
    assert model["deep_max_tokens"] > 8_192, "fell back to the starving defaults"
    assert model["normal_max_tokens"] > 2_048


@pytest.mark.asyncio
async def test_the_build_block_is_still_behind_the_admin_gate(client):
    r = await client.get("/health/config")

    assert r.status_code in (401, 403)
    assert "commit" not in r.text


@pytest.mark.asyncio
async def test_public_health_says_nothing_about_the_build(client):
    """/health is unauthenticated; it stays a liveness check."""
    r = await client.get("/health")

    assert r.status_code == 200
    assert "commit" not in r.text
