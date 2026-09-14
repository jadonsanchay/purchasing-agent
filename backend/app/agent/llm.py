"""LLM adapters. The runner only depends on `LLM.respond`, so tests use ScriptedLLM and production uses OpenAI."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import settings


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMTurn:
    tool_calls: list[ToolCall]
    text: str = ""
    raw_output_items: list[dict] = field(default_factory=list)  # appended to the conversation verbatim
    usage: dict[str, int] = field(default_factory=dict)


class LLM(Protocol):
    def respond(self, instructions: str, input_items: list[dict], tools: list[dict]) -> LLMTurn: ...


class OpenAILLM:
    """OpenAI Responses API with function tools. Stateless: full conversation is sent each turn."""

    def __init__(self, model: str | None = None, api_key: str | None = None):
        from openai import OpenAI

        self.model = model or settings.openai_model
        self.client = OpenAI(api_key=api_key or settings.openai_api_key)

    def respond(self, instructions: str, input_items: list[dict], tools: list[dict]) -> LLMTurn:
        resp = self.client.responses.create(
            model=self.model,
            instructions=instructions,
            input=input_items,
            tools=tools,
            tool_choice="auto",
            parallel_tool_calls=True,
            store=False,
        )
        calls: list[ToolCall] = []
        raw: list[dict] = []
        text_parts: list[str] = []
        for item in resp.output:
            d = item.model_dump(exclude_none=True)
            if item.type == "function_call":
                calls.append(ToolCall(call_id=item.call_id, name=item.name, arguments=json.loads(item.arguments or "{}")))
                raw.append({"type": "function_call", "call_id": item.call_id, "name": item.name, "arguments": item.arguments})
            elif item.type == "message":
                for c in getattr(item, "content", []) or []:
                    if getattr(c, "type", "") == "output_text":
                        text_parts.append(c.text)
                raw.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "\n".join(text_parts)}]})
            elif item.type == "reasoning":
                raw.append(d)  # reasoning items must be echoed back for reasoning models
        usage = {}
        if resp.usage:
            usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return LLMTurn(tool_calls=calls, text="\n".join(text_parts), raw_output_items=raw, usage=usage)


class ScriptedLLM:
    """Deterministic stand-in for tests and offline evals. Each script entry is one turn: a list of
    (tool_name, arguments). Turns are consumed in order regardless of input."""

    def __init__(self, script: list[list[tuple[str, dict[str, Any]]]]):
        self.script = [list(t) for t in script]
        self.seen_inputs: list[list[dict]] = []
        self._n = 0

    def respond(self, instructions: str, input_items: list[dict], tools: list[dict]) -> LLMTurn:
        self.seen_inputs.append(list(input_items))
        if not self.script:
            return LLMTurn(tool_calls=[], text="(script exhausted)", raw_output_items=[{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "(script exhausted)"}]}])
        turn = self.script.pop(0)
        calls, raw = [], []
        for name, args in turn:
            self._n += 1
            cid = f"call_{self._n}"
            calls.append(ToolCall(call_id=cid, name=name, arguments=args))
            raw.append({"type": "function_call", "call_id": cid, "name": name, "arguments": json.dumps(args)})
        return LLMTurn(tool_calls=calls, raw_output_items=raw)
