"""No paid APIs: plan cardinality, local-only approvals and fail-closed reporting."""

import hashlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlsplit

import pytest

from browser_agent.config import Settings
from browser_agent.storage import Store
from evals.fixtures import FixtureServer
from evals.report import build_report, junit_result, manual_check, require_deterministic
from evals.run import (
    FixtureApprover,
    build_plan,
    complete_result,
    fixture_browser_factory,
    upload_trace,
)


def test_suite_has_two_cases_not_cartesian_product():
    plan = build_plan(suite="generalization", seeds=[201, 202], max_experiment_usd=10)
    assert [(case["case"], case["seed"]) for case in plan] == [
        ("unfamiliar_event", 201),
        ("food_layout_variant", 202),
    ]
    with pytest.raises(ValueError, match="maximum admission"):
        build_plan(suite="generalization", seeds=[201, 202], max_experiment_usd=5)
    with pytest.raises(ValueError, match="one seed"):
        build_plan(suite="generalization", seeds=[201], max_experiment_usd=10)
    with pytest.raises(ValueError):
        build_plan(case="mail_latest_10", suite="core")


def test_repetitions_require_explicit_aggregate_allowance():
    with pytest.raises(ValueError):
        build_plan(case="mail_latest_10", repetitions=2, max_experiment_usd=5)
    assert (
        len(build_plan(case="mail_latest_10", repetitions=2, max_experiment_usd=10))
        == 2
    )


def test_partial_state_success_does_not_pass_normal_case():
    for status in ("partial", "needs_user", "failed", "cancelled"):
        assert not complete_result(
            {"status": status, "summary": "Effects happened", "remaining": []}
        )
    assert not complete_result(
        {
            "status": "completed",
            "summary": "Done",
            "remaining": ["One letter still missing"],
        }
    )
    assert complete_result(
        {
            "status": "completed",
            "summary": "Verified completed effects",
            "remaining": [],
        }
    )


@pytest.mark.asyncio
async def test_read_only_fixture_navigation_may_be_approved_but_payment_never_is(
    tmp_path,
):
    store = Store(tmp_path / "ledger.sqlite")
    with FixtureServer("food_previous_order") as fixture:
        approver = FixtureApprover(fixture, store)
        base = urlsplit(fixture.url)
        for key, expected in [("history", True), ("cart", True), ("pay", False)]:
            effect = {
                "destination": f"{base.scheme}://{base.netloc}{fixture.route(key)}",
                "operation": "page_change",
                "method": "",
                "fields": [],
            }
            rid = store.request_approval("run", key, {"tool": "click"}, effect, "gen")
            assert (
                await approver(
                    {"kind": "approval", "request_id": rid, "details": effect}
                )
            )["approved"] is expected
        assert [r["kind"] for r in approver.records[:2]] == ["navigation", "navigation"]
        effect = {
            "destination": f"{base.scheme}://{base.netloc}{fixture.route('history')}",
            "operation": "form_change",
            "method": "post",
        }
        rid = store.request_approval("run", "not-get", {"tool": "click"}, effect, "gen")
        assert not (
            await approver({"kind": "approval", "request_id": rid, "details": effect})
        )["approved"]


def test_junit_rejects_empty_failed_skipped_and_malformed(tmp_path):
    path = tmp_path / "junit.xml"
    assert not junit_result(path)["passed"]
    for xml in [
        "<testsuite/>",
        '<testsuite><testcase name="test_p01"><skipped/></testcase></testsuite>',
        '<testsuite><testcase name="test_p01"><failure/></testcase></testsuite>',
        "not xml",
    ]:
        path.write_text(xml)
        assert not junit_result(path)["passed"]
    path.write_text(
        '<testsuite><testcase name="test_p01_invalid"><properties><property name="acceptance_ids" value="B04,F08"/></properties></testcase></testsuite>'
    )
    assert junit_result(path)["passed"]
    assert junit_result(path)["ids"] == ["B04", "F08", "P01"]


def test_artifact_existence_never_passes_manual_review(tmp_path):
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"synthetic test artifact")
    assert not manual_check(
        {"evidence_path": str(video), "status": "PASS"}, "fingerprint"
    )
    entry = {
        "status": "PASS",
        "reviewer": "Test reviewer",
        "reviewed_at": "2026-09-09T12:00:00+00:00",
        "description": "Inspected artifact in a test",
        "evidence_path": str(video),
        "evidence_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        "runtime_fingerprint": "fingerprint",
    }
    assert manual_check(entry, "fingerprint")
    assert not manual_check(entry, "changed-runtime")
    video.write_bytes(b"changed artifact")
    assert not manual_check(entry, "fingerprint")


def test_final_report_is_not_ready_when_mandatory_evidence_missing(tmp_path):
    settings = Settings(artifact_dir=tmp_path / "artifacts")
    settings.prepare()
    report = build_report(settings, "test-release", root=tmp_path)
    assert report["overall"] == "NOT READY"
    assert len(report["missing_test_ids"]) == 47
    assert "jobs_resume_3" in report["missing_cases"]
    assert not any(report["manual_reviews"].values())
    with pytest.raises(RuntimeError, match="gated"):
        require_deterministic(settings)


@pytest.mark.asyncio
async def test_fixture_approval_requires_exact_local_expected_effect(tmp_path):
    store = Store(tmp_path / "ledger.sqlite")
    with FixtureServer("mail_latest_10") as fixture:
        approver = FixtureApprover(fixture, store)
        base = urlsplit(fixture.url)
        for index, expected in [(1, True), (0, False), (12, False)]:
            mid = fixture.messages[index]["id"]
            effect = {
                "destination": f"{base.scheme}://{base.netloc}{fixture.route('delete:' + mid)}",
                "fields": [],
            }
            request_id = store.request_approval(
                "run", "action-" + str(index), {"tool": "click"}, effect, "generation"
            )
            result = await approver(
                {"kind": "approval", "request_id": request_id, "details": effect}
            )
            assert result["approved"] is expected
        mid = fixture.messages[1]["id"]
        effect = {
            "destination": "https://real.example" + fixture.route("delete:" + mid),
            "fields": [],
        }
        request_id = store.request_approval(
            "run", "external", {"tool": "click"}, effect, "generation"
        )
        assert not (
            await approver(
                {"kind": "approval", "request_id": request_id, "details": effect}
            )
        )["approved"]
        effect["destination"] = fixture.url
        assert not (
            await approver(
                {"kind": "approval", "request_id": request_id, "details": effect}
            )
        )["approved"]


@pytest.mark.asyncio
async def test_fixture_browser_blocks_external_network_and_injects_stale_dom(tmp_path):
    with FixtureServer("stale_ref_recovery") as fixture:
        browser = fixture_browser_factory(fixture)(
            tmp_path / "profile", headless=True, artifact_dir=tmp_path / "evidence"
        )
        try:
            await browser.start(fixture.url)
            observation = await browser.observe()
            assert observation and fixture.state["stale_fault_injected"]
            # A blocked request is recorded by the harness; no external page is loaded.
            await browser.page.evaluate(
                "fetch('https://example.com/').catch(() => null)"
            )
            assert fixture.state["blocked_external_requests"] == [
                "https://example.com/"
            ]
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_langsmith_export_actually_awaits_root_and_nested_child(monkeypatch):
    run_id = str(uuid.uuid4())

    class FakeTree:
        def __init__(self, **kwargs):
            self.id = kwargs.get("id", uuid.uuid4())

        def post(self):
            pass

        def create_child(self, **kwargs):
            return FakeTree(**kwargs)

        def end(self, **kwargs):
            pass

        def patch(self):
            pass

        def get_url(self):
            return "https://smith.langchain.com/test-trace"

    monkeypatch.setattr("evals.run.RunTree", FakeTree)
    retrieve = AsyncMock(return_value=SimpleNamespace(parent_run_ids=[run_id]))
    client = SimpleNamespace(
        list_examples=Mock(return_value=[]),
        create_example=Mock(),
        create_feedback=Mock(),
        runs=SimpleNamespace(retrieve=retrieve),
    )
    project = SimpleNamespace(id=uuid.uuid4(), name="test-project")
    dataset = SimpleNamespace(id=uuid.uuid4())
    record = {
        "run_id": run_id,
        "case": "mail_latest_10",
        "seed": 101,
        "source_prompt": "Source",
        "effective_prompt": "Task",
        "url": "http://127.0.0.1:1/start",
        "started_at": "2026-09-09T12:00:00+00:00",
        "runtime_fingerprint": "fingerprint",
        "result": {"status": "completed"},
        "grade": {"checks": {"state": True}},
        "passed": True,
        "budget": {},
    }
    exported = await upload_trace(
        client,
        project,
        dataset,
        record,
        [{"event": "tool_call", "time": "2026-09-09T12:00:01+00:00"}],
    )
    assert exported["verified"]
    assert retrieve.await_count == 2
    assert client.create_feedback.call_count == 2
