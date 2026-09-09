import json

import pytest

from browser_agent.context import ContextOverflow, build_input
from browser_agent.tools import ProtocolError, parse_call, protocol_pair, tool_specs


def response(name="click", args=None):
    return {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "name": name,
                "call_id": "c1",
                "arguments": json.dumps(args or {"ref": "e3"}),
            }
        ],
    }


@pytest.mark.parametrize(
    "name,args",
    [
        ("unknown", {}),
        ("click", {"ref": 3}),
        ("click", {"ref": "x", "safe": True}),
        ("fill", {"ref": "x"}),
        ("press", {"ref": "x", "key": "Meta+L"}),
    ],
)
def test_p01_reject_invalid(name, args):
    with pytest.raises(ProtocolError):
        parse_call(response(name, args))


def test_p01_malformed_json():
    r = response()
    r["output"][0]["arguments"] = 'prose ```{"ref":"x"}```'
    with pytest.raises(ProtocolError):
        parse_call(r)


def test_p02_no_partial_dispatch():
    r = response()
    r["output"] *= 2
    with pytest.raises(ProtocolError):
        parse_call(r)
    r = response()
    r["status"] = "incomplete"
    with pytest.raises(ProtocolError):
        parse_call(r)
    r = response()
    r["output"].append(
        {"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}
    )
    with pytest.raises(ProtocolError):
        parse_call(r)


def test_p03_native_call_roundtrip():
    call = parse_call(response("fill", {"ref": "e3", "value": "Привет"}))
    pair = protocol_pair(call, {"ok": True})
    assert pair[0]["call_id"] == pair[1]["call_id"] == "c1"
    assert json.loads(pair[0]["arguments"])["value"] == "Привет"
    assert all(
        t["strict"] and t["parameters"]["additionalProperties"] is False
        for t in tool_specs()
    )


def test_p04_context_bounds_and_protocol_groups():
    pair = protocol_pair(parse_call(response()), {"ok": True})
    state = {
        "task": "Preserve essential constraints",
        "notes": "n" * 10000,
        "history": [pair] * 1000,
        "observation": {"text": "巨" * 50000, "refs": {"hidden": "not delivered"}},
    }
    inputs = build_input(state)
    assert len(json.dumps(inputs)) < 80000
    assert inputs[0]["content"] == state["task"]
    assert len([x for x in inputs if x.get("type") == "function_call"]) == 6
    assert "TRUNCATED" in inputs[-1]["content"]
    with pytest.raises(ContextOverflow):
        build_input({"task": "x" * 12001})
