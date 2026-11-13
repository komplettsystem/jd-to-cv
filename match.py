"""Matching a job posting against a CV: requirements, cited evidence, verdicts, score.

A backend (see backends.py) extracts requirements from the posting and judges each one
against candidate CV lines. Everything else (chunking, ranking, parsing, scoring) lives here
and is the same for every backend, so results from different models are comparable.
"""
import json
import math
import re
from collections import Counter

VERDICTS = ("met", "partial", "not_on_record")
VALUE = {"met": 1.0, "partial": 0.5, "not_on_record": 0.0}
KINDS = ("minimum", "desirable")
DEFAULT_WEIGHT = {"minimum": 2, "desirable": 1}
MAX_REQUIREMENTS = 30
POSTING_LIMIT = 20000

EMAIL = re.compile(r"\S+@\S+\.\S+")


def strip_comments(md):
    """The master CV starts with an HTML comment of internal notes. Models and share links never see it."""
    return re.sub(r"<!--.*?-->", "", md, flags=re.S)


SENTENCE_END = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\u201c(])')
ABBREVIATIONS = ("e.g.", "i.e.", "vs.", "etc.", "approx.", "u.s.", "st.", "dr.", "mr.", "ms.")
SPLIT_FROM = 220    # a prose paragraph up to this many characters is kept whole
MIN_SENTENCE = 40   # a shorter sentence joins its neighbour


def sentences(text):
    """A long prose paragraph (a profile) as sentences, so that a citation points at the sentence that carries the evidence and the quote
    shown to the reader is the one the reason is about. Short paragraphs stay whole; a very short sentence joins the one before it."""
    if len(text) <= SPLIT_FROM:
        return [text]
    glued = []
    for part in SENTENCE_END.split(text):
        if glued and glued[-1].lower().endswith(ABBREVIATIONS):   # "e.g. C2PA" is not the end of a sentence
            glued[-1] += " " + part
        else:
            glued.append(part)
    out = []
    for part in glued:
        if out and len(part) < MIN_SENTENCE:
            out[-1] += " " + part
        else:
            out.append(part)
    if len(out) > 1 and len(out[0]) < MIN_SENTENCE:
        out[1] = out[0] + " " + out[1]
        out.pop(0)
    return out


def _chunks(md, prefix, default_section):
    """CV lines: a bullet is one line however long (it is one claim); prose is cut into sentences when it is long."""
    chunks = []
    section = default_section
    para = []
    bullet = [False]

    def flush():
        text = " ".join(para).replace("**", "").strip()
        para.clear()
        was_bullet, bullet[0] = bullet[0], False
        if len(text) >= 20 and not EMAIL.search(text):
            for piece in ([text] if was_bullet else sentences(text)):
                chunks.append({"id": f"{prefix}{len(chunks)}", "section": section, "text": piece})

    for line in md.splitlines():
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("#"):
            flush()
            section = s.lstrip("#").strip()
        elif s[:2] in ("- ", "* ") or s.startswith("• "):
            flush()
            bullet[0] = True
            para.append(s[2:].strip())
        else:
            para.append(s)
    flush()
    return chunks


def chunk_cv(md):
    return _chunks(md, "c", "CV")


def chunk_extra(text):
    return _chunks(text, "x", "Extra facts")


STOP = set(
    "a an and are as at be by for from has have in into is it its of on or that the their this to "
    "with you your we our will can who what which about across also more other such than then they "
    "them these those up out over per via i my me been being was were do does did not no so if but "
    "any all each both able".split()
)


def tokens(text):
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9+#.\-]*", text.lower()):
        w = w.rstrip(".-")
        if w in STOP or len(w) < 2:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if w.endswith(suf) and len(w) - len(suf) >= 4:
                w = w[: -len(suf)]
                break
        out.append(w)
    return out


# How many CV lines the judge sees for each requirement. The public CV holds several wordings of the same
# work, so the best line is not always in the first three (it was found again in 59 of 68 cases at 3, 68 of 68 at 5).
EVIDENCE_K = 5


def rank_evidence(requirement, chunks, k=EVIDENCE_K):
    """TF-IDF cosine between the requirement and each chunk. Returns up to k chunks with score > 0."""
    docs = [Counter(tokens(c["text"])) for c in chunks]
    n = len(docs)
    df = Counter()
    for d in docs:
        df.update(d.keys())
    idf = {t: math.log((n + 1) / (df[t] + 1)) + 1 for t in df}
    q = Counter(tokens(requirement))
    qvec = {t: c * idf.get(t, 0.0) for t, c in q.items()}
    qnorm = math.sqrt(sum(v * v for v in qvec.values()))
    scored = []
    for chunk, d in zip(chunks, docs):
        dvec = {t: c * idf[t] for t, c in d.items()}
        dnorm = math.sqrt(sum(v * v for v in dvec.values()))
        dot = sum(v * dvec.get(t, 0.0) for t, v in qvec.items())
        if dot > 0 and qnorm and dnorm:
            scored.append({**chunk, "score": dot / (qnorm * dnorm)})
    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored[:k]


# A requirement the posting itself lets "equivalent experience" satisfy (usually a degree). The judge reads it on the years of
# work the CV shows, so the CV's profile line, which usually states them, must be among the lines it is offered.
EQUIVALENT = re.compile(r"\b(?:equivalent|comparable)\s+(?:(?:practical|professional|work|industry|relevant|hands-on)\s+)*(?:experience|knowledge)", re.I)
PROFILE_SECTION = re.compile(r"profile|summary|about", re.I)


def candidates_for(requirement, chunks, k=EVIDENCE_K):
    """The CV lines offered to the judge for one requirement: the k best word matches, and for an "or equivalent experience"
    requirement also the profile or summary lines (or, without one, the first two lines that state a number of years)."""
    found = rank_evidence(requirement, chunks, k)
    if not EQUIVALENT.search(requirement):
        return found
    extra = [c for c in chunks if PROFILE_SECTION.search(c["section"])][:2] or \
            [c for c in chunks if re.search(r"\b\d+\+?\s+years|\b(?:ten|fifteen|twenty)\s+years", c["text"], re.I)][:2]
    have = {c["id"] for c in found}
    return found + [c for c in extra if c["id"] not in have]


# --- baseline requirement extraction (no model) -------------------------------------------

# Most specific first. Matched case-sensitively inside running text (a posting pasted as one
# line) and case-insensitively when a short line is only the heading.
_MARKERS = [
    ("des", r"Preferred Qualifications|Nice[- ]to[- ]Haves?|Ways [Tt]o Stand Out|"
            r"Examples of Desirable Skills(?:, Knowledge,? and Experience)?|Desirable Skills|Bonus Points|Preferred"),
    ("min", r"Minimum (?:Basic )?(?:Qualifications|Requirements)|Basic Qualifications|Required Qualifications|"
            r"What We Need|What You(?:'ll| Will) Need|What You Bring|Must[- ]Haves?|Requirements|Qualifications"),
    ("off", r"What You(?:'ll| Will) Do|Responsibilities|About (?:Us|the Role|the Team|You)|Compensation|Benefits|"
            r"Salary|Equal Opportunity|What Makes \w+ Special|Your Mission|Pay Range"),
]
_GROUPS = {f"g{i}": mode for i, (mode, _) in enumerate(_MARKERS)}
_MARKER_RE = re.compile("|".join(f"(?P<g{i}>{p})" for i, (_, p) in enumerate(_MARKERS)))
_HEADING_RE = re.compile(_MARKER_RE.pattern, re.I)
_CUES = re.compile(
    r"experience|proven|ability|understanding|knowledge|skills|fluency|record of|hands-on|years|"
    r"background|familiar|proficien", re.I)


def _mode_of(match_obj):
    return _GROUPS[match_obj.lastgroup]


def _sentences(line):
    bullet = line.lstrip().startswith(("-", "*", "•"))
    line = line.lstrip("-*• \t")
    if bullet:
        return [line.strip()]
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+(?=[A-Z0-9])", line) if s.strip()]


def extract_baseline(posting):
    text = posting.replace("’", "'")
    text = _MARKER_RE.sub(lambda m: f"\n§{_mode_of(m)}§\n", text)
    mode = None
    found = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("§") and line.endswith("§"):
            mode = line.strip("§")
            continue
        head = re.fullmatch(r"(?:" + _HEADING_RE.pattern + r")\s*:?", line, re.I)
        if len(line) <= 60 and head:
            mode = _mode_of(head)
            continue
        if mode in ("min", "des"):
            kind = "minimum" if mode == "min" else "desirable"
            for s in _sentences(line):
                if 30 <= len(s) <= 400:
                    found.append({"text": s, "kind": kind})
    if not found:
        for s in re.split(r"\n|(?<=[.!?;])\s+(?=[A-Z0-9])", text):
            s = s.strip().lstrip("-*• ")
            if 30 <= len(s) <= 400 and _CUES.search(s) and not s.startswith("§"):
                found.append({"text": s, "kind": "desirable"})
    seen, out = set(), []
    for r in found:
        if r["text"] not in seen:
            seen.add(r["text"])
            out.append(r)
    return out[:MAX_REQUIREMENTS]


# --- parsing model output -----------------------------------------------------------------

def parse_json(text):
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for m in re.finditer(r"[\[{]", s):
        try:
            obj, _ = decoder.raw_decode(s[m.start():])
            return obj
        except json.JSONDecodeError:
            continue
    raise ValueError(f"no JSON in model output: {text[:200]!r}")


_KIND_WORDS = {"minimum": "minimum", "required": "minimum", "must": "minimum", "basic": "minimum",
               "desirable": "desirable", "preferred": "desirable", "nice": "desirable", "plus": "desirable"}


def parse_requirements(obj):
    rows = obj.get("requirements") if isinstance(obj, dict) else obj
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"model returned no requirements list: {str(obj)[:200]!r}")
    out = []
    for r in rows[:MAX_REQUIREMENTS]:
        text = (r.get("text") or "").strip() if isinstance(r, dict) else ""
        kind_raw = str(r.get("kind", "")).strip().lower() if isinstance(r, dict) else ""
        kind = _KIND_WORDS.get(kind_raw.split()[0]) if kind_raw else None
        if not text or kind is None:
            raise ValueError(f"bad requirement entry: {r!r}")
        out.append({"text": text, "kind": kind})
    return out


def strip_ids(text, ids):
    """Take the app's own numbers for CV lines (c10 and so on) out of a sentence a model wrote. They belong in the id fields
    only: a reader cannot know what "shown in c10" means. Only the given ids are touched. Em dashes (and spaced en dashes) the
    model used as a pause become commas: this text is read as the owner's own writing, and the dash reads as machine-made."""
    for cid in sorted(ids, key=len, reverse=True):
        c = re.escape(cid)
        text = re.sub(rf"\s*[(\[]\s*{c}\s*[)\]]", "", text, flags=re.I)                                  # "(c10)" goes
        text = re.sub(rf"\b(?:the\s+)?(?:CV\s+)?(?:(?:line|lines|evidence|id)\s+)?{c}\b", "the CV line", text, flags=re.I)
    text = re.sub(r"(^|[.!?]\s+)the CV line", lambda m: m.group(1) + "The CV line", text)
    text = re.sub(r"\s*\u2014\s*|\s+\u2013\s+", ", ", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


UNVERIFIED_REASON = "The CV line first cited could not be verified, so this counts as not shown."


def parse_judgements(obj, candidates):
    """candidates[i] is the list of chunks offered for requirement i. Returns one judgement per requirement."""
    rows = obj.get("judgements") if isinstance(obj, dict) else obj
    if not isinstance(rows, list):
        raise ValueError(f"model returned no judgements list: {str(obj)[:200]!r}")
    by_idx = {}
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get("idx"), int):
            by_idx[r["idx"]] = r
    all_ids = {c["id"] for cands in candidates for c in cands}
    out = []
    for i, cands in enumerate(candidates):
        r = by_idx.get(i)
        if r is None:
            raise ValueError(f"model gave no judgement for requirement {i}")
        verdict = str(r.get("verdict", "")).strip().lower().replace(" ", "_").replace("-", "_")
        if verdict not in VERDICTS:
            raise ValueError(f"unknown verdict {r.get('verdict')!r} for requirement {i}")
        reason = strip_ids(str(r.get("reason") or "").strip(), all_ids)
        eid = r.get("evidence_id")
        flag = None
        chunk_id = None
        if verdict in ("met", "partial"):
            if eid in {c["id"] for c in cands}:
                chunk_id = eid
            else:
                verdict, flag = "not_on_record", "invalid_citation"
                reason = UNVERIFIED_REASON   # the model's own sentence argued for the verdict it lost, so it is not shown
        out.append({"verdict": verdict, "chunk_id": chunk_id, "reason": reason, "flag": flag})
    return out


# --- scoring ------------------------------------------------------------------------------

def align_requirements(items_a, items_b, threshold=0.55):
    """Rows of (item of A or None, item of B or None) for putting two reports of one posting side by side. Each model worded and
    listed the requirements itself, so items are paired by the words they share (the shared words as a share of the shorter text),
    best pairs first, each item once. Rows follow A's order; what only B has comes last."""
    tok_a = [set(tokens(i["text"])) for i in items_a]
    tok_b = [set(tokens(i["text"])) for i in items_b]
    pairs = []
    for x, ta in enumerate(tok_a):
        for y, tb in enumerate(tok_b):
            shared = len(ta & tb)
            share = shared / min(len(ta), len(tb)) if ta and tb else 0
            if shared >= 3 and share >= threshold:
                pairs.append((share, shared, -abs(x - y), x, y))
    pairs.sort(reverse=True)
    partner_of_a, taken_b = {}, set()
    for _, _, _, x, y in pairs:
        if x not in partner_of_a and y not in taken_b:
            partner_of_a[x] = y
            taken_b.add(y)
    rows = [(a, items_b[partner_of_a[x]] if x in partner_of_a else None) for x, a in enumerate(items_a)]
    rows += [(None, b) for y, b in enumerate(items_b) if y not in taken_b]
    return rows


def effective_verdict(item):
    return item.get("override_verdict") or item["verdict"]


def effective_weight(item):
    w = item.get("override_weight")
    return DEFAULT_WEIGHT[item["kind"]] if w is None else w


def score(items):
    """0 to 100, weighted. None when nothing carries weight."""
    total = sum(effective_weight(i) for i in items)
    if total <= 0:
        return None
    got = sum(effective_weight(i) * VALUE[effective_verdict(i)] for i in items)
    return got / total * 100


def display_order(items):
    """The order the report shows requirements in: minimum requirements first, then the nice-to-haves, each in its own order.
    The numbers on the page, in the chat and in `rescore` all count in this order, starting at 1."""
    return [i for i in items if i["kind"] == "minimum"] + [i for i in items if i["kind"] != "minimum"]


def rescore(items, leave_out=(), treat_as_met=()):
    """(the match now, the match after leaving some requirements out and treating others as met). Numbers are the
    requirement numbers shown in the report. Raises ValueError for a number the report does not have."""
    ordered = display_order(items)
    for k in (*leave_out, *treat_as_met):
        if not 1 <= k <= len(ordered):
            raise ValueError(f"there is no requirement {k}; this report has {len(ordered)}")
    changed = [{**it, "override_verdict": "met"} if n in treat_as_met else it
               for n, it in enumerate(ordered, 1) if n not in leave_out]
    return score(items), score(changed)


def scoring_rules():
    """How the match percentage is calculated, in words, written from the constants so it cannot drift from the code."""
    return (f"Each requirement has a weight: a minimum requirement {DEFAULT_WEIGHT['minimum']}, a nice-to-have "
            f"{DEFAULT_WEIGHT['desirable']} (unless a requirement line says otherwise). A verdict is worth points: met "
            f"{VALUE['met']:g}, partly {VALUE['partial']:g}, not shown {VALUE['not_on_record']:g}. The match percentage is "
            "the sum of weight times points over all requirements, divided by the sum of the weights, times 100. A requirement "
            "that is left out leaves both sums.")


def minimum_gap(items):
    """(minimum requirements not shown, minimum requirements)."""
    mins = [i for i in items if i["kind"] == "minimum"]
    return sum(1 for i in mins if effective_verdict(i) == "not_on_record"), len(mins)


# --- the pipeline -------------------------------------------------------------------------

def run_match(posting, cv_md, extra_facts, backend):
    posting = posting[:POSTING_LIMIT]
    chunks = chunk_cv(strip_comments(cv_md)) + chunk_extra(extra_facts or "")
    by_id = {c["id"]: c for c in chunks}
    reqs = backend.extract_requirements(posting)
    if not reqs:
        raise ValueError("no requirements found in the posting")
    candidates = [candidates_for(r["text"], chunks, EVIDENCE_K) for r in reqs]
    judgements = backend.judge(reqs, candidates, chunks) if getattr(backend, "reads_whole_cv", False) else backend.judge(reqs, candidates)
    items = []
    for i, (r, j) in enumerate(zip(reqs, judgements)):
        chunk = by_id.get(j["chunk_id"])
        items.append({
            "idx": i, "text": r["text"], "kind": r["kind"], "verdict": j["verdict"],
            "evidence": chunk["text"] if chunk else "", "section": chunk["section"] if chunk else "",
            "reason": j["reason"], "flag": j["flag"], "chunk_id": j["chunk_id"],
        })
    return items


# --- the other direction: does the posting fit the candidate's own terms ------------------

TERM_VERDICTS = ("fits", "conflict", "unclear")
TERM_WEIGHT = {"dealbreaker": 3, "important": 2, "nice": 1}
TERM_VALUE = {"fits": 1.0, "unclear": 0.5, "conflict": 0.0}


def term_verdict(item):
    return item.get("override_verdict") or item["verdict"]


def term_score(items):
    """0 to 100: how well the posting fits the candidate's terms. A criterion the posting is silent on counts half."""
    total = sum(TERM_WEIGHT[i["importance"]] for i in items)
    if not total:
        return None
    return sum(TERM_WEIGHT[i["importance"]] * TERM_VALUE[term_verdict(i)] for i in items) / total * 100


def term_conflicts(items):
    """Dealbreakers the posting conflicts with."""
    return [i for i in items if i["importance"] == "dealbreaker" and term_verdict(i) == "conflict"]


def _norm(text):
    return re.sub(r"\s+", " ", text.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')).strip().lower()


def quote_in_posting(quote, posting):
    return bool(quote) and _norm(quote) in _norm(posting)


def parse_terms(obj, criteria, posting):
    """One verdict per criterion. A fits or conflict verdict needs a quote that really is in the posting."""
    rows = obj.get("terms") if isinstance(obj, dict) else obj
    if not isinstance(rows, list):
        raise ValueError(f"model returned no terms list: {str(obj)[:200]!r}")
    by_idx = {r["idx"]: r for r in rows if isinstance(r, dict) and isinstance(r.get("idx"), int)}
    out = []
    for i, c in enumerate(criteria):
        r = by_idx.get(i)
        if r is None:
            raise ValueError(f"model gave no verdict for criterion {i} ({c['name']})")
        verdict = str(r.get("verdict", "")).strip().lower()
        if verdict not in TERM_VERDICTS:
            raise ValueError(f"unknown verdict {r.get('verdict')!r} for criterion {i}")
        quote = (r.get("quote") or "").strip()
        reason = str(r.get("reason") or "").strip()
        flag = None
        if verdict in ("fits", "conflict") and not quote_in_posting(quote, posting):
            verdict, flag = "unclear", "invalid_quote"
            reason = f"[model quoted text that is not in the posting; downgraded] {reason}".strip()
            quote = ""
        out.append({"verdict": verdict, "quote": quote if verdict != "unclear" else quote, "reason": reason, "flag": flag})
    return out
