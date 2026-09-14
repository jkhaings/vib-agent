"""Lightweight fakes for the Anthropic Python SDK's Message shape, used to
exercise agent/loop.py without any real API calls. Only the attributes
loop.py actually reads are implemented (content blocks' .type/.text/.id/
.name/.input, .stop_reason, .usage.model_dump()) -- not a full SDK
reimplementation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50

    def model_dump(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


@dataclass
class FakeMessage:
    content: list[Any]
    stop_reason: str
    usage: FakeUsage = field(default_factory=FakeUsage)


class FakeMessagesResource:
    def __init__(self, responses: list[FakeMessage]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeMessage:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeMessagesResource ran out of canned responses")
        return self._responses.pop(0)


class FakeAnthropicClient:
    def __init__(self, responses: list[FakeMessage]) -> None:
        self.messages = FakeMessagesResource(responses)


def build_consistent_echo(result: Any) -> str:
    """The exact echo payload a fully-consistent draft would emit for the
    given AnalysisResult -- mirrors agent/consistency.py's own extraction so
    tests can assert the happy path without hand-duplicating field logic."""
    faults = [
        {"fault": f.fault, "confidence": f.confidence}
        for f in result.findings
        if f.fault != "no_significant_findings"
    ]
    measurements = [m.technique for m in result.recommended_measurements]
    zone = result.iso.iso_zone if result.iso else None
    payload = {"zone": zone, "faults": faults, "recommended_measurements": measurements}
    return f"<<<ECHO_START>>>\n{json.dumps(payload)}\n<<<ECHO_END>>>"


def draft_message(narrative: str, echo_json: str) -> FakeMessage:
    return FakeMessage(content=[FakeTextBlock(text=f"{narrative}\n\n{echo_json}")], stop_reason="end_turn")
