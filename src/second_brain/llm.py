"""One small seam for the model, so tests and the demo can swap in a scripted one."""

import json
import logging
from typing import Protocol

from openai import OpenAI

log = logging.getLogger("second_brain")


class Model(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class OpenRouterModel:
    def __init__(self, api_key: str, model: str, base_url: str):
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def complete(self, system: str, user: str) -> str:
        reply = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        if reply.usage:
            log.info("model=%s tokens=%s", self.model, reply.usage.total_tokens)
        return reply.choices[0].message.content or ""


class ScriptedModel:
    """Returns canned replies in order and records what it was asked."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self.replies:
            raise RuntimeError("ScriptedModel has no replies left")
        return self.replies.pop(0)


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a reply, tolerating code fences and chatter."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("model reply contained no JSON object")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model reply was not a JSON object")
    return data
