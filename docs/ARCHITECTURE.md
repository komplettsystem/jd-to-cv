# Architecture

Reverse Apply checks a job posting against a CV. A visitor pastes a posting and gets a report with the CV line
behind every verdict, a chat that stays inside the posting, the CV and the report, and a draft cover letter. The
owner gets admin dashboards: runs, traces, evals, models, spend.

## The tool landscape for an agentic app like this

This is the general picture of what teams usually combine, not a list of features checked for this project.

| Layer | What teams commonly use | What this app uses, and why |
|---|---|---|
| Model gateway | OpenRouter, LiteLLM, AWS Bedrock, the vendors' own APIs | OpenRouter and Bedrock through Pydantic AI's providers, plus the Anthropic API and the local `claude` command and Ollama for development. One catalog table lists them; the admin switches them on. |
| Typed agents | Pydantic AI, LangChain and LangGraph, plain SDK calls | Pydantic AI: typed outputs validated by Pydantic, automatic retry with the validation error as feedback, small surface. LangGraph earns its place when a workflow needs branching, loops and long-lived state; these agents are single steps. |
| Traces | OpenTelemetry (GenAI semantic conventions) with Langfuse, Arize Phoenix, Logfire, LangSmith or Grafana as viewers | OpenTelemetry spans from Pydantic AI, stored in the app's own SQLite tables and shown in its own waterfall. Setting `OTEL_EXPORTER_OTLP_ENDPOINT` also forwards them to any OTLP viewer. |
| Evals | pydantic-evals, promptfoo, DeepEval, Ragas, Inspect, OpenAI Evals | pydantic-evals: cases, evaluators and reports as code, results stored for the dashboards. |
| Cost | genai-prices, provider price pages, the price OpenRouter publishes per model | genai-prices for Anthropic and Bedrock, OpenRouter's own prices for OpenRouter. An unknown price shows as n/a, never 0. |
| Guardrails | Prompt rules, schema validation, output validators, moderation APIs, LLM judges | Prompt rules plus code that checks every answer before it is shown (see Scope below). |
| Storage | Postgres, SQLite, a vector database | SQLite: one admin and a few readers. The CV is small enough to rank with TF-IDF; no vector database. |
| Web | Flask, FastAPI, Next.js | Flask with server-rendered pages, a small script for chat, no front-end framework. |

## How a check runs

1. A visitor posts a posting (admin: text, file or address). The app stores it and queues one job per model.
2. `match.run_match`: the model extracts requirements (typed list). The judge reads the whole CV (up to 60,000 characters,
   `WHOLE_CV_CHARS` in `llm/agents.py`) and judges each requirement, citing one CV line by id; a longer CV falls back to the five
   CV lines per requirement that share the most words with it (TF-IDF). A citation to a line it was not offered is downgraded to
   "not shown" and flagged.
3. The report is a weighted score (minimum requirements count twice, partial counts half) plus the cited lines.
4. Optional: the posting is checked against the owner's own terms (the other direction), the talent pool search is
   planned, a cover letter is drafted, and follow-up questions are answered.

Every unit of work runs inside one OpenTelemetry root span (`Tracing.subject`), so a report, a chat answer or an
eval case is one trace with the model requests and tool calls under it.

## Scope: what the chat and the letter may say

- The chat answers only from the posting, the CV and the report. Its instructions say so, and code checks every
  answer: it must declare scope and basis, claims about the CV must cite real CV lines, figures above 20 must exist
  in the material, and no email address or phone number may appear. A failed check makes the agent retry with the
  reason; after two failed retries the visitor gets a fixed message. An out-of-scope question gets a fixed refusal
  with suggested questions, never the model's own text.
- Posting text and questions are treated as untrusted data and are wrapped in tags.
- The cover letter is a draft written by software. Each body paragraph must cite CV lines. Code checks length,
  banned words, "new to me" lines and citations; a visitor's draft that fails the checks is not shown.
  A visitor chooses only language and length from fixed lists.
- Visitors' limits: checks per day, questions per chat, letters per report, a daily spend budget, posting length,
  a honeypot field and a minimum form time. Reports expire after the retention period and can be deleted by the visitor.

## Talent pool numbers

LinkedIn offers no public counts API and its terms restrict automated access, so the app never calls it. It drafts
a cumulative search (titles, then each requirement's terms added), links each step, and stores the count someone
read off LinkedIn with its source and date. A count means profiles whose text contains the terms. It is not a
count of people who can do the work.

## Data

`runs` (a posting, source admin or public) have `results` (kind fit or terms) with `items` or `term_items`;
`chats` with `chat_messages`; `letters`; `pool_steps`; `arena_votes`. `traces` and `spans` hold the OpenTelemetry data
and contain full prompts, so they are admin-only. `catalog` is the model list. `eval_runs` and `eval_cases` hold evals.
`settings` holds the owner's facts, terms, letter rules, limits and model roles.

## Security

Admin: scrypt password hash, login rate limit, session cookie (HttpOnly, SameSite=Lax, Secure behind HTTPS), CSRF
token on every POST, Content-Security-Policy with no inline script or style. Traces, notes, terms and the model
catalog never reach a public page; a test checks it.

## The match engine and the chat bot

The scoring lives in one place, `match.py` (weights, what each verdict is worth, the percentage). `match_engine.py` wraps it in a
small message interface: JSON-RPC 2.0 shaped like the Model Context Protocol (`initialize`, `tools/list`, `tools/call`) with two
tools, `rescore` and `explain_scoring`. The engine is plain code, not a model.

The chat bot (`llm/chat.py`) of a model that can call tools chooses its source for each question, and its instructions say how:

| The question is about | The bot uses | What answers |
|---|---|---|
| the posting or the report (why a verdict, which are met) | no tool | the report text in its prompt |
| the score (how it is calculated, what leaving a requirement out would do) | `explain_scoring`, `rescore` | the match engine: plain code, no model |
| the CV, where the report does not already cover it ("Has he led a team across several countries?") | `ask_matcher` | the matcher: the report's own judge, a model, reading the whole CV |

The bot is not told the formula; to answer "what would removing requirement 6 do to the match?" it calls `rescore` and reports the
engine's number. A model without tools is given the formula and the effect of each requirement, worked out in code beforehand, and
word-matched CV lines for the question, exactly as before the matcher existed.

The matcher (`llm/matcher.py`) is a small agent-to-agent call, not a second judge: up to five statements from the bot's question are
turned into requirements and go through `Agents.judge_async`, the same judge step and the same whole-CV reading a report uses (the
sync and async runners share one preparation, `Agents._judging`). Each statement comes back as a report item does: a verdict (met,
partial, not shown), the one CV line it rests on and one sentence of reason; the page shows them with the report's own labels and the
quoted line. Rules around it:

- The statements come from a visitor's question, which is untrusted. The judge is told that a requirement's own claim is not
  evidence, it can cite only CV lines, and a credit that cites no real line is downgraded to not shown as in any report. The result
  is a reading of the CV, never an edit: the stored report and its percentage are not touched.
- The matcher's model requests count against the chat run's usage limits (`usage` is passed into the nested run), and the matcher
  may be called twice per answer (`MAX_MATCHER_CALLS`). Its cost lands on the same trace, so the per-report spend cap sees it.
- If the matcher fails, the bot is told to say that the CV does not show it; the failed exchange is kept and shown. An answer about the CV normally has to cite a CV line; when the matcher showed none (or failed), "the CV does not show it" is accepted with no line, so that no unrelated quote is attached to it.
- The nested run is one trace with the chat run: root span, then the chat agent, its `ask_matcher` tool call, then the judge agent.
- A share link that does not show the CV gets the exchange without the quoted lines (`web.common.hide_cv_lines`).

Every request and reply, to the engine or the matcher, is stored with the answer (`chat_messages.exchanges`) and shown under it on
the report page. Figures that appear in an answer must come from the posting, the CV, the report or a reply from the engine or the
matcher; any other figure makes the answer fail its check and the model retries.

The engine could be offered to outside agents over HTTP as an MCP server (see the roadmap); today it is called inside the app only.
