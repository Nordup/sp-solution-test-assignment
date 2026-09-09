"""Fresh observation supersedes pending dispatch feedback before memory refresh."""

import copy
import json

import pytest
from test_graph_resume import approval_answer, click, graph_case, pending

from browser_agent.browser import BrowserError


async def test_forced_memory_binds_old_target_to_new_page_then_advances_once(tmp_path):
    first_page = '<title>Stage A</title><a href="https://fixture.test/b">Open preparation</a>'
    second_page = '''<title>Stage B</title><h1>Preparation</h1>
    <form action="https://fixture.test/review" method="post" onsubmit="event.preventDefault();window.effects=(window.effects||0)+1;document.title='Stage C';document.querySelector('output').textContent='Review ready';this.remove()">
    <button>Advance to review</button></form><output></output>'''
    async with graph_case(tmp_path, first_page, [click("Open preparation"), click("Advance to review")], classification="ordinary") as (
        browser, actor, store, runtime, graph, config, initial, _events
    ):
        await browser.page.route("https://fixture.test/b", lambda route: route.fulfill(body=second_page, content_type="text/html"))
        initial.update(steps=4, memory_step=1, task="Open preparation and advance to its review page.")
        original_call, original_review = actor.call, actor.review
        memory_packets = []

        async def review(task, action, metadata):
            response = await original_review(task, action, metadata)
            if metadata["name"] == "Advance to review":
                response["classification"] = "consequential"
            return response

        async def call(request, purpose="actor"):
            if request.get("tool_choice") == {"type": "function", "name": "remember"}:
                memory_packets.append(copy.deepcopy(request))
                current = json.loads(next(item["content"].split("\n", 1)[1] for item in request["input"] if item.get("content", "").startswith("Current browser observation")))
                feedback = json.loads(request["input"][-1]["content"].removeprefix("Runtime feedback: "))
                native = json.loads([item for item in request["input"] if item.get("type") == "function_call_output"][-1]["output"])
                assert current["title"] == "Stage B"
                assert feedback["requires_observation"] is False
                assert feedback["target"]["name"] == "Open preparation"
                assert feedback["source"]["evidence_id"] != current["id"]
                assert feedback["result"]["evidence_id"] == current["id"]
                assert feedback["result"]["title"] == "Stage B"
                assert native["requires_observation"] is False
                assert native["verified_transition"] == feedback
                assert native["observation"]["id"] == current["id"]
                prior_call = [item for item in request["input"] if item.get("type") == "function_call"][-1]
                assert json.loads(prior_call["arguments"])["ref"] == feedback["target"]["ref"]
                source = json.loads((runtime.run_dir / "evidence" / (feedback["source"]["evidence_id"] + ".json")).read_text())
                assert f'"Open preparation" [ref={feedback["target"]["ref"]}]' in source["text"]
                assert "Advance to review" in current["text"]
                assert await browser.page.evaluate("window.effects||0") == 0
            return await original_call(request, purpose)

        actor.call, actor.review = call, review
        paused = await graph.ainvoke(initial, config)
        assert pending(paused)["kind"] == "approval"
        assert len(memory_packets) == 1
        rows = store.actions_for_run(initial["run_id"])
        assert len(rows) == 1  # A -> B has run; B -> C still needs exact approval.
        result = await graph.ainvoke(approval_answer(paused), config)
        assert result["observation"]["title"] == "Stage C"
        assert await browser.page.evaluate("window.effects||0") == 1
        assert len(store.actions_for_run(initial["run_id"])) == 2
        assert actor.calls == 3 and actor.memory_calls == 1


@pytest.mark.parametrize("failure", ["browser", "challenge"])
async def test_unusable_observation_preserves_pending_native_result(tmp_path, failure):
    async with graph_case(tmp_path) as (_browser, _actor, _store, runtime, _graph, _config, initial, _events):
        observed = await runtime.observe(initial)
        pending_result = {"status": "executed", "requires_observation": True, "url": "https://fixture.test/next"}
        state = initial | observed | {
            "feedback": json.dumps(pending_result),
            "call": {"name": "click", "call_id": "call", "arguments": {"ref": "old-ref"}},
            "history": [[{"type": "function_call_output", "call_id": "call", "output": json.dumps(pending_result)}]],
        }
        original = copy.deepcopy(state)
        if failure == "browser":
            async def unavailable():
                raise BrowserError("closed", "Synthetic closed browser")
            runtime.browser.observe = unavailable
        else:
            await runtime.browser.page.set_content("<h1>Verify you are human</h1><p>CAPTCHA</p>")
        update = await runtime.verify(state)
        assert update["route"] == "ask"
        assert "history" not in update
        assert "observed_after_dispatch" not in update.get("feedback", "")
        assert state == original
        assert json.loads(state["history"][-1][-1]["output"])["requires_observation"] is True
