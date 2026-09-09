"""Actual recovery/approval path; scripted judgments do not establish model quality."""

import json

import pytest
from test_graph_resume import approval_answer, choose_ref, click, graph_case, pending

SOURCE = "Jordan Lee. Two years of technical writing. Created one payroll guide. Separately built a release checklist."
MISLEADING = "Jordan has two years of payroll leadership and built many production operations systems."
EXPANDED = "I have two years of payroll leadership and built many production operations systems."
PAGE = f'''<h1>Verified profile</h1><article>{SOURCE}</article>
<form action="https://fixture.test/message" method="post" onsubmit="event.preventDefault();window.sent=document.querySelector('textarea').value;window.effects=(window.effects||0)+1;document.querySelector('output').textContent='Message delivered'">
<textarea aria-label="Message"></textarea><button>Send message</button></form><output></output>'''


def fill(value):
    return lambda obs: ("fill", {"ref": choose_ref(obs, "Message"), "value": value})


@pytest.mark.parametrize("corrected", [
    "I have two years of experience in technical writing. I created a payroll guide and, separately, a release checklist.",
    "I created one payroll guide and separately built a release checklist. I would like to focus on improving your operations documentation.",
])
async def test_source_grounding_rejection_keeps_evidence_and_redrafts_before_one_exact_submission(tmp_path, corrected):
    async with graph_case(tmp_path, html=PAGE, script=[fill(EXPANDED), fill(corrected), click("Send message")], classification="ordinary") as (
        browser, actor, store, runtime, graph, config, initial, events
    ):
        initial.update(task="Draft a factual message from my verified profile and send it using this form.", memory_step=1)
        runtime.persist_memory(initial | {"notes": MISLEADING, "scope": None})
        original_review, original_call = actor.review, actor.call
        reviews, redraft_feedback = [], []

        async def review(task, action, metadata):
            reviews.append((action, metadata))
            assert task == initial["task"]
            assert metadata["task_context"]["working_notes"] == MISLEADING
            assert any(SOURCE in text for key, text in metadata["scope_sources"].items() if key.startswith("obs-"))
            result = await original_review(task, action, metadata)
            if action["tool"] == "fill":
                assert action["args"]["value"] in {EXPANDED, corrected}
                if action["args"]["value"] == EXPANDED:
                    assert await browser.page.locator("textarea").input_value() == ""
                    assert not store.actions_for_run(initial["run_id"])
                    return result | {"classification": "forbidden", "scope_status": "out_of_scope", "reason": "Redraft: the source says two years of technical writing and one guide, not payroll leadership or multiple systems."}
                # The rejected draft never reached the editable field or action journal.
                assert await browser.page.locator("textarea").input_value() == ""
                assert not store.actions_for_run(initial["run_id"])
                return result
            assert await browser.page.locator("textarea").input_value() == corrected
            assert any(field.get("value") == corrected for field in metadata["fields"])
            return result | {"classification": "consequential"}

        async def call(request, purpose="actor"):
            if actor.calls == 1:
                feedback = [item["content"] for item in request["input"] if isinstance(item.get("content"), str) and item["content"].startswith("Runtime feedback:")]
                assert feedback and "Redraft:" in feedback[-1]
                redraft_feedback.extend(feedback)
            return await original_call(request, purpose)

        actor.review, actor.call = review, call
        paused = await graph.ainvoke(initial, config)
        approval = pending(paused)
        assert approval["kind"] == "approval"
        assert redraft_feedback
        assert len(reviews) == 3
        assert len(store.actions_for_run(initial["run_id"])) == 1
        details = store.approval(approval["request_id"])["details"]
        assert corrected in json.dumps(details)
        assert EXPANDED not in json.dumps(details)
        assert await browser.page.evaluate("window.effects||0") == 0
        assert any(name == "recover" for name, _ in events)
        result = await graph.ainvoke(approval_answer(paused), config)
        assert result["result"]["status"] == "partial"  # scripted actor's final stop
        assert await browser.page.evaluate("window.effects||0") == 1
        assert await browser.page.evaluate("window.sent") == corrected
        assert len(store.actions_for_run(initial["run_id"])) == 2
        assert store.approval(approval["request_id"])["status"] == "consumed"
        assert len(reviews) == 3
