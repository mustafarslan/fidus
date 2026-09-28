"""Adapter translation tests: pure functions, no network."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS
from typing import Any

import pytest

from fidus.errors import ProviderError
from fidus.llm import anthropic_adapter as A
from fidus.llm import gemini_adapter as G
from fidus.llm import openai_adapter as O
from fidus.llm.fake import FakeProvider, fake_text
from fidus.llm.json_protocol import JsonToolProvider, parse_envelope
from fidus.llm.types import Message, Role, TextPart, ToolCall, ToolResult, ToolSpec, user

TOOL = ToolSpec(
    "read_file", "Read a file", {"type": "object", "properties": {"path": {"type": "string"}}}
)


def convo() -> list[Message]:
    return [
        user("brief"),
        Message(
            Role.ASSISTANT, [TextPart("let me look"), ToolCall("c1", "read_file", {"path": "a.py"})]
        ),
        Message(
            Role.USER, [ToolResult("c1", "read_file", "contents", False), TextPart("budget note")]
        ),
    ]


# -- Anthropic ---------------------------------------------------------------------------------
def test_anthropic_request_shape() -> None:
    msgs = A.to_request_messages(convo())
    assert msgs[1]["content"][1] == {
        "type": "tool_use",
        "id": "c1",
        "name": "read_file",
        "input": {"path": "a.py"},
    }
    assert msgs[2]["content"][0]["type"] == "tool_result"
    assert msgs[2]["content"][0]["tool_use_id"] == "c1"
    assert msgs[2]["content"][1] == {"type": "text", "text": "budget note"}
    assert A.to_request_tools([TOOL])[0]["input_schema"]["type"] == "object"


def _anthropic_resp(blocks: list[dict[str, Any]], stop: str = "tool_use") -> Any:
    return NS(
        content=blocks,
        stop_reason=stop,
        model="claude-opus-5",
        usage=NS(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=100,
            cache_creation_input_tokens=0,
        ),
    )


def test_anthropic_response_keeps_thinking_raw_for_echo() -> None:
    blocks = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "reading"},
        {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "x"}},
    ]
    c = A.from_response(_anthropic_resp(blocks))
    assert [type(p).__name__ for p in c.message.parts] == ["TextPart", "ToolCall"]
    assert c.message.raw == blocks and c.stop_reason == "tool_use"
    assert c.usage.input_tokens == 110 and c.usage.cache_read_tokens == 100
    # Echoed verbatim on the next turn (thinking signature preserved)
    assert A.to_request_messages([user("x"), c.message])[1]["content"] == blocks


def test_anthropic_fallback_echo_rules() -> None:
    blocks = [
        {"type": "thinking", "thinking": "", "signature": "old"},
        {"type": "text", "text": "partial"},
        {"type": "fallback", "from": {"model": "a"}, "to": {"model": "b"}},
        {"type": "tool_use", "id": "t2", "name": "read_file", "input": {}},
    ]
    c = A.from_response(_anthropic_resp(blocks))
    assert [b["type"] for b in c.message.raw] == ["text", "tool_use"]


def test_anthropic_refusal_raises() -> None:
    resp = _anthropic_resp([], stop="refusal")
    resp.stop_details = NS(category="cyber")
    with pytest.raises(ProviderError, match="cyber"):
        A.from_response(resp)


# -- OpenAI-compatible -------------------------------------------------------------------------
def test_openai_request_shape() -> None:
    msgs = O.to_request_messages("SYS", convo())
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[2]["tool_calls"][0]["function"] == {
        "name": "read_file",
        "arguments": json.dumps({"path": "a.py"}),
    }
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "contents"}
    assert msgs[4] == {"role": "user", "content": "budget note"}


def test_openai_response_bad_json_args() -> None:
    tc = NS(id="x", function=NS(name="read_file", arguments="{not json"))
    resp = NS(
        choices=[NS(message=NS(content=None, tool_calls=[tc]), finish_reason="tool_calls")],
        usage=NS(prompt_tokens=3, completion_tokens=2, prompt_tokens_details=None),
        model="m",
    )
    c = O.from_response(resp)
    call = c.message.tool_calls[0]
    assert O.BAD_ARGS in call.arguments and c.stop_reason == "tool_use"


# -- Gemini ------------------------------------------------------------------------------------
def test_gemini_contents_and_raw_passthrough() -> None:
    from google.genai import types

    contents = G.to_contents(convo(), types)
    assert contents[1].role == "model"
    assert contents[1].parts[1].function_call.name == "read_file"
    assert contents[2].parts[0].function_response.response == {"result": "contents"}
    raw = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(name="read_file", args={}),
                thought_signature=b"sig",
            )
        ],
    )
    resp = NS(
        candidates=[NS(content=raw, finish_reason="STOP")],
        usage_metadata=NS(
            prompt_token_count=5,
            candidates_token_count=2,
            thoughts_token_count=1,
            cached_content_token_count=0,
        ),
        model_version="g",
    )
    c = G.from_response(resp, turn=1)
    assert c.message.tool_calls[0].id.startswith(G.SYNTHETIC_ID)  # invented when Gemini omits one
    assert c.usage.output_tokens == 3
    assert G.to_contents([user("x"), c.message], types)[1] is raw  # thought signature preserved


# -- JSON tool protocol ------------------------------------------------------------------------
def test_parse_envelope_variants() -> None:
    names = {"read_file", "done"}
    assert parse_envelope('{"tool": "done", "arguments": {"summary": "x"}}', names)[0] == "done"
    fenced = 'Sure!\n```json\n{"tool": "read_file", "arguments": {"path": "a{b}.py"}}\n```'
    assert parse_envelope(fenced, names) == ("read_file", {"path": "a{b}.py"})
    with pytest.raises(ValueError):
        parse_envelope('{"tool": "rm_rf"}', names)
    with pytest.raises(ValueError):
        parse_envelope("no json here", names)


def test_json_provider_repairs_then_succeeds() -> None:
    inner = FakeProvider(
        [fake_text("oops"), fake_text('{"tool": "read_file", "arguments": {"path": "a"}}')]
    )
    p = JsonToolProvider(inner)  # type: ignore[arg-type]
    c = p.complete(system="S", messages=convo(), tools=[TOOL], max_tokens=100, temperature=None)
    assert c.message.tool_calls[0].name == "read_file"
    assert "Tool-calling protocol" in inner.calls[0]["system"]
    assert inner.calls[0]["tools"] == []  # tools are described in the prompt, not passed natively
    sent = inner.calls[0]["messages"]
    assert '<tool_result name="read_file"' in sent[2].parts[0].text


def test_auto_protocol_rescues_dropped_tool_calls() -> None:
    from fidus.llm.json_protocol import AutoToolProvider
    from fidus.llm.types import Completion, Usage

    empty = Completion(Message(Role.ASSISTANT, []), Usage(10, 800), "end_turn", "m")
    inner = FakeProvider(
        [
            empty,  # native: dropped call
            fake_text('{"tool": "read_file", "arguments": {"path": "a"}}'),  # JSON rescue
            fake_text('{"tool": "read_file", "arguments": {"path": "b"}}'),  # stays on JSON
        ]
    )
    p = AutoToolProvider(inner)  # type: ignore[arg-type]
    kw = {
        "system": "S",
        "messages": convo(),
        "tools": [TOOL],
        "max_tokens": 100,
        "temperature": None,
    }
    c1 = p.complete(**kw)  # type: ignore[arg-type]
    assert c1.message.tool_calls[0].arguments == {"path": "a"} and c1.usage.output_tokens > 800
    c2 = p.complete(**kw)  # type: ignore[arg-type]
    assert c2.message.tool_calls[0].arguments == {"path": "b"}
    assert inner.calls[1]["tools"] == [] and inner.calls[2]["tools"] == []
