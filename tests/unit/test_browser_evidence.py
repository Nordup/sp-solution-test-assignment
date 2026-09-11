"""Reviewer evidence retention independent of browser process execution."""

import time

from browser_agent.browser.artifacts import ArtifactStore
from browser_agent.browser.evidence import EvidenceCache


def evidence_cache(tmp_path):
    artifacts = ArtifactStore(tmp_path / "session", tmp_path / "evidence")
    artifacts.prepare_session()
    artifacts.evidence_root = tmp_path / "evidence"
    return EvidenceCache(artifacts), artifacts


def test_screenshot_output_does_not_erase_page_evidence(tmp_path):
    evidence, _artifacts = evidence_cache(tmp_path)
    evidence.update(
        "snapshot",
        {"result": {"snapshot": [{"ref": "e5", "role": "button", "name": "Delete"}]}},
    )
    assert "Delete" in evidence.text and "e5" in evidence.text

    evidence.update(
        "screenshot",
        {"result": "- [Screenshot of viewport](screenshot.png)"},
    )
    assert "Delete" in evidence.text and "e5" in evidence.text
    assert "screenshot.png" in evidence.text


def test_stale_snapshot_path_does_not_replace_current_page_evidence(tmp_path):
    evidence, artifacts = evidence_cache(tmp_path)
    evidence.update(
        "snapshot",
        {"result": {"snapshot": [{"ref": "e5", "name": "Current inbox"}]}},
    )
    old = artifacts.session_root / "old.yml"
    old.write_text("ref: e99\nname: Old warning\n", encoding="utf-8")
    after_write = time.time_ns()
    evidence.update(
        "click",
        {"result": {"snapshot": {"file": str(old)}}},
        fresh_after_ns=after_write,
    )
    assert "Current inbox" in evidence.text
    assert "Old warning" not in evidence.text
