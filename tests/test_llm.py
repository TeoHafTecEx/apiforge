"""Provider selection and the OpenAI / Anthropic wire formats."""
import json
from types import SimpleNamespace

import pytest

from apiforge import llm
from apiforge.llm import LLM, ToolCall, Turn

TOOLS = [{"name": "click", "description": "Click", "parameters": {
    "type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}]
HISTORY = [
    Turn("user", text="start"),
    Turn("assistant", text="", calls=[ToolCall("c1", "click", {"id": 3})]),
    Turn("tool_results", results=[("c1", "clicked")]),
]


@pytest.mark.parametrize("env,expected", [
    ({"OPENAI_API_KEY": "x"}, "openai"),
    ({"ANTHROPIC_API_KEY": "x"}, "anthropic"),
    ({"OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": "y", "APIFORGE_PROVIDER": "openai"}, "openai"),
])
def test_provider_selection(monkeypatch, env, expected):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "APIFORGE_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert llm.provider() == expected


def test_no_key_is_a_clear_error(monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "APIFORGE_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        llm.provider()


def test_openai_wire_format(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    sent = {}

    def create(**kw):
        sent.update(kw)
        call = SimpleNamespace(id="c2", function=SimpleNamespace(name="click", arguments='{"id": 5}'))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))])

    m = LLM(prov="openai", model="test-model")
    m.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    reply = m.chat("sys", HISTORY, tools=TOOLS)

    assert reply.calls == [ToolCall("c2", "click", {"id": 5})]
    msgs = sent["messages"]
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[2]["tool_calls"][0]["function"] == {"name": "click", "arguments": json.dumps({"id": 3})}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "clicked"}
    assert sent["tools"][0]["type"] == "function" and sent["tools"][0]["function"]["name"] == "click"
    assert sent["model"] == "test-model"


def test_anthropic_wire_format(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    sent = {}

    class Stream:
        def __init__(self, **kw):
            sent.update(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return SimpleNamespace(content=[
                SimpleNamespace(type="text", text="ok"),
                SimpleNamespace(type="tool_use", id="c2", name="click", input={"id": 5})])

    m = LLM(prov="anthropic", model="test-model")
    m.client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: Stream(**kw)))
    reply = m.chat("sys", HISTORY, tools=TOOLS)

    assert reply.text == "ok" and reply.calls == [ToolCall("c2", "click", {"id": 5})]
    assert sent["system"] == "sys"
    assert sent["messages"][1]["content"][0] == {"type": "tool_use", "id": "c1", "name": "click", "input": {"id": 3}}
    assert sent["messages"][2]["content"][0]["type"] == "tool_result"
    assert sent["tools"][0]["input_schema"]["required"] == ["id"]
