"""Browser evidence and artifact behavior without a real browser process."""

from __future__ import annotations

import pytest

from browser_agent.browser import PlaywrightCLI
from browser_agent.config import Settings


def _settings(tmp_path):
    return Settings(artifact_dir=tmp_path, browser_headed=False)


@pytest.mark.asyncio
async def test_search_browser_artifact_returns_bounded_context_and_offsets(tmp_path):
    browser = PlaywrightCLI(_settings(tmp_path), session_name="search-artifact")
    await browser.start()
    path = browser.browser_session_dir / "page.yml"
    path.write_text(
        "before: inbox\n"
        "name: ordinary\n"
        "ref: e5\n"
        "name: Delete permanently\n"
        "warning: permanent deletion\n"
        "after: inbox\n",
        encoding="utf-8",
    )
    result = await browser.execute(
        "search_browser_artifact",
        {"path": str(path), "query": "delete"},
    )
    assert result["status"] == "searched"
    assert result["count"] == 1
    assert result["matches"][0]["line"] == 4
    assert result["matches"][0]["offset"] == path.read_text(encoding="utf-8").index(
        "Delete"
    )
    assert "ref: e5" in result["matches"][0]["text"]
    assert "warning: permanent deletion" in result["matches"][0]["text"]
    await browser.close()


@pytest.mark.asyncio
async def test_search_browser_artifact_finds_late_query_in_huge_line(tmp_path):
    browser = PlaywrightCLI(_settings(tmp_path), session_name="search-late")
    await browser.start()
    path = browser.browser_session_dir / "large.yml"
    prefix = "x" * 20_000
    path.write_text(
        prefix + " Delete warning ref=e25 " + "y" * 20_000, encoding="utf-8"
    )
    result = await browser.execute(
        "search_browser_artifact",
        {"path": str(path), "query": "delete warning"},
    )
    assert result["count"] == 1
    assert result["matches"][0]["offset"] == len(prefix) + 1
    assert "Delete warning" in result["matches"][0]["text"]
    assert sum(len(item["text"]) for item in result["matches"]) <= 6_000
    await browser.close()


@pytest.mark.asyncio
async def test_oversized_inline_snapshot_is_externalized_but_reviewer_keeps_full_result(
    monkeypatch, tmp_path
):
    browser = PlaywrightCLI(_settings(tmp_path), session_name="large-snapshot")
    await browser.start()
    large_snapshot = {
        "snapshot": [
            {"role": "generic", "ref": "e1", "text": "x" * 8000},
            {"role": "button", "ref": "e250", "name": "Delete warning"},
            {"role": "generic", "ref": "e251", "text": "y" * 8000},
        ]
    }

    async def fake_run(_command, _args):
        return large_snapshot

    monkeypatch.setattr(browser, "_invoke_cli", fake_run)
    result = await browser.execute("playwright", {"command": "snapshot", "args": []})
    assert result["status"] == "executed"
    compact = result["output"]["result"]
    assert compact["truncated"] is True
    artifact = compact["artifact"]
    assert artifact.endswith(".txt")
    assert browser.evidence.find('"ref": "e250"') >= 0
    read = await browser.execute(
        "read_browser_artifact", {"path": artifact, "offset": 0}
    )
    assert read["status"] == "read"
    assert '"ref": "e250"' in read["text"]
    search = await browser.execute(
        "search_browser_artifact", {"path": artifact, "query": "e250"}
    )
    assert search["status"] == "searched"
    assert search["count"] == 1
    assert '"ref": "e250"' in search["matches"][0]["text"]
    await browser.close()


@pytest.mark.asyncio
async def test_oversized_eval_result_is_externalized(monkeypatch, tmp_path):
    browser = PlaywrightCLI(_settings(tmp_path), session_name="large-eval")
    await browser.start()
    value = "prefix " + ("secret-value " * 1500)

    async def fake_run(_command, _args):
        return {"result": value}

    monkeypatch.setattr(browser, "_invoke_cli", fake_run)
    result = await browser.execute(
        "playwright", {"command": "eval", "args": ["() => document.body.innerText"]}
    )
    assert result["status"] == "executed"
    compact = result["output"]["result"]
    assert compact["truncated"] is True
    read = await browser.execute(
        "read_browser_artifact", {"path": compact["artifact"], "offset": 0}
    )
    assert read["status"] == "read"
    assert read["text"].startswith("prefix secret-value")
    await browser.close()
