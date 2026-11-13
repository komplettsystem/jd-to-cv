# Security policy

If you find a security problem, please report it privately and do not open a public issue.

Use GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability"). Say what you
found, how to reproduce it, and what an attacker could do with it. This is a personal project maintained in spare time:
expect an answer within a few days, not an agreement on timelines.

In scope: the web application, its admin, the upload and chat paths, the deployment files in `deploy/`.
Out of scope: findings that need a person to already control the server, the model providers' own services, and
denial of service by sheer traffic volume.

Two security reviews were done on 12 November 2026, both by AI agent runs (Claude), not by a person or an outside firm: one of the application code and one of the deployment files and the repository. Their fixes are listed in [docs/SECURITY-REVIEWS.md](docs/SECURITY-REVIEWS.md), together with what was not done. This is not a penetration test.
