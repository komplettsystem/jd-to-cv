# Why the report no longer shows a match percentage

Written 2026-11-15 about a change made on 2026-11-14, and updated the same day with two further tests. It says what changed, what drove it, what the evidence is and how little it covers.

## What changed

1. A visitor's report no longer shows "75% match". It shows the minimum requirements of the posting as three counts, for example "5 met, 2 partly, 1 not shown", and the overall reading (one of four levels: strong fit, good fit, a stretch, little overlap, with a short summary and the CV lines it rests on).
2. The percentage is still worked out in `match.py`, from the stored verdicts, each time a page is shown. Only the owner sees it, on the admin page, labelled "not shown to visitors".
3. The chat no longer quotes or recalculates a percentage. A "what if" question (what if this requirement were left out) now goes to `check_overall`. It returns exact counts before and after, worked out in code, and the overall reading re-made by a model on the changed requirements, so the answer says whether the reading would change. The stored report is never touched.
4. The separate scoring service that the chat used to call for recalculation (`match_engine.py`) was removed. Nothing else used it, and recalculation is not part of the planned API and MCP offer.
5. Two sentences on the report page that said the matcher and the overall reading "never change the percentage" now say they never change the report, since visitors no longer see a percentage.

## What drove it

The percentage was far above the owner's own reading on two of the four postings he reviewed in full. On one of them the live page showed 75% while his own verdicts for the same report give 19%. A single number reads as a measurement, and a recruiter or job seeker has no way to tell that this one is off by that much.

The owner traced the problem to how the number comes about: the judge matches each requirement to a claim in the CV, and that matching is erratic, so the percentage built on it came out erratic too, and more confusing than helpful. He judged that the overall reading, which looks at seniority, domain and scope the way a hiring manager does, shows the actual fit more realistically. That is his judgment. The evidence below agrees with it in part: the judge is unstable and leans generous, but its disagreements with him are mostly one step, not random.

## The evidence

Each requirement of a report gets a verdict from the judge: a model step that reads the whole CV and gives "met", "partly" or "not shown", citing the one CV line it rests on. The percentage is built from these verdicts (minimum requirements count twice, "partly" counts half).

The judge was tested against the owner's own labels: 37 requirements from six reports, three passes each. 33 came from four reports he reviewed in full, 4 from requirements he changed in two earlier reports. Verdicts he never looked at do not count as labels.

1. With the current rules the judge agreed with him on 55% to 67% of judgements, across five runs of the same rules (64%, 67%, 59%, 62%, 55%). Runs of identical rules therefore differ by up to about 12 points. In each run it gave a more generous verdict than him on about a third of judgements and a stricter one on 3 to 7%. Of the requirements he marked "not shown", it caught about 30%.
2. None of thirteen variants did better than that run-to-run spread. The variants:
   - stricter wording for "partly";
   - quoting the words that cover the requirement, with code checking the quote exists in the line;
   - writing what is shown and what is missing first, "met" only if nothing is missing;
   - a second reader that may lower a verdict, the same model or a larger one;
   - his labelled examples in the prompt (10 balanced, or about 28 from the other reports);
   - a larger model as the judge: Sonnet 5.5, or Opus 5.5 at high effort (Claude models from Anthropic; Haiku is the smallest, Opus the largest);
   - up to three CV lines per requirement instead of one.
   Agreement ranged from 56% to 67%. Most variants changed how the verdict is reached. Only two (several lines per requirement, the quote check) touched which line is cited.
3. Scores on the four fully reviewed reports (partly counted as half), live judge and test judge against the owner's own verdicts:

   | Report | Live judge | Test judge, mean of three passes | Owner |
   |---|---|---|---|
   | A | 75 | 52 | 19 |
   | B | 56 | 62.5 | 38 |
   | C | 78 | 78 | 78 |
   | D | 64 | 64 | 56 |

   The live judge is above the owner by 56, 19, 0 and 8 points, a mean error of 20.8. The test judge's mean error is 16.7, computed from unrounded scores. Counting "partly" as a quarter or as nothing lowers the test judge's error to 15.6 and 14.6, so how much credit "partly" earns is not the main cause. In three of the 37 cases the judge's own reason says the key part is missing while the verdict is still "partly".
4. The test judge was a stand-in (Haiku 5.5 through the local `claude` command), because the variants were run repeatedly and the live model is paid per call. On the four reports its verdicts matched the live judge's on 67%, 88%, 92% and 89% of requirements (A to D). The stand-in is therefore closest to the live judge on the reports where the live judge was least wrong, and the live column above is the more direct evidence.

## Where the disagreements sit

1. About 85% of disagreements with the owner are between neighbouring levels: 34 of 40, 39 of 46, 35 of 42 and 44 of 50 in four runs of the same rules. The judge rarely says "met" where he says "not shown".
2. Asked only whether a requirement is fully shown, or only whether it has any credit at all, the judge agrees with him on 71% to 80% of judgements, against 55% to 64% on the three levels.
3. The judge is also unstable. In one run, 11 of the 37 requirements got a different verdict in at least one of the three passes, always by one step, never from "met" to "not shown". Total agreement hides this, because the flips cancel out.
4. A guess, not a finding: most requirements are written as an ideal candidate (abstract, several skills in one sentence) and a CV line is concrete, so whether the line "shows" the requirement depends on how much inference is allowed. The three-level scale puts that judgment call in the middle level, where the owner and the judge draw the line differently and the judge draws it differently from pass to pass. The check would be for the owner to label the same requirements again a few days later. If he flips on "partly" at a similar rate, the middle level is fuzzy by nature, and no judge would match it closely.
5. The matching itself, meaning which CV line backs a requirement, was not measured on its own. His labels are verdicts only, so it is not known whether the cited line is the right one, or the same on every pass.

## Confidence signals that were tried

A confidence shown next to each verdict would let a reader see where the judge is unreliable. Six cheap signals were tested on the same 37 labelled requirements, on verdicts that cite a line. The number is the chance that a verdict more generous than the owner's looks less certain than one that agrees (0.50 means the signal tells nothing). The 95% interval comes from resampling requirements.

| Signal | Chance | Interval |
|---|---|---|
| Word overlap between the requirement and the cited line | 0.65 | 0.49 to 0.78 |
| Words of the requirement found in the judge's quote | 0.64 | 0.49 to 0.78 |
| A second reader (Sonnet) lowers the verdict | 0.62 | 0.47 to 0.76 |
| A second reader (Haiku) lowers the verdict | 0.55 | 0.41 to 0.68 |
| The three passes agree | 0.54 | 0.41 to 0.67 |
| The judge's own stated confidence (0 to 100) | 0.42 | 0.25 to 0.59 |

Every interval includes 0.50. Six signals on 37 requirements and one labeller means the best of them is about the size that chance produces when several are tried. The check that the quote exists in the cited line never failed (99 of 99), so it works as a guard against invented quotes and gives no confidence. Even the highest third of word overlap had 28% more generous verdicts: the judge credits lines that share words with the requirement and still do not show it.

Every signal except word overlap comes from the same judge or a similar model, and a judge that leans generous leans the same way on every pass and when asked how sure it is. Only a standard from outside the judge, such as the owner's labels or a rule checked in code, can expose that lean. No confidence is shown on the report, because none was good enough.

## What the counts cost

These are expected costs, reasoned from the design and not measured.

1. Visitors lose a quick single figure to compare postings by. Three counts are harder to compare across postings.
2. "Not shown" can be misread as "the person lacks this". The report says next to the counts that it does not mean that.
3. The counts come from the same judge as the percentage, so on a report the owner has not reviewed they carry the same leniency, for example a "partly" on a requirement whose key part is missing. Showing counts instead of a percentage does not make the verdicts more accurate. It only stops one summary number from adding up their errors into a precise-looking figure.
4. Where the owner has confirmed or changed verdicts in a report, the counts and the admin percentage use his verdicts, not the judge's.

## What we did not check

1. The evidence is four reports, 37 requirements and one labeller. The labels were made with the report in front of him. One model family was tested, and a stand-in was used for the variants. It is a hint, not a measure.
2. The overall reading was not tested against his labels. Its eval (`overall-reading` in `docs/EVALS.md`) checks that the rules hold, that an unrelated posting does not come back as a good fit and that a hidden instruction is not followed, on three fixed postings. It says little about whether the reading is good.
3. Whether visitors understand the counts better than the percentage was not tested. No visitor was asked.
4. The owner's agreement with the overall reading rests on his reading of the reports, not on a measured comparison.
5. Whether the middle level is fuzzy for the owner himself (a second labelling) and whether the cited line is the right one were not tested.
6. The judge test files (labels, per-variant results) are in the owner's private research folder and are not part of this repository, because the labels come from the owner's own job search.

## What would change this

No threshold has been set for bringing the percentage back, and the owner would decide. The percentage is still computed and shown on the admin page. On reports the owner has not reviewed it shows the judge's reading, so it can be compared with his own as more reports are reviewed. A fair retest would need more than one labeller, more reports and the live model.
