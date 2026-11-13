# Security reviews

Two reviews were done on 12 November 2026, before the first deployment. Both were run by an AI agent (Claude) that read the code and
the deployment files and reported findings, which were then fixed in the same session. No person and no outside firm reviewed the code,
and nobody tried to attack the running site. Treat this as a careful self-review, not a certification.

**What this file is.** A summary written afterwards from the commit message of the fixes (`fixes from the application security review`)
and the project's handover note. The agents' full reports were not saved as files, so the list below is what the fixes show, not the
original wording of each finding. `tests/test_security_hardening.py` and `tests/test_ingest.py` hold the tests that were added with the fixes.

## Review of the application code (eight findings, all fixed)

The handover note records eight findings; the commit lists the fixes below, and some findings needed more than one.

- Input length is capped before any pattern is matched against it.
- A PDF that expands to a very large text is refused (a bound on the extracted size).
- A forged forwarding header cannot change the address the app sees (proxy handling is explicit).
- Requests must use an expected host name (a host allowlist), and a cross-site POST is refused.
- Spend is reserved for parallel jobs, so several requests at once cannot all pass the monthly cap check.
- Traces are tied to the report they belong to and are purged with it.
- A shared link never carries facts the owner did not choose to share.
- A posting fetched from an address is fetched from a pinned, checked address.
- Expired reports are deleted on a timer, not only when someone visits.

## Review of the deployment files and the repository

- A deploy ships only committed files.
- State (database, keys) is kept apart from the code and is never replaced by a deploy.
- Per-visitor limits cover an IPv6 /64 as one visitor.
- Deletion of a report is a secure delete.
- The app refuses to start without an admin password hash or a public URL (`gunicorn.conf.py`), and a proxy setting without a password is refused.
- The compose file runs the app and the demo with a read-only filesystem, all Linux capabilities dropped and `no-new-privileges`.
- Test fixtures use fictional people and companies.

## Not done

- A dependency lock file with hashes, and pinned image digests.
- A penetration test or any review by a person.
- A re-review after later changes (the chat's matcher agent, the read-only demo mode, the model catalog). Those have tests but no separate review.
