"""Turn a catalog entry into a Pydantic AI model. One place knows how each provider is reached."""
import json
import re
import subprocess
import tempfile

from opentelemetry import trace
from pydantic_ai import PromptedOutput
from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart, TextPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import RequestUsage

CLI_TIMEOUT = 600


def _instructions(messages):
    return next((m.instructions for m in reversed(messages) if isinstance(m, ModelRequest) and m.instructions), "")


def _clean(text):
    """The command sometimes wraps JSON in a Markdown fence even when told not to."""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())


def _flatten(messages):
    """Text the `claude` command can read: user turns and earlier answers. Instructions go in --system-prompt."""
    out = []
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, (UserPromptPart, SystemPromptPart, ToolReturnPart)):
                    out.append(p.content if isinstance(p.content, str) else json.dumps(p.content))
        elif isinstance(m, ModelResponse):
            text = "".join(p.content for p in m.parts if isinstance(p, TextPart))
            if text:
                out.append(f"[Your earlier answer]\n{text}")
    return "\n\n".join(out)


def _cli_function(alias):
    def run(messages, info):
        cmd = ["claude", "-p", "--model", alias, "--no-session-persistence", "--tools", "", "--setting-sources", "",
               "--output-format", "json"]
        # info.instructions holds the agent's instructions plus the output schema that PromptedOutput adds; the
        # instructions on the messages do not include the schema. They replace Claude Code's own system prompt.
        system = info.instructions or _instructions(messages)
        if system:
            cmd += ["--system-prompt", system]
        r = subprocess.run(
            cmd, input=_flatten(messages), capture_output=True, text=True, cwd=tempfile.gettempdir(), timeout=CLI_TIMEOUT)
        if r.returncode != 0:
            raise RuntimeError(f"claude exited {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")
        data = json.loads(r.stdout)
        if data.get("is_error"):
            raise RuntimeError(f"claude reported an error: {str(data.get('result'))[:300]}")
        u = data.get("usage", {})
        trace.get_current_span().set_attribute("match.cost_usd", float(data.get("total_cost_usd", 0.0)))
        return ModelResponse(
            parts=[TextPart(_clean(data["result"]))],
            usage=RequestUsage(input_tokens=u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                               + u.get("cache_creation_input_tokens", 0), output_tokens=u.get("output_tokens", 0)))
    return run


def build_model(entry, settings):
    p, name = entry["provider"], entry["model_name"]
    if p == "anthropic":
        return AnthropicModel(name, provider=AnthropicProvider(api_key=settings.anthropic_api_key))
    if p == "openrouter":
        return OpenRouterModel(name, provider=OpenRouterProvider(api_key=settings.openrouter_api_key,
                                                                  app_title="Reverse Apply"))
    if p == "bedrock":
        return BedrockConverseModel(name, provider=BedrockProvider(region_name=settings.aws_region,
                                                                    profile_name=settings.aws_profile or None))
    if p == "ollama":
        return OpenAIChatModel(name, provider=OllamaProvider(base_url=f"{settings.ollama_url}/v1"))
    if p == "cli":
        return FunctionModel(_cli_function(name), model_name=f"claude-cli:{name}")
    raise ValueError(f"no model for provider {p!r}")


def output_spec(entry, output_type):
    """Tool-calling output for API models, a prompted JSON answer for models that cannot call tools."""
    return PromptedOutput(output_type) if entry["output_mode"] == "prompted" else output_type
