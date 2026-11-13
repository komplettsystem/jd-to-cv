"""The list of models the app can use, kept in the database so the admin can switch them on and off.

Defaults are a snapshot (2026-11-11). "Refresh" in the admin calls OpenRouter's public /models endpoint (no key
needed) and Bedrock's list-inference-profiles (needs a valid AWS login), and Ollama's /api/tags. Ids look like
`provider:model_name`."""
import json
import re
import shutil
import time
import urllib.error
import urllib.request

BASELINE = {"id": "baseline", "provider": "baseline", "model_name": "baseline",
            "label": "Baseline (keyword overlap, no model)", "output_mode": "tool", "tools": 0, "local_only": 0}

# Development only: the local `claude` command, billed to a plan, never to a card.
CLI = [{"id": f"cli:{a}", "provider": "cli", "model_name": a, "label": f"Claude {a} (local `claude` command, plan usage)",
        "output_mode": "prompted", "tools": 0, "local_only": 1} for a in ("haiku", "sonnet")]

# The default shortlist (see docs/MODELS.md): models with a good price for the quality they give on the public
# leaderboards, from any maker; your own evals decide which one to use. Results need to be okay, not great;
# the spend caps do the rest. Bedrock ids and prices are defaults and must be confirmed with "Refresh from Bedrock" once
# an AWS login works: many regions need an inference-profile id (the eu. prefix for the EU).
# (id, label, output mode, USD per million tokens in, out, reasoning effort, where the price came from)
BEDROCK_DEFAULTS = [
    ("amazon.nova-micro-v1:0", "Amazon Nova Micro", "tool", 0.035, 0.14, "", "openrouter.ai list, 2026-11-12"),
    ("amazon.nova-lite-v1:0", "Amazon Nova Lite", "tool", 0.06, 0.24, "", "openrouter.ai list, 2026-11-12"),
    ("eu.amazon.nova-2-lite-v1:0", "Amazon Nova 2 Lite", "tool", 0.374, 3.157, "low",
     "third-party listing of the EU price, AWS page not readable, confirm in the console"),
    ("openai.gpt-6.1-terra", "OpenAI GPT-6.1 Terra", "tool", 1.10, 5.50, "medium",
     "third-party listing, AWS announced it 2026-10-26, id and price unconfirmed"),
]

# (id at OpenRouter, label, context, USD per input token, USD per output token, output mode, reasoning effort),
# from openrouter.ai/api/v1/models on 2026-11-12. Prices refresh with "Refresh from OpenRouter".
OPENROUTER_DEFAULTS = [
    ("openai/gpt-6.1-terra", "OpenAI GPT-6.1 Terra", 1050000, 0.000001, 0.000005, "tool", "medium"),
    ("anthropic/claude-haiku-5.5", "Claude Haiku 5.5", 1000000, 0.0000001, 0.0000005, "tool", "high"),
    ("nvidia/nemotron-3-super-120b-a12b", "NVIDIA Nemotron 3 Super 120B", 262144, 0.000000075, 0.0000004, "prompted", ""),
    ("nvidia/nemotron-3.5-super-vl", "NVIDIA Nemotron 3.5 Super VL", 1048576, 0.00000015, 0.00000047, "tool", ""),
    # More makers, for comparison in the evals.
    ("openai/gpt-6.1-sol", "OpenAI GPT-6.1 Sol", 1050000, 0.000002, 0.00001, "tool", "low"),
    ("meta/muse-ember", "Meta Muse Ember", 1048576, 0.0000001, 0.0000005, "prompted", ""),
    ("deepseek/deepseek-v4.1-flash-1017", "DeepSeek V4.1 Flash", 1048576, 0.0000003, 0.0000012, "tool", "high"),
    ("xiaomi/mimo-v2.6-pro", "Xiaomi MiMo V2.6 Pro", 1050000, 0.000000435, 0.00000087, "tool", ""),
]
OPENROUTER_FAMILIES = ("nvidia/", "amazon/", "anthropic/claude-haiku", "anthropic/claude-sonnet-5", "openai/gpt-6",
                       "x-ai/grok-4", "google/gemini-3.5-flash-lite", "google/gemini-4", "meta/muse", "deepseek/deepseek-v4",
                       "xiaomi/mimo", "qwen/qwen3.8", "z-ai/glm-5.3", "minimax/minimax-m3")

# Models whose provider refuses a forced tool call ("tool_choice required is not supported"): they answer in prompted JSON instead.
NO_FORCED_TOOLS = ("meta/muse", "qwen/")


def prompted_only(model_name):
    return any(model_name.startswith(p) for p in NO_FORCED_TOOLS)


# Never offered, never synced: removed by decision.
EXCLUDED = ("fable",)
CATALOG_VERSION = 3


def default_rows():
    rows = [BASELINE, *CLI]
    for mid, label, mode, pin, pout, effort, src in BEDROCK_DEFAULTS:
        rows.append({"id": f"bedrock:{mid}", "provider": "bedrock", "model_name": mid, "label": f"{label} (AWS Bedrock)",
                     "output_mode": mode, "tools": 1, "local_only": 0, "price_in": pin / 1e6, "price_out": pout / 1e6,
                     "effort": effort, "source": f"default, not yet confirmed with Bedrock; price: {src}"})
    for mid, label, ctx, pin, pout, mode, effort in OPENROUTER_DEFAULTS:
        rows.append({"id": f"openrouter:{mid}", "provider": "openrouter", "model_name": mid,
                     "label": f"{label} (OpenRouter)", "output_mode": mode, "tools": 1, "local_only": 0,
                     "context": ctx, "price_in": pin, "price_out": pout, "effort": effort,
                     "source": "snapshot 2026-11-12"})
    return rows


def excluded(model_name):
    return any(x in model_name.lower() for x in EXCLUDED)


def seed(db):
    """First start, or a new catalog version: put the shortlist in and drop what was excluded. Later starts leave
    the admin's on/off choices alone."""
    if db.get_setting("catalog_version") != str(CATALOG_VERSION):
        db.run("DELETE FROM catalog")
        db.upsert_catalog(default_rows())
        db.set_setting("catalog_version", str(CATALOG_VERSION))
        for key in ("public_model", "chat_model", "judge_model"):
            if excluded(db.get_setting(key)):
                db.set_setting(key, "")
    elif not db.list_catalog():
        db.upsert_catalog(default_rows())
    for prefix in NO_FORCED_TOOLS:   # also for rows stored before this was known; keeps the admin's on/off choices
        db.run("UPDATE catalog SET output_mode = 'prompted' WHERE provider = 'openrouter' AND model_name LIKE ?", (prefix + "%",))


def sync_openrouter(db, timeout=15):
    """Refresh prices and add the models of the main families from OpenRouter's public list. Returns the count."""
    with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=timeout) as resp:
        data = json.loads(resp.read())["data"]
    rows = []
    for m in data:
        mid = m["id"]
        if ":" in mid or mid.startswith("~") or not mid.startswith(OPENROUTER_FAMILIES) or excluded(mid):
            continue
        p = m.get("pricing", {})
        try:
            pin, pout = float(p["prompt"]), float(p["completion"])
        except (KeyError, ValueError, TypeError):
            continue
        if pin < 0 or pout < 0:   # "price varies" is listed as -1: no fixed price, so the caps could not count it
            continue
        rows.append({"id": f"openrouter:{mid}", "provider": "openrouter", "model_name": mid,
                     "label": f"{m.get('name', mid)} (OpenRouter)",
                     "output_mode": "prompted" if mid.startswith("nvidia/") or prompted_only(mid) else "tool", "tools": 1, "enabled": 0, "context": m.get("context_length"), "price_in": pin, "price_out": pout,
                     "source": "openrouter.ai/api/v1/models"})
    db.upsert_catalog(rows)
    return len(rows)


BEDROCK_PROVIDERS = ("Amazon", "NVIDIA", "OpenAI")


def sync_bedrock(db, settings):
    """Add Amazon and NVIDIA text models, plus Claude Haiku and OpenAI GPT-6 models, that this AWS account can call (needs `aws login` or a
    role). Inference profiles are listed first because many regions need them; plain on-demand models follow."""
    import boto3
    session = boto3.Session(profile_name=settings.aws_profile or None, region_name=settings.aws_region)
    client = session.client("bedrock")
    wanted = ("amazon.nova", "nvidia.", "anthropic.claude-haiku", "openai.gpt-6")
    rows, seen = [], set()
    for p in client.list_inference_profiles(maxResults=100)["inferenceProfileSummaries"]:
        pid = p["inferenceProfileId"]
        base = pid.split(".", 1)[1] if pid.split(".", 1)[0] in ("eu", "us", "apac", "global", "us-gov") else pid
        if base.startswith(wanted) and not excluded(pid):
            seen.add(pid)
            rows.append({"id": f"bedrock:{pid}", "provider": "bedrock", "model_name": pid,
                         "label": f"{p.get('inferenceProfileName', pid)} (Bedrock profile, {settings.aws_region})",
                         "output_mode": "prompted" if base.startswith("nvidia.") else "tool", "tools": 1, "enabled": 0,
                         "source": f"bedrock {settings.aws_region}"})
    for m in client.list_foundation_models(byOutputModality="TEXT")["modelSummaries"]:
        mid = m["modelId"]
        if (m.get("providerName") in BEDROCK_PROVIDERS or mid.startswith("anthropic.claude-haiku") or mid.startswith("openai.gpt-6")) \
                and mid.startswith(wanted) and "ON_DEMAND" in m.get("inferenceTypesSupported", []) \
                and mid not in seen and not excluded(mid):
            rows.append({"id": f"bedrock:{mid}", "provider": "bedrock", "model_name": mid,
                         "label": f"{m.get('modelName', mid)} (Bedrock, {settings.aws_region})",
                         "output_mode": "prompted" if mid.startswith("nvidia.") else "tool", "tools": 1, "enabled": 0,
                         "source": f"bedrock {settings.aws_region}"})
    db.upsert_catalog(rows)
    return len(rows)


_ollama_cache = {"at": 0.0, "tags": []}


def ollama_tags(url):
    if time.time() - _ollama_cache["at"] < 10:
        return _ollama_cache["tags"]
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=2) as resp:
            tags = [m["name"] for m in json.loads(resp.read())["models"]]
    except (urllib.error.URLError, OSError):
        tags = []
    _ollama_cache.update(at=time.time(), tags=tags)
    return tags


def ollama_has_tools(url, name):
    """Whether Ollama lists "tools" among the model's capabilities (POST /api/show answers with a `capabilities` list). False when the
    list is missing (an older Ollama) or the lookup fails: a model is not marked as able to call tools on a guess."""
    try:
        req = urllib.request.Request(f"{url}/api/show", data=json.dumps({"model": name}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return "tools" in (json.loads(resp.read()).get("capabilities") or [])
    except (urllib.error.URLError, OSError, ValueError):
        return False


def sync_ollama(db, settings):
    rows = [{"id": f"ollama:{t}", "provider": "ollama", "model_name": t, "label": f"Ollama {t} (local)",
             "output_mode": "prompted", "tools": int(ollama_has_tools(settings.ollama_url, t)), "local_only": 1, "source": settings.ollama_url}
            for t in ollama_tags(settings.ollama_url)]
    db.upsert_catalog(rows)
    return len(rows)


NO_TOOLS = "No tool calls: the chat answers from fewer CV lines and cannot ask the matcher or the scoring service."
_GENERIC = {"instruct", "chat", "latest", "preview", "free", "local", "base"}


def _words(entry):
    """The name words that identify a maker or family in a model name ("anthropic/claude-haiku-5.5" gives anthropic, claude, haiku)."""
    return {w for w in re.findall(r"[a-z]+", entry["model_name"].lower()) if len(w) >= 4 and w not in _GENERIC}


def tool_alternatives(rows, n=2):
    """For each model without tool calls, up to `n` catalog models that have them: those that share a word of its name come first (the
    same maker or family), then the cheapest, models switched on before models switched off. A model whose price varies is left out."""
    def price(m):
        p = m.get("price_in")
        return float("inf") if p is None else p
    pool = [m for m in rows if m["tools"] and m["provider"] != "baseline" and (m.get("price_in") is None or m["price_in"] >= 0)]
    out = {}
    for m in rows:
        if m["tools"]:
            continue
        own = _words(m)
        out[m["id"]] = sorted(pool, key=lambda c: (not (own & _words(c)), not c["enabled"], price(c)))[:n]
    return out


AWS_CHECK_SECONDS = 60
_aws_cache = {}   # (profile, region) -> (checked at, (usable, reason))


def _aws_state(settings):
    """Whether this machine has AWS credentials. Looking is slow without them (the AWS library tries every source, about two
    seconds), and the admin pages ask once per Bedrock model on every load, so the answer is kept for a minute."""
    key = (settings.aws_profile, settings.aws_region)
    hit = _aws_cache.get(key)
    if hit and time.time() - hit[0] < AWS_CHECK_SECONDS:
        return hit[1]
    try:
        import boto3
        creds = boto3.Session(profile_name=settings.aws_profile or None,
                              region_name=settings.aws_region).get_credentials()
        state = (True, "") if creds else (False, "no AWS credentials (run `aws login`)")
    except Exception as e:  # a bad profile name etc.; shown to the admin as the reason
        state = (False, f"AWS configuration problem: {e}")
    _aws_cache[key] = (time.time(), state)
    return state


def bedrock_configured(settings):
    """True when this server has AWS credentials, so Bedrock models can run at all."""
    return _aws_state(settings)[0]


def availability(entry, settings):
    """(usable now, reason it is not)."""
    p = entry["provider"]
    if p == "baseline":
        return True, ""
    if p == "cli":
        return (True, "") if shutil.which("claude") else (False, "`claude` command not found")
    if p == "anthropic":
        return (True, "") if settings.anthropic_api_key else (False, "ANTHROPIC_API_KEY is not set")
    if p == "openrouter":
        return (True, "") if settings.openrouter_api_key else (False, "OPENROUTER_API_KEY is not set")
    if p == "bedrock":
        return _aws_state(settings)
    if p == "ollama":
        return (True, "") if entry["model_name"] in ollama_tags(settings.ollama_url) else (False, "Ollama is not running")
    return False, f"unknown provider {p}"


def usable(db, settings, include_disabled=False):
    """Catalog rows with `available` and `reason` added, for forms and the models page."""
    out = []
    for e in db.list_catalog():
        if not e["enabled"] and not include_disabled:
            continue
        e["available"], e["reason"] = availability(e, settings)
        out.append(e)
    return out
