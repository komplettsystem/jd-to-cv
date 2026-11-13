"""Read the JSON lines written by compare_models.py and print one table per model, as Markdown.

There are no hand-labelled answers for these postings, so quality is measured against the other models: for every requirement the
verdicts of the other models give a reference (their median, met = 1, partly = 0.5, not shown = 0), and a model's distance from
it is its deviation. That finds a model that drifts from the group; it cannot find a mistake all models share. Repeat stability is
how far the match percentage moves between two passes of the same model on the same posting."""
import argparse
import json
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import match

VALUE = {"met": 1.0, "partial": 0.5, "not_on_record": 0.0}


def short(model):
    return model.split(":", 1)[-1].split("/", 1)[-1]


def load(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def consensus_deviation(runs):
    """Per model: mean distance of its verdicts from the median of the OTHER models' verdicts on the same requirement, and the
    share of requirements where it agrees exactly. Requirements are paired across models with match.align_requirements."""
    by_key = defaultdict(list)
    for r in runs:
        if r["ok"] and r["items"]:
            by_key[(r["posting"], r["pass"])].append(r)
    dist, exact, n = defaultdict(float), defaultdict(int), defaultdict(int)
    for key, group in by_key.items():
        if len(group) < 4:
            continue
        pivot = sorted(group, key=lambda r: len(r["items"]))[len(group) // 2]      # the model with the median number of requirements
        votes = defaultdict(dict)                                                  # pivot index -> {model: value}
        for r in group:
            rows = match.align_requirements(pivot["items"], r["items"])
            for x, (a, b) in enumerate(rows[:len(pivot["items"])]):
                if b is not None:
                    votes[x][r["model"]] = VALUE[b["verdict"]]
        for x, per_model in votes.items():
            for model, value in per_model.items():
                others = [v for m, v in per_model.items() if m != model]
                if len(others) < 3:
                    continue
                ref = st.median(others)
                dist[model] += abs(value - ref)
                exact[model] += value == ref
                n[model] += 1
    return {m: (dist[m] / n[m], exact[m] / n[m], n[m]) for m in n}


def stability(runs):
    """Per model: mean absolute change of the match percentage between pass 0 and pass 1 of the same posting."""
    scores = defaultdict(dict)
    for r in runs:
        if r["ok"] and r["score"] is not None:
            scores[(r["model"], r["posting"])][r["pass"]] = r["score"]
    diffs = defaultdict(list)
    for (model, _), per_pass in scores.items():
        if 0 in per_pass and 1 in per_pass:
            diffs[model].append(abs(per_pass[0] - per_pass[1]))
    return {m: (st.mean(d), len(d)) for m, d in diffs.items()}


def score_bias(runs):
    """Per model: mean signed distance of its match percentage from the median of all models on the same posting and pass."""
    groups = defaultdict(list)
    for r in runs:
        if r["ok"] and r["score"] is not None:
            groups[(r["posting"], r["pass"])].append(r)
    out = defaultdict(list)
    for group in groups.values():
        if len(group) >= 4:
            med = st.median(r["score"] for r in group)
            for r in group:
                out[r["model"]].append(r["score"] - med)
    return {m: st.mean(v) for m, v in out.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    args = ap.parse_args()
    runs = load(args.results)
    models = sorted({r["model"] for r in runs}, key=short)
    dev, stab, bias = consensus_deviation(runs), stability(runs), score_bias(runs)
    print(f"{len(runs)} runs, {len({r['posting'] for r in runs})} postings, {len(models)} models, "
          f"{sum(r.get('cost') or 0 for r in runs):.2f} USD spent\n")
    print("| Model | Runs | Failed | Seconds (median) | Cost per report | Requirements found | Minimum share | Met / partly / none | Dropped citations | Overall reading failed | Score vs group | Deviation from the others | Same verdict as the others | Score change between passes |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    rows = []
    for m in models:
        rs = [r for r in runs if r["model"] == m]
        ok = [r for r in rs if r["ok"]]
        items = [i for r in ok for i in r["items"]]
        verdicts = Counter(i["verdict"] for i in items)
        total = sum(verdicts.values()) or 1
        dropped = sum(1 for i in items if i["flag"] == "invalid_citation")
        overall_failed = sum(1 for r in ok if r.get("overall_error"))
        d = dev.get(m)
        s = stab.get(m)
        row = (f"| {short(m)} | {len(rs)} | {len(rs) - len(ok)} | {st.median([r['seconds'] for r in ok]) if ok else 0:.0f} | "
               f"${st.mean([r['cost'] for r in ok]) if ok else 0:.3f} | {len(items) / len(ok) if ok else 0:.1f} | "
               f"{sum(1 for i in items if i['kind'] == 'minimum') / total:.0%} | "
               f"{verdicts['met'] / total:.0%} / {verdicts['partial'] / total:.0%} / {verdicts['not_on_record'] / total:.0%} | "
               f"{dropped / total:.1%} | {overall_failed}/{len(ok)} | {bias.get(m, 0):+.1f} | "
               f"{(f'{d[0]:.3f}') if d else '-'} | {(f'{d[1]:.0%}') if d else '-'} | {(f'{s[0]:.1f} pts ({s[1]} pairs)') if s else '-'} |")
        rows.append((m, row))
    for _, row in rows:
        print(row)
    print("\nFailures by model and reason (first 100 characters):")
    for m in models:
        reasons = Counter(r["error"][:100] for r in runs if r["model"] == m and not r["ok"])
        for reason, count in reasons.most_common(3):
            print(f"- {short(m)}: {count}x {reason}")


if __name__ == "__main__":
    main()
