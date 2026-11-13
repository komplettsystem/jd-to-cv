# Reverse Apply

> **Status:** published for reference. Issues and pull requests are closed; this is a personal project shared as-is, not a maintained one.

A job posting applies to a candidate. Paste a job posting and get a report on how well a CV matches it, with the CV line
behind every verdict. Ask follow-up questions in a chat that stays inside the posting, the CV and the report, and get a
draft cover letter. People can also check their own CV, which is deleted when they are done or after 30 days. The owner gets
admin dashboards: runs, model traces, evals, models, spend, and a place to upload the public CV.

**Live:** https://jd-to-cv.karstenrieke.com. **Read-only demo with made-up data and the admin open:** https://demo.jd-to-cv.karstenrieke.com

It is a showcase of a small agentic product with the pieces around it built properly: typed agents, traces you can replay,
evals you can run, spend caps, a scoped chat, and privacy by design. It is one Flask app with SQLite.

## What it does

- **Report:** each requirement of the posting is judged against single CV lines (met, partly, not shown), with the line quoted.
  A separate overall reading looks at the whole CV (what fits, what the CV makes likely, where the CV and the role differ) and never changes the score.
- **Chat and letter:** limited by code to the posting, the CV and the report; letters cite the CV lines they rest on.
- **Check your own CV:** consent first, contact details removed, a retention choice, delete at once.
- **Admin:** dashboards, trace replay, evals (four dataset kinds), a model catalog with prices and reasoning effort, spend caps.
- **Roadmap page** with votes and a wishlist; contact and imprint pages.

## Run it

Python 3.14 (what the tests workflow and the Docker image use).

```
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt     # first time
.venv/bin/python app.py                                                   # http://127.0.0.1:8765
.venv/bin/python -m pytest -q
```

Without `ADMIN_PASSWORD_HASH` the admin answers only to this machine. Create a hash with `.venv/bin/python -m web.auth hash`.
The app refuses to listen on a public address, or to start under gunicorn, without one.

Demo data (a fictional candidate and fictional postings, never for production):

```
MATCH_DB=data/demo.db MATCH_CV=tools/sample_cv.md OWNER_NAME="Ada Example" .venv/bin/python tools/seed_demo.py
MATCH_DB=data/demo.db MATCH_CV=tools/sample_cv.md OWNER_NAME="Ada Example" .venv/bin/python app.py --port 8770
```

## Configuration (environment variables)

`MATCH_DB`, `MATCH_DATA`, `MATCH_CV` (the owner's own CV, default: the sample), `MATCH_PUBLIC_CV` (optional file for the CV
visitors may see quoted; normally uploaded in the admin instead), `OWNER_NAME`, `LEGAL_NAME`, `LEGAL_ADDRESS`,
`CONTACT_EMAIL`, `LINKEDIN_URL`, `GITHUB_URL`, `HOSTING_LABEL`, `ADMIN_USER`, `ADMIN_PASSWORD_HASH`, `SECRET_KEY`,
`COOKIE_SECURE`, `TRUST_PROXY` (only behind a proxy, and only with a password), `PUBLIC_URL`, `OPENROUTER_API_KEY`,
`ANTHROPIC_API_KEY`, `AWS_REGION`, `AWS_PROFILE`, `OLLAMA_URL`, `TRACE_CONTENT`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `JUDGE_MODEL`,
`EVAL_CONCURRENCY`, `ALLOW_LOOPBACK_ADMIN` (lets gunicorn start without a password, for a local trial only), `ALLOW_LOCAL_PUBLIC_MODEL` (development only),
`DEMO_READONLY` and `MAIN_URL` (the read-only demo with made-up data, see `docs/DEPLOYMENT.md`).

## Documentation

- `docs/ARCHITECTURE.md`: the tool landscape, what this app uses and why, how a check runs, how scope is enforced.
- `docs/EVALS.md`: what each eval dataset can and cannot tell you.
- `docs/MODELS.md`: providers, choosing models, reasoning effort, spend caps.
- `docs/DEPLOYMENT.md`: putting it on a server (Docker Compose and Caddy on any Ubuntu server, plus optional Terraform for Hetzner Cloud).
- `docs/PRIVACY-AND-LEGAL.md`: what the app does about personal data and what the operator still has to do.

## Status and limits

One web process with threads and a SQLite file; per-visitor limits are in memory. A job queue and storage away from the
web server are on the roadmap. The Docker image and compose file run the public demo on a single VPS behind Caddy; the Terraform files
(Hetzner) were validated but not run end to end. Prompt-injection resistance was tested with scripted models, not against real ones.

## License and security

MIT, see `LICENSE`. Report vulnerabilities privately, see `SECURITY.md`.
