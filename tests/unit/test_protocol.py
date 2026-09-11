"""Native tool-call parsing and result pairing."""

import json

import pytest
from openai.types.responses import ResponseFunctionToolCall, ResponseReasoningItem

from browser_agent.protocol import ProtocolError, parse_tool_call


def native(
    name="playwright",
    arguments='{"command": "snapshot", "args": []}',
):
    return {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "name": name,
                "call_id": "one",
                "arguments": arguments,
            }
        ],
    }


@pytest.mark.parametrize("bad", ["{broken", '{"unexpected": true}'])
def test_native_json_rejects_malformed_or_extra_fields(bad):
    assert parse_tool_call(native())["arguments"] == {"command": "snapshot", "args": []}
    with pytest.raises(ProtocolError):
        parse_tool_call(native(arguments=bad))


def test_incomplete_native_response_feedback_preserves_prior_results():
    with pytest.raises(ProtocolError) as caught:
        parse_tool_call({"status": "incomplete", "output": []})
    message = str(caught.value)
    assert "next model decision was incomplete" in message
    assert "no new browser call was dispatched" in message
    assert "Prior browser results remain valid" in message


def test_actual_cli_image_is_native_input_image_and_text_is_preserved():
    from browser_agent.protocol import tool_result_items

    call = parse_tool_call(native("playwright", '{"command":"screenshot","args":[]}'))
    pair = tool_result_items(
        call,
        {
            "status": "executed",
            "tool": "playwright",
            "output": {"path": "shot.png"},
            "content": [{"type": "image", "data": "aGk=", "mimeType": "image/png"}],
        },
    )
    output = pair[1]["output"]
    assert {block["type"] for block in output} == {"input_text", "input_image"}
    assert "shot.png" in output[0]["text"]
    assert output[1]["image_url"].startswith("data:image/png;base64,")


def test_tool_result_items_preserve_opaque_reasoning_and_assistant_items_once():
    response = {
        "status": "completed",
        "output": [
            {
                "type": "reasoning",
                "id": "rs_1",
                "encrypted_content": "opaque-reasoning-1",
                "summary": [],
            },
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Inspecting the page."}],
                "status": "completed",
            },
            {
                "type": "function_call",
                "name": "playwright",
                "call_id": "call_1",
                "arguments": '{"command":"snapshot","args":[]}',
            },
        ],
    }
    from browser_agent.protocol import tool_result_items

    call = parse_tool_call(response)
    paired = tool_result_items(call, {"status": "executed", "output": {"ok": True}})
    assert [item["type"] for item in paired] == [
        "reasoning",
        "message",
        "function_call",
        "function_call_output",
    ]
    assert paired[0]["encrypted_content"] == "opaque-reasoning-1"
    assert paired[1]["content"][0]["text"] == "Inspecting the page."
    assert sum(item["type"] == "function_call" for item in paired) == 1
    assert paired[-1]["call_id"] == "call_1"


def test_completed_sdk_items_are_normalized_for_next_input_without_losing_ciphertext():
    reasoning = ResponseReasoningItem(
        id="rs-sdk",
        summary=[],
        type="reasoning",
        encrypted_content="opaque-sdk-reasoning",
    )
    function_call = ResponseFunctionToolCall(
        arguments='{"command":"snapshot","args":[]}',
        call_id="call-sdk",
        name="playwright",
        type="function_call",
    )

    class CompletedResponse:
        def __init__(self):
            self.status = "completed"
            self.output = [reasoning, function_call]

        def model_dump(self, **kwargs):
            return {
                "status": self.status,
                "output": [item.model_dump(**kwargs) for item in self.output],
            }

    call = parse_tool_call(CompletedResponse())
    carried = call["response_items"]
    assert carried[0] == {
        "id": "rs-sdk",
        "summary": [],
        "type": "reasoning",
        "encrypted_content": "opaque-sdk-reasoning",
    }
    assert carried[1] == {
        "arguments": '{"command":"snapshot","args":[]}',
        "call_id": "call-sdk",
        "name": "playwright",
        "type": "function_call",
    }
    assert all("status" not in item for item in carried)


@pytest.mark.parametrize(
    ("name", "payload", "expected"),
    [
        ("playwright", {"command": "", "args": []}, "command"),
        ("playwright", {"command": "click", "args": [1]}, "args"),
        ("read_browser_artifact", {"path": "", "offset": 0}, "path"),
    ],
)
def test_invalid_tool_arguments_report_safe_actionable_validation(
    name, payload, expected
):
    with pytest.raises(ProtocolError) as caught:
        parse_tool_call(
            native(
                name,
                json.dumps(payload),
            )
        )
    message = str(caught.value)
    assert name in message and expected in message
    assert "private-secret-selector" not in message
    assert "Native arguments failed" not in message
