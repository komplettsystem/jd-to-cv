"""What a retry sends back to the model. A retry replays the earlier turns, and through OpenRouter the provider rejects the model's
earlier thinking block ("Invalid `signature` in `thinking` block"): one such rejection took a whole overall reading down in
production. Only the visible parts of the earlier turns are replayed."""
from dataclasses import replace

from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import ModelResponse, ThinkingPart


def drop_thinking(messages):
    out = []
    for message in messages:
        if isinstance(message, ModelResponse):
            parts = [p for p in message.parts if not isinstance(p, ThinkingPart)]
            if not parts:
                continue
            if len(parts) != len(message.parts):
                message = replace(message, parts=parts)
        out.append(message)
    return out


CAPABILITY = ProcessHistory(drop_thinking)
