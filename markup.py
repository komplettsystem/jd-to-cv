"""Show the posting text with each requirement's verdict laid over the sentence it came from."""
import re

import match

MIN_OVERLAP = 0.35


def _sentences(line):
    return [s for s in re.split(r"(?<=[.!?;])\s+", line) if s.strip()] or [line]


def mark_posting(posting, items):
    """One list per line of the posting; each piece is {text, item, verdict, kind}. item is None for unmarked text.
    A requirement marks at most one sentence: the one whose words overlap its words most (and over a threshold)."""
    lines = [_sentences(l) if l.strip() else [] for l in posting.splitlines()]
    toks = [[set(match.tokens(s)) for s in sents] for sents in lines]
    taken = {}
    for ii, it in enumerate(items):
        want = set(match.tokens(it["text"]))
        if len(want) < 2:
            continue
        best, best_pos = 0.0, None
        for li, sents in enumerate(toks):
            for si, t in enumerate(sents):
                if not t or (li, si) in taken:
                    continue
                score = len(want & t) / len(want | t)
                if score > best:
                    best, best_pos = score, (li, si)
        if best_pos and best >= MIN_OVERLAP:
            taken[best_pos] = ii
    out = []
    for li, sents in enumerate(lines):
        row = []
        for si, text in enumerate(sents):
            ii = taken.get((li, si))
            it = items[ii] if ii is not None else None
            row.append({"text": text, "item": ii, "verdict": match.effective_verdict(it) if it else None,
                        "kind": it["kind"] if it else None})
        out.append(row)
    return out
