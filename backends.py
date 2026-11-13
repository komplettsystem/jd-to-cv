"""Model backends. Every backend does the same two jobs: extract requirements, judge evidence.

- baseline: no model; keyword overlap. Free, deterministic, a floor to compare models against.
- claude-cli:<alias>: the local `claude` command, signed in as the user (plan usage, no API key).
- ollama:<tag>: a local model served by Ollama.
- anthropic-api:<model>: the Anthropic API, only when ANTHROPIC_API_KEY is set (paid per token).
"""
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

import match

CLAUDE_ALIASES = ("haiku", "sonnet", "opus")
API_MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

EXTRACT_PROMPT = """TASK: EXTRACT REQUIREMENTS

Below is a job posting. List what the posting asks of the candidate, as short separate requirements.

Rules:
- Write each requirement as one sentence of at most 25 words, in your own words but without adding anything the posting does not say.
- kind is "minimum" only when the posting itself calls the item required, a minimum, basic, a must, or what the team needs. Everything else is "desirable": preferred, a plus, an example of desirable experience, a skill implied by a responsibility, or an item the posting does not label.
- Leave out the company description, pay, benefits, location and legal boilerplate. Responsibilities are not requirements; include one only for the skill it clearly implies, as one short requirement.
- At most 25 requirements.

Answer with JSON only, in exactly this shape:
{{"requirements": [{{"text": "...", "kind": "minimum"}}]}}

JOB POSTING:
{posting}
"""

JUDGE_PROMPT = """TASK: JUDGE EVIDENCE

For each requirement below, decide from the numbered evidence lines (taken from one person's CV and notes) whether the evidence shows the person meets it.

Rules:
- "met": an evidence line itself shows the person did this, at the scope the requirement asks. Working in the same field, or having led people who did it, is not enough; use "partial".
- "partial": a line shows related work, a smaller scope, or part of the requirement.
- "not_on_record": nothing in the evidence shows it. Absence from the CV is not proof the person lacks the skill, so never say "no".
- Use only the evidence shown. Do not invent facts or credit the person with a team's work unless the line says they did it.
- For "met" and "partial", give the id of the single best evidence line in evidence_id. For "not_on_record", use null.
- reason: one plain sentence, at most 25 words.

Answer with JSON only, in exactly this shape:
{{"judgements": [{{"idx": 0, "verdict": "met", "evidence_id": "c3", "reason": "..."}}]}}
Include one entry for every requirement.

{blocks}
"""


def _judge_blocks(requirements, candidates):
    blocks = []
    for i, (req, cands) in enumerate(zip(requirements, candidates)):
        lines = [f"Requirement {i} [{req['kind']}]: {req['text']}"]
        if cands:
            lines += [f"  {c['id']} | {c['section']} | {c['text']}" for c in cands]
        else:
            lines.append("  (no evidence lines found)")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


class LLMBackend:
    """Wraps any `complete(prompt) -> str` function."""

    def __init__(self, complete):
        self.complete = complete

    def extract_requirements(self, posting):
        out = self.complete(EXTRACT_PROMPT.format(posting=posting))
        return match.parse_requirements(match.parse_json(out))

    def judge(self, requirements, candidates):
        out = self.complete(JUDGE_PROMPT.format(blocks=_judge_blocks(requirements, candidates)))
        return match.parse_judgements(match.parse_json(out), candidates)


class BaselineBackend:
    """Keyword overlap between the requirement and its best-ranked CV line. No model."""

    def extract_requirements(self, posting):
        return match.extract_baseline(posting)

    def judge(self, requirements, candidates):
        out = []
        for req, cands in zip(requirements, candidates):
            want = set(match.tokens(req["text"]))
            if not cands or not want:
                out.append({"verdict": "not_on_record", "chunk_id": None,
                            "reason": "Baseline: no CV line shares a word with this requirement.", "flag": None})
                continue
            best = cands[0]
            have = set(match.tokens(best["text"]))
            shared = len(want & have)
            cover = shared / len(want)
            verdict = "met" if cover >= 0.5 else "partial" if cover >= 0.25 else "not_on_record"
            out.append({"verdict": verdict, "chunk_id": best["id"] if verdict != "not_on_record" else None,
                        "reason": f"Baseline: {shared} of {len(want)} requirement words appear in this line.",
                        "flag": None})
        return out


def _claude_cli(alias):
    def complete(prompt):
        r = subprocess.run(
            ["claude", "-p", "--model", alias, "--no-session-persistence", "--tools", "", "--setting-sources", ""],
            input=prompt, capture_output=True, text=True, cwd=tempfile.gettempdir(), timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"claude exited {r.returncode}: {r.stderr.strip()[:300]}")
        return r.stdout
    return complete


def _post_json(url, payload, headers, timeout=600):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url}: {e.read()[:300].decode(errors='replace')}") from e


def _ollama(tag):
    def complete(prompt):
        data = _post_json(f"{OLLAMA_URL}/api/chat",
                          {"model": tag, "stream": False, "format": "json",
                           "messages": [{"role": "user", "content": prompt}]}, {})
        return data["message"]["content"]
    return complete


def _anthropic_api(model):
    def complete(prompt):
        data = _post_json("https://api.anthropic.com/v1/messages",
                          {"model": model, "max_tokens": 4096, "messages": [{"role": "user", "content": prompt}]},
                          {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        return data["content"][0]["text"]
    return complete


def _ollama_tags():
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2) as resp:
            return [m["name"] for m in json.loads(resp.read())["models"]], None
    except (urllib.error.URLError, OSError) as e:
        return [], f"Ollama not reachable at {OLLAMA_URL} (start it with `ollama serve`): {e}"


def list_backends():
    """Every backend the app can offer, with whether it can run right now and why not."""
    out = [{"id": "baseline", "label": "Baseline (keyword overlap, no model)", "available": True, "reason": ""}]
    has_claude = shutil.which("claude") is not None
    for alias in CLAUDE_ALIASES:
        out.append({"id": f"claude-cli:{alias}", "label": f"Claude {alias} (local claude command, plan usage)",
                    "available": has_claude, "reason": "" if has_claude else "`claude` command not found"})
    tags, why = _ollama_tags()
    for tag in tags:
        out.append({"id": f"ollama:{tag}", "label": f"Ollama {tag} (local)", "available": True, "reason": ""})
    if not tags:
        out.append({"id": "ollama:", "label": "Ollama (local models)", "available": False, "reason": why})
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    for m in API_MODELS:
        out.append({"id": f"anthropic-api:{m}", "label": f"{m} (Anthropic API, paid per token)",
                    "available": has_key, "reason": "" if has_key else "ANTHROPIC_API_KEY is not set"})
    return out


def get_backend(backend_id):
    kind, _, name = backend_id.partition(":")
    if kind == "baseline":
        return BaselineBackend()
    if kind == "claude-cli" and name in CLAUDE_ALIASES:
        return LLMBackend(_claude_cli(name))
    if kind == "ollama" and name:
        return LLMBackend(_ollama(name))
    if kind == "anthropic-api" and name in API_MODELS:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        return LLMBackend(_anthropic_api(name))
    raise ValueError(f"unknown backend {backend_id!r}")
