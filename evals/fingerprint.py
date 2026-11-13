"""What an eval run ran with, recorded on the run so that a change in its result can be traced to a cause.

Hashes, not texts: two runs with the same judge-rules hash used the same judge wording. `code` is the commit the app was
deployed from (the deploy script writes it to a VERSION file); "dev" when the app runs from a checkout without one."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _h(text):
    return hashlib.sha1(str(text).encode()).hexdigest()[:8]


def code_version():
    try:
        return (ROOT / "VERSION").read_text().strip() or "dev"
    except OSError:
        return "dev"


def current(entry, spec):
    """The settings in force right now for `entry` (a catalog row) and the dataset `spec`."""
    from llm import agents, chat
    import match
    cases = getattr(spec, "fingerprint_source", lambda: spec.name)()
    return {"model": entry.get("model_name") or entry["id"], "effort": entry.get("effort") or "", "output_mode": entry.get("output_mode") or "",
            "max_tokens": agents.SETTINGS["max_tokens"], "whole_cv_chars": agents.WHOLE_CV_CHARS,
            "equivalent_experience_lines": bool(match.EQUIVALENT.pattern.startswith("\\b(?:equivalent")),
            "judge_rules": _h(agents.JUDGE_RULES), "chat_rules": _h(chat.INSTRUCTIONS_WITH_ENGINE), "chat_matcher_calls_max": chat.MAX_MATCHER_CALLS, "extract_rules": _h(agents.EXTRACT_RULES),
            "dataset": _h(json.dumps(cases, sort_keys=True, default=str)), "code": code_version()}
