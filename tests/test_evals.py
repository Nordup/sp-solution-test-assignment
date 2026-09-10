"""Real fixture outcomes and bounded synthetic permissions; never paid actor runs."""

import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest
from playwright.async_api import Error as PlaywrightError

from browser_agent.config import Settings
from evals.fixtures import FixtureServer
from evals.graders import grade_fixture
from evals.run import FixtureApprover, evaluate, fixture_browser_factory
from scripts.demo_terminal import run_demo


def visit(fixture, key, fields=None):
    url = fixture.url.rsplit("/", 1)[0] + fixture.route(key)
    request = Request(
        url, data=urlencode(fields).encode() if fields is not None else None
    )
    with urlopen(request, timeout=3) as response:
        text = response.read().decode()
        fixture.record_delivered_view({"id": key, "url": response.url, "text": text})
        return text


@pytest.mark.parametrize(
    "case", ["mail_latest_10", "food_previous_order", "jobs_resume_3"]
)
def test_actual_fixture_outcomes_pass_and_missing_effects_fail(case):
    with FixtureServer(case) as fixture:
        result = {"status": "completed", "summary": "Fixture outcome test."}
        assert not grade_fixture(fixture, result)["passed"]
        if case == "mail_latest_10":
            for message in fixture.messages:
                if message["latest"]:
                    visit(fixture, "mail:" + message["id"])
                    if message["spam"]:
                        visit(fixture, "delete:" + message["id"], {})
        elif case == "food_previous_order":
            visit(fixture, "history")
            visit(fixture, "add:ember:bbq", {})
            visit(fixture, "add:ember:fries", {})
            visit(fixture, "review", {})
        else:
            visit(fixture, "profile")
            for job in fixture.jobs:
                if job["suitable"]:
                    visit(fixture, "job:" + job["id"])
                    letter = f"Dear {job['company']}, I have Python experience and built a RAG service with FastAPI and PostgreSQL. I also shipped LangGraph agents with human approval gates. Your {job['title']} role interests me."
                    visit(fixture, "apply:" + job["id"], {"letter": letter})
        assert grade_fixture(fixture, result)["passed"]
        assert not grade_fixture(fixture, result | {"status": "partial"})["passed"]
        fixture.state["payment_count"] += 1
        assert not grade_fixture(fixture, result)["passed"]


async def test_fixture_approvals_deny_off_origin_payment_and_wrong_deletion():
    with FixtureServer("mail_latest_10") as fixture:
        approver = FixtureApprover(fixture)
        legitimate = next(m for m in fixture.messages if m["latest"] and not m["spam"])
        spam = next(m for m in fixture.messages if m["latest"] and m["spam"])
        base = fixture.url.rsplit("/", 1)[0]
        for destination, expected in (
            (base + fixture.route("delete:" + legitimate["id"]), False),
            (base + fixture.route("delete:" + spam["id"]), True),
            ("https://other.invalid" + fixture.route("delete:" + spam["id"]), False),
            (base + fixture.route("pay"), False),
        ):
            answer = await approver(
                {
                    "kind": "approval",
                    "request_id": "exact",
                    "action": {"tool": "click", "args": {"ref": "current"}},
                    "details": {"destination": destination},
                }
            )
            assert answer == {"request_id": "exact", "approved": expected}
        assert (
            await approver({"kind": "clarification", "question": "Which one?"}) is None
        )


async def test_fixture_browser_blocks_external_network_and_records_observations(
    tmp_path,
):
    with FixtureServer("food_previous_order") as fixture:
        browser = fixture_browser_factory(fixture)(tmp_path / "profile", headless=True)
        await browser.start(fixture.url)
        try:
            observation = await browser.observe()
            assert (
                fixture.state["delivered_observations"][-1]["id"] == observation["id"]
            )
            with pytest.raises(PlaywrightError):
                await browser.page.goto("https://external.invalid/never-load")
            assert fixture.state["blocked_external_requests"] == [
                "https://external.invalid/never-load"
            ]
        finally:
            await browser.close()


async def test_terminal_launcher_forwards_real_task_and_human_responder(tmp_path):
    from browser_agent import cli

    seen = []

    async def runner(settings, **kwargs):
        seen.append(kwargs)
        assert kwargs["task"] == "The user's exact task"
        assert kwargs["responder"] is cli.human and kwargs["console"] is cli.console
        assert kwargs["headless"] is False and kwargs["synthetic"] is True
        assert not {"release_session", "checkpoint", "expected_results"} & kwargs.keys()
        return {"status": "partial", "summary": "Actual runner result"}

    result = await run_demo(
        Settings(artifact_dir=tmp_path),
        "The user's exact task",
        fixture_name="food_previous_order",
        runner=runner,
    )
    assert result["summary"] == "Actual runner result" and len(seen) == 1
    with pytest.raises(ValueError):
        await run_demo(
            Settings(artifact_dir=tmp_path),
            "task",
            fixture_name="food_previous_order",
            profile="demo",
            runner=runner,
        )


async def test_simple_eval_keeps_failures_locally_without_release_or_paid_calls(
    tmp_path, monkeypatch
):
    async def actor(settings, **kwargs):
        assert kwargs["synthetic"] and kwargs["new_run_id"]
        return {
            "run_id": kwargs["new_run_id"],
            "status": "partial",
            "summary": "No fixture work performed",
            "remaining": ["Read the messages"],
            "steps": 1,
            "cost_usd": 0,
        }

    monkeypatch.setattr("evals.run.run_agent", actor)
    records = await evaluate(Settings(artifact_dir=tmp_path), ["mail_latest_10"])
    assert len(records) == 1 and not records[0]["passed"]
    saved = list((tmp_path / "evals").glob("simple-*/*.json"))
    assert (
        len(saved) == 1
        and json.loads(saved[0].read_text())["result"]["status"] == "partial"
    )
    assert saved[0].stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="maximum"):
        await evaluate(Settings(artifact_dir=tmp_path), ["mail_latest_10"] * 4)


async def test_optional_letter_judge_uses_only_remaining_task_allowance(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from evals.run import judge_letters

    budgets = []
    closed = AsyncMock()

    class FakeJudge:
        cost_usd = 0.02

        def __init__(self, settings):
            budgets.append(settings.budget_usd)
            self.client = SimpleNamespace(close=closed)

        async def call(self, request, purpose):
            assert purpose == "letter_evaluator"
            assert request["tools"][0]["strict"]
            return {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "name": "grade_letters",
                        "call_id": "judge",
                        "arguments": json.dumps(
                            {"grounded": True, "personalized": True, "issues": []}
                        ),
                    }
                ],
            }

    monkeypatch.setattr("evals.run.Gateway", FakeJudge)
    with FixtureServer("jobs_resume_3") as fixture:
        result = await judge_letters(
            Settings(artifact_dir=tmp_path), fixture, {"cost_usd": 4.9}
        )
        assert budgets == [pytest.approx(0.1)] and result["cost_usd"] + 4.9 <= 5
        assert result["status"] == "graded" and closed.await_count == 1
        stopped = await judge_letters(
            Settings(artifact_dir=tmp_path), fixture, {"cost_usd": 5}
        )
        assert stopped["status"] == "not_run" and len(budgets) == 1


def test_langsmith_export_links_synthetic_case_to_dataset_and_scores(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from evals.run import export_langsmith

    client = SimpleNamespace(
        read_dataset=Mock(return_value=SimpleNamespace(id="dataset")),
        create_example=Mock(return_value=SimpleNamespace(id="example")),
        create_project=Mock(),
        create_run=Mock(),
        create_feedback=Mock(),
    )
    monkeypatch.setattr("langsmith.Client", lambda **_kwargs: client)
    record = {
        "case": "mail_latest_10",
        "seed": 101,
        "task": "Read synthetic inbox",
        "run_id": "a26751c1-6b90-40c1-9e72-2284b5e2d100",
        "result": {"status": "completed", "summary": "Synthetic result"},
        "grade": {"checks": {"completed": True, "exactly_spam_removed": False}},
        "cost_usd": 0.05,
        "started_at": "2026-09-10T00:00:00Z",
        "ended_at": "2026-09-10T00:01:00Z",
    }
    result = export_langsmith(record, "synthetic-experiment")
    assert result["status"] == "exported"
    sent = client.create_run.call_args.kwargs
    assert sent["reference_example_id"] == "example" and sent["id"] == record["run_id"]
    assert sent["extra"]["metadata"]["synthetic"] is True
    assert client.create_feedback.call_count == 2
    assert (
        client.create_example.call_args.kwargs["outputs"]["checks"][
            "exactly_spam_removed"
        ]
        is True
    )
    assert client.create_feedback.call_args.kwargs["score"] == 0
    assert client.create_project.call_args.kwargs["reference_dataset_id"] == "dataset"
    assert sent["project_name"] == "synthetic-experiment"
