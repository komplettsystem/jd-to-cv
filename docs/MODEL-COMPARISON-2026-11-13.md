# Model comparison, 13 November 2026

One calibration run: 19 real job postings (public career pages, kept out of git) against the owner's CV (contact details removed), 11 models, 2 passes each, with the site's own check (`tools/compare_models.py`, analysis by `tools/analyze_comparison.py`). 418 runs. The first pass used the site's answer limit of 8192 tokens; the runs that failed on it, plus all Qwen runs (which first failed on a forced tool call), were repeated with a limit of 24000 tokens and Qwen in prompted mode. Credits went from 9.57 to 2.65 USD (this run, the earlier trial and live traffic).

## What the table can and cannot say

There is no answer key. "Score vs group" and "Deviation from the others" measure agreement with the other ten models, not correctness. A model can be far from the group and right. The only statements about quality that stand on their own are the mechanical ones: failures, dropped citations, missing overall readings, speed, cost and repeat stability. Two passes per posting is a small sample for stability.

| Model | Failed | Median seconds | Cost per report | Requirements found | Met / partly / none | Overall reading failed | Dropped citations | Score vs group (pts) | Deviation from the others | Same verdict as the others | Score change between passes (pts) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-haiku-5.5 | 0 | 36 | $0.008 | 11.0 | 20 / 66 / 14 | 0/38 | 0.2% | -7.0 | 0.125 | 73% | 4.6 |
| deepseek-v4.1-flash | 0 | 51 | $0.008 | 10.5 | 40 / 46 / 13 | 0/38 | 0.0% | +4.2 | 0.080 | 82% | 5.1 |
| gpt-6.1-terra | 0 | 25 | $0.021 | 12.7 | 15 / 73 / 12 | 0/38 | 0.0% | -9.7 | 0.144 | 69% | 2.3 |
| muse-ember | 0 | 33 | $0.011 | 11.2 | 26 / 59 / 15 | 0/38 | 0.0% | -5.4 | 0.090 | 80% | 4.4 |
| mimo-v2.6-pro | 0 | 260 | $0.018 | 10.5 | 41 / 42 / 16 | 1/38 | 0.0% | +3.2 | 0.085 | 81% | 4.7 |
| mimo-v2.6-flash | 0 | 62 | $0.005 | 12.1 | 30 / 50 / 20 | 1/38 | 0.0% | -5.4 | 0.121 | 74% | 8.5 |
| minimax-m3.1-flash | 0 | 32 | $0.004 | 13.9 | 38 / 51 / 10 | 10/38 | 0.4% | +2.9 | 0.123 | 73% | 10.1 |
| glm-5.3-flash | 0 | 114 | $0.002 | 10.5 | 47 / 41 / 12 | 8/38 | 0.0% | +8.3 | 0.109 | 76% | 7.2 |
| glm-5.3 | 7 | 80 | $0.021 | 10.3 | 38 / 47 / 15 | 3/31 | 0.0% | +0.5 | 0.099 | 77% | 7.0 |
| qwen3.8-flash | 1 | 291 | $0.015 | 11.3 | 25 / 63 / 11 | 0/37 | 0.0% | -4.3 | 0.108 | 76% | 4.1 |
| nemotron-3-super | 1 | 74 | $0.007 | 11.3 | 63 / 18 / 18 | 1/37 | 0.0% | +12.1 | 0.215 | 57% | 8.5 |

## Findings

1. **The site's 8192-token answer limit breaks reasoning models.** With it, DeepSeek, GLM (both), Mimo (both), Muse, Nemotron and Qwen failed on 4 to 24 of 38 runs ("token limit exceeded before any response"). At 24000 almost all finished; GLM 5.3 still failed 7 and Qwen 1. Haiku and Terra never failed at either limit. Cost per report for the repeated models includes the larger limit.
2. **Haiku 5.5 and Terra are the only models that were reliable under the production settings.** Terra is the fastest and the most repeatable (2.3 points between passes) but costs about 2.6 times as much as Haiku and finds the most requirements (12.7).
3. **The models split into two camps on how strict a verdict is.** Haiku, Terra, Muse and Qwen mark most requirements "partly" and few "met" (met 15 to 26%). DeepSeek, Mimo Pro, GLM and MiniMax mark 38 to 47% "met". Nemotron is an outlier (63% met, 57% agreement) and cannot be recommended. That is why scores differ by 10 to 20 points for the same posting, as in one of the 19 postings. Which camp is right needs the owner's own labels.
4. **MiniMax and GLM flash often cannot produce the overall reading** (10 and 8 of 38 failed), and MiniMax's scores moved 10 points between passes. Cheap, but not for a page recruiters see.
5. **DeepSeek v4.1 flash is the strongest alternative:** no failures at the larger limit, same cost as Haiku, closest to the group (82% same verdict), repeat change 5.1 points, 51 seconds. Its scores run 4 points above the group where Haiku runs 7 below.

## What to do next

- Keep Haiku 5.5 as the public and chat model for now: reliable, cheap, 36 seconds, no dropped overall readings.
- Do not switch on anyone's judgment of "better" yet. The deciding question is which strictness matches the owner's own reading of his CV against postings. Mark 10 to 15 reports as reviewed in the admin and use the my-labels evaluation; then compare Haiku, DeepSeek and Terra against those labels.
- If any reasoning model goes live, raise the answer limit (`llm/agents.py` `SETTINGS["max_tokens"]`) to about 24000 first. Measured, not yet deployed.
- Qwen's prompted-mode change is committed but not deployed; Qwen is slow (median 291 s) and not a candidate.

## Second check: against the owner's own per-posting notes

The first table has no answer key. The owner keeps private per-posting notes ("Meets", "Not on record", "Does not"), written with an assistant and in part reviewed by the owner. A script kept outside this repository matches requirement lines against those notes with narrow keyword patterns (about 50 gap items and 6 to 12 strength items per model, from 15 postings, two passes). Limits: the notes are coarse, the match is by wording, the CV has changed since some were written (the Reverse Apply bullet), and the strength sample is small. "Partly" on a gap line is arguable, so only "met" counts as over-crediting.

| Model | Gap lines marked met (over-credit) | Gap lines marked none | Strength lines marked met | Strength lines marked none |
|---|---|---|---|---|
| nemotron-3-super | 19% | 51% | 100% | 0% |
| mimo-v2.6-pro | 15% | 52% | 100% | 0% |
| glm-5.3 | 13% | 50% | 88% | 0% |
| glm-5.3-flash | 13% | 40% | 100% | 0% |
| muse-ember | 12% | 47% | 89% | 0% |
| minimax-m3.1-flash | 11% | 25% | 91% | 0% |
| mimo-v2.6-flash | 9% | 51% | 80% | 0% |
| deepseek-v4.1-flash | 8% | 41% | 88% | 0% |
| gpt-6.1-terra | 8% | 30% | 17% | 0% |
| claude-haiku-5.5 | 6% | 47% | 64% | 0% |
| qwen3.8-flash | 6% | 34% | 70% | 0% |

What it says:
- **Over-crediting is lowest for Haiku and Qwen (6%), then DeepSeek and Terra (8%).** Nemotron (19%) and Mimo Pro (15%) credit gaps most.
- **No model reliably says "none" on a known gap.** The best reach about half; the rest answer "partly". Prompting is the lever here, not model choice.
- **Terra under-credits real strengths:** only 17% "met" on lines the notes say he meets (12 items), against 64% for Haiku and 88% for DeepSeek. That is why Terra's scores run lowest across the batch.
- **Differences of a few points are noise** at this sample size. Only the Terra and Nemotron deviations are large enough to stand out.
- **Haiku and DeepSeek come out best on balance** (low over-credit, real strengths mostly recognised). This supports keeping Haiku and considering DeepSeek, not a switch.

## Third check: judge prompt tightened ("partly" needs part of what the requirement names)

Same 16 postings, two passes, three models, old against new wording (`llm/agents.py` `JUDGE_RULES`, not deployed). Share of known-gap lines marked "none" (higher is better) and strength lines marked "met":

| Model | Gap lines "none", old to new | Gap lines "met", old to new | Strength lines "met", old to new | Mean score, old to new |
|---|---|---|---|---|
| claude-haiku-5.5 | 47% to 46% | 6% to 6% | 64% to 78% | 54.7 to 54.1 |
| deepseek-v4.1-flash | 41% to 35% | 8% to 2% | 88% to 75% | 66.8 to 62.5 |
| gpt-6.1-terra | 30% to 50% | 8% to 8% | 17% to 18% | 53.1 to 49.7 |

The change moves Terra and leaves Haiku, the live model, where it was. About 48 gap lines and 8 to 11 strength lines per cell, one rerun: changes under about 10 points are within noise. Not deployed.

## Fourth check: the judge reads the whole CV instead of five word-matched lines

Until now the judge saw, per requirement, the five CV lines that share the most words with it (TF-IDF), and could only cite one of those. For a CV of about 1,900 tokens that hides lines a reader would find (the "or equivalent experience" degree case was one). `llm/agents.py` `WHOLE_CV_CHARS` (30,000 characters; longer CVs fall back to five lines) now shows the whole CV once for all requirements. Haiku, the same 16 postings, two passes each, against the earlier top-5 run (which did not yet have the equivalent-experience rule, a small confound):

| | Top-5 lines | Whole CV |
|---|---|---|
| Mean score | 54.7 | 65.8 |
| Cost per report | $0.0075 | $0.0052 |
| Median seconds | 36 | 31 |
| Citations not found in the CV | 1 | 0 |
| Requirements marked "none" | 13% | 8% |
| Gap lines in the owner's notes marked "met" | 6% | 6% |
| Strength lines in the owner's notes marked "met" | 64% (n=11) | 90% (n=10) |

Scores rise by about 11 points because lines the word match missed are now found; known gaps are not credited more. One model, 16 postings and small strength samples: a strong hint, not a measure. Older reports and the other models' figures above were produced with the top-5 lines. Switch off with `WHOLE_CV_CHARS = 0`.

Limit raised to 60,000 characters (the upload cap). Test: Haiku, same 16 postings, two passes, a CV padded to 52,000 characters (the master CV plus the tailored application CVs, so mostly repeated facts, not unrelated text): no failures, mean score 65.0 against 65.8 with the 7,000-character CV, cost per report 0.94 against 0.52 cents, median 33 against 31 seconds, no unfound citations. Known-gap lines marked "met" 8% against 6% (4 lines against 3, noise). Not tested: a long CV made of unrelated material such as a publication list.

## Fifth check: the thirty-case benchmark (`reverse-apply-bench`)

Thirty fictional cases in eight groups of traps (`evals/benchmark.py`), run through the app's own check. Cases passed, with repeat runs where there are any (case pass rate moves by about 3 points per case, so a difference of one or two cases is noise: Haiku's three runs scored 27, 25 and 27):

| Model | Cases passed | Over-credit | Under-credit | Cost per run |
|---|---|---|---|---|
| DeepSeek v4.1 flash (one run) | 29 of 30 (one model error, no wrong verdict) | 0% | 0% | $0.019 |
| Claude Haiku 5.5 (three runs) | 27, 25, 27 | 0% | 6 to 10% | $0.013 |
| GPT-6.1 Terra (two runs) | 24, 24 | 0% | 3 to 7% | $0.070 |

Haiku answers "partly" where the CV clearly shows the requirement (GDPR, roadmap, a board briefing). Terra answers "partly" where the CV shows nothing or states an absence ("have no experience with payment systems yet"). No model credited a neighbouring field or a title as met, so those cases do not separate the models yet. The first run exposed several expected answers that were arguable; they were tightened before the figures above, so the cases were adjusted after seeing failures.
