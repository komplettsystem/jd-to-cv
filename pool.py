"""Talent pool reference: how many LinkedIn profiles contain the terms a posting asks for.

The app never makes these numbers up and never calls LinkedIn itself. It drafts a cumulative search
(titles, then titles AND the first requirement's terms, then AND the next, ...), gives a search link for each
step, and stores the count someone read off LinkedIn, with its source and date. What a count means:
profiles whose text contains the terms. It is not a count of people who can do the work, LinkedIn rounds and
caps its numbers, and what you see depends on the account that searched."""
import re
from urllib.parse import quote

from pydantic import BaseModel, Field


class PoolStepOut(BaseModel):
    label: str = Field(description="Short name of the requirement this step adds, at most 6 words")
    requirement_idx: int | None = Field(None, description="Index of the posting requirement it comes from")
    any_of: list[str] = Field(min_length=1, max_length=6,
                              description="Words or short phrases a profile would use for it; any one is enough")


class PoolPlanOut(BaseModel):
    titles: list[str] = Field(min_length=1, max_length=6, description="Job titles for the role, as people write them")
    steps: list[PoolStepOut] = Field(min_length=1, max_length=5)


def _group(terms):
    cleaned = [t.replace('"', "").strip() for t in terms if t.strip()]
    return "(" + " OR ".join(f'"{t}"' if " " in t else t for t in cleaned) + ")"


def build_steps(plan):
    """Cumulative boolean searches. Step 0 is the titles alone; each later step adds one requirement."""
    steps, query = [], _group(plan.titles)
    steps.append({"label": "People with this kind of title", "requirement_idx": None, "query": query})
    for st in plan.steps:
        query = f"{query} AND {_group(st.any_of)}"
        steps.append({"label": st.label, "requirement_idx": st.requirement_idx, "query": query})
    for s in steps:
        s["url"] = "https://www.linkedin.com/search/results/people/?keywords=" + quote(s["query"])
    return steps


def parse_count(text):
    """'About 1,200 results' -> 1200; '1K+' -> 1000; '12.5K' -> 12500; None if there is no number."""
    m = re.search(r"([\d][\d.,]*)\s*([KkMm]?)", text or "")
    if not m:
        return None
    raw, unit = m.group(1), m.group(2).lower()
    if unit:
        n = float(raw.replace(",", "."))
        return int(n * (1000 if unit == "k" else 1_000_000))
    return int(re.sub(r"[.,]", "", raw))


def funnel(steps):
    """Steps that have a count, in order, each with its share of the first step and of the previous one."""
    counted = [s for s in steps if s.get("count") is not None]
    if not counted:
        return []
    first = counted[0]["count"]
    out, prev = [], None
    for s in counted:
        out.append({**s, "share_of_first": (s["count"] / first) if first else None,
                    "share_of_previous": (s["count"] / prev) if prev else None})
        prev = s["count"]
    return out


def rarity(steps):
    """One sentence for the report, or None when there is nothing measured. Lower-bound counts are marked."""
    f = funnel(steps)
    if len(f) < 2 or not f[0]["count"]:
        return None
    last = f[-1]
    capped = any("+" in (s.get("count_text") or "") for s in (f[0], last))
    ratio = f[0]["count"] / max(last["count"], 1)
    return {"first": f[0]["count"], "last": last["count"], "one_in": round(ratio),
            "capped": capped, "last_label": last["label"]}
