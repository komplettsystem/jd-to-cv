"""Cover letter drafts: the rules, and the checks that run on every draft in code.

The model writes paragraphs and cites the CV lines each one rests on. These checks then decide whether the
draft may be shown as clean. Nothing here sends anything."""
import re

import match

MAX_WORDS = 320
BANNED = ("leverage", "seamless", "seamlessly", "robust", "delve", "unlock", "paradigm", "tapestry",
          "game-changing", "synergy", "cutting-edge", "state-of-the-art", "holistic", "load-bearing")
CLAIM_IN_QUESTION = re.compile(
    r"\bI (led|built|own|owned|ran|run|shipped|designed|wrote|launched|managed|drove|created)\b|\bmy (team|work|experience)\b", re.I)
GAP_PATTERNS = (r"\bnew to me\b", r"\bi am new to\b", r"\bi'm new to\b", r"\bi have not (yet )?worked\b",
                r"\bsteep learning curve\b", r"\bdespite (my )?lack of\b")

# Default rules for the letters. Neither default opens with a question. The owner can replace either in the admin settings.
DEFAULT_RULES_RECRUITER = """Voice and structure:
- Open with the single most relevant proof for this role: one cited CV line, stated at the scope the line states, in the posting's own terms. Do not open with a question, with flattery, or with "I am excited".
- Then one or two more pieces of proof, each from a cited CV line. Do not turn a team's work into personal work unless the line says it was the person's own.
- Never mention what is new to the writer or what they lack. Do not address a requirement that is marked not_on_record.
- One question about the role is welcome after the proof, where it helps; never as the opening.
- End with a plain offer to talk. No sign-off flourish.
- Plain and direct, the way a person would say it to a colleague. Under 300 words, no buzzwords, at most one dash in the whole letter."""

DEFAULT_RULES_SEEKER = """Voice and structure:
- Open with the single most relevant proof for this role: one cited CV line, stated at the scope the line states, in the posting's own terms. Do not open with a question, with flattery, or with "I am excited".
- Then one or two more pieces of proof, each from a cited CV line. Say only what the cited lines say: do not turn a team's work into personal work unless the line says it was the person's own.
- Leave out requirements marked not_on_record rather than apologise for them, and do not write about what the writer lacks.
- Where a question about the role helps, ask it after the proof, never as the opening.
- End with a plain offer to talk.
- Plain and professional. Under 300 words, no buzzwords, at most one dash in the whole letter."""

DEFAULT_RULES = DEFAULT_RULES_RECRUITER


# Three ways to open, to be compared blind by the owner (see the letter arena in the admin). "proof" is the default.
VARIANTS = {
    "proof": DEFAULT_RULES_RECRUITER,
    "question": """Voice and structure:
- Open with the method: how you would look at this role's problem, as one or two concrete questions you would ask (a question paragraph). Do not open with a claim about yourself, with flattery, or with "I am excited".
- Then one or two pieces of proof, each taken from a cited CV line, stated at the scope the line states. Do not turn a team's work into personal work unless the line says it was the person's own.
- Never mention what is new to the writer or what they lack. Do not address a requirement that is marked not_on_record, except as one of the questions you would ask.
- End with a plain offer to talk. No sign-off flourish.
- Under 300 words, plain sentences, no buzzwords, at most one dash in the whole letter.""",
    "problem": """Voice and structure:
- Open the first body paragraph with the role's main problem in the posting's own words, as one plain sentence, and follow it at once with the cited CV line that speaks to it. Do not open with a question, with flattery, or with "I am excited".
- Then one more piece of proof, from a cited CV line, stated at the scope the line states. Do not turn a team's work into personal work unless the line says it was the person's own.
- Never mention what is new to the writer or what they lack. Do not address a requirement that is marked not_on_record.
- End with a plain offer to talk. No sign-off flourish.
- Plain and direct, the way a person would say it to a colleague. Under 300 words, no buzzwords, at most one dash in the whole letter.""",
}
VARIANT_LABELS = {"proof": "Proof first", "question": "Question first", "problem": "Problem first"}


# A place in a CV section title is where a job was located, not where the person lives; a model once wrote "I am based in Paris / Milan" from one.
LIVES_IN = re.compile(r"\b(?:I(?:'m| am)(?: currently)? (?:based|located|living)|I live|I(?:'m| am) from|based in|living in)\b[^.!?\n]{0,40}", re.I)


def default_rules(door):
    return DEFAULT_RULES_SEEKER if door == "seeker" else DEFAULT_RULES_RECRUITER


def words(text):
    return len(re.findall(r"\b[\w'-]+\b", text))


def render(paragraphs):
    return "\n\n".join(p["text"].strip() for p in paragraphs)


def check_letter(paragraphs, chunks_by_id, items, max_words=MAX_WORDS):
    """errors make the draft unusable until fixed; warnings are for the reader to judge."""
    text = render(paragraphs)
    n = words(text)
    errors, warnings = [], []
    if n > max_words:
        errors.append(f"{n} words, over the limit of {max_words}")
    hits = sorted({b for b in BANNED if re.search(rf"\b{re.escape(b)}\b", text, re.I)})
    if hits:
        errors.append("banned words: " + ", ".join(hits))
    for pat in GAP_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            errors.append(f"gap line: \"{m.group(0)}\"")
    for i, p in enumerate(paragraphs):
        ids = p.get("evidence_ids") or []
        bad = [e for e in ids if e not in chunks_by_id]
        if bad:
            errors.append(f"paragraph {i + 1} cites lines that do not exist: {', '.join(bad)}")
        if p.get("kind") == "body" and not ids:
            errors.append(f"paragraph {i + 1} states things with no CV line behind it")
        if p.get("kind") == "question" and CLAIM_IN_QUESTION.search(p["text"]):
            errors.append(f"paragraph {i + 1} is a question paragraph but makes a claim about the writer")
    where = LIVES_IN.search(text)
    if where:
        errors.append(f"says where the writer lives or is based (\"{where.group(0)}\"), which no CV line states")
    dashes = len(re.findall(r"[—–]| - ", text))
    if n and dashes / n * 100 > 1.0:
        warnings.append(f"{dashes} dashes in {n} words")
    lowered = set(match.tokens(text))
    for it in items:
        if match.effective_verdict(it) != "not_on_record":
            continue
        want = set(match.tokens(it["text"]))
        if len(want) >= 3 and len(want & lowered) / len(want) >= 0.6:
            warnings.append(f"touches a requirement that is not shown: {it['text'][:90]}")
    return {"words": n, "errors": errors, "warnings": warnings, "ok": not errors}
