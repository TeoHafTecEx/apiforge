"""One small interface over Anthropic and OpenAI, so the rest of apiforge doesn't care which.

Provider choice: APIFORGE_PROVIDER=openai|anthropic, otherwise whichever API key is set
(OPENAI_API_KEY or ANTHROPIC_API_KEY). Model: APIFORGE_MODEL, otherwise the provider default.
Set OPENAI_BASE_URL to use any OpenAI-compatible endpoint (Azure, OpenRouter, a local model).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-5.6-sol",
}


def provider() -> str:
    explicit = os.getenv("APIFORGE_PROVIDER", "").strip().lower()
    if explicit:
        if explicit not in DEFAULT_MODELS:
            raise ValueError(f"APIFORGE_PROVIDER must be one of {', '.join(DEFAULT_MODELS)}")
        return explicit
    if os.getenv("OPENAI_API_KEY") and not os.getenv("ANTHROPIC_API_KEY"):
        return "openai"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    raise RuntimeError("Set OPENAI_API_KEY or ANTHROPIC_API_KEY (and optionally APIFORGE_PROVIDER).")


def default_model(prov: str | None = None) -> str:
    return os.getenv("APIFORGE_MODEL") or DEFAULT_MODELS[prov or provider()]


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Turn:
    """Neutral conversation history, rendered to each provider's format on every call."""
    role: str  # "user" | "assistant" | "tool_results"
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    results: list[tuple[str, str]] = field(default_factory=list)  # (tool_call_id, content)


# ---------------------------------------------------------------- rendering
def _anthropic_messages(history: list[Turn]) -> list[dict]:
    out = []
    for t in history:
        if t.role == "user":
            out.append({"role": "user", "content": t.text})
        elif t.role == "assistant":
            blocks = [{"type": "text", "text": t.text}] if t.text else []
            blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in t.calls]
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "…"}]})
        else:
            out.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": cid, "content": content} for cid, content in t.results]})
    return out


def _openai_messages(system: str, history: list[Turn]) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for t in history:
        if t.role == "user":
            out.append({"role": "user", "content": t.text})
        elif t.role == "assistant":
            msg = {"role": "assistant", "content": t.text or None}
            if t.calls:
                msg["tool_calls"] = [{"id": c.id, "type": "function",
                                      "function": {"name": c.name, "arguments": json.dumps(c.input)}}
                                     for c in t.calls]
            out.append(msg)
        else:
            out += [{"role": "tool", "tool_call_id": cid, "content": content} for cid, content in t.results]
    return out


# ---------------------------------------------------------------- clients
class LLM:
    def __init__(self, model: str | None = None, prov: str | None = None):
        self.provider = prov or provider()
        self.model = model or default_model(self.provider)
        if self.provider == "anthropic":
            from anthropic import Anthropic

            self.client = Anthropic()
        else:
            from openai import OpenAI

            self.client = OpenAI(timeout=900)  # long code generations

    def chat(self, system: str, history: list[Turn], tools: list[dict] | None = None,
             max_tokens: int = 4000) -> Turn:
        """One model call. Tools use a neutral schema: {name, description, parameters}."""
        if self.provider == "anthropic":
            kwargs = dict(model=self.model, max_tokens=max_tokens, system=system,
                          messages=_anthropic_messages(history))
            if tools:
                kwargs["tools"] = [{"name": t["name"], "description": t["description"],
                                    "input_schema": t["parameters"]} for t in tools]
            with self.client.messages.stream(**kwargs) as stream:  # streaming avoids timeouts
                msg = stream.get_final_message()
            text = "".join(b.text for b in msg.content if b.type == "text")
            calls = [ToolCall(b.id, b.name, dict(b.input)) for b in msg.content if b.type == "tool_use"]
        else:
            kwargs = dict(model=self.model, max_completion_tokens=max_tokens,
                          messages=_openai_messages(system, history))
            if tools:
                kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
            msg = self.client.chat.completions.create(**kwargs).choices[0].message
            text = msg.content or ""
            calls = []
            for c in msg.tool_calls or []:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except ValueError:
                    args = {}
                calls.append(ToolCall(c.id, c.function.name, args))
        return Turn("assistant", text=text, calls=calls)
