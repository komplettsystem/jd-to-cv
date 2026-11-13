# Deploying

Reverse Apply is one Python web process (gunicorn, one worker, several threads) with a SQLite file. It needs a place
for that file, outbound HTTPS to a model provider, and a reverse proxy for HTTPS. The files in `deploy/` set this up
with Docker Compose and Caddy on any Ubuntu 24.04 or Debian 13 server, whichever company hosts it. `deploy/hetzner/` adds Terraform for
people who want the server itself created from code on Hetzner Cloud.

## What it needs

- 2 GB of RAM is comfortable. The app itself uses about 200 MB; building the Docker image and the operating system
  need the rest. 1 GB can work with a swap file but is tight.
- A domain name pointing at the server, so Caddy can get an HTTPS certificate.
- One model provider account. OpenRouter alone is enough; Bedrock and the Anthropic API are optional.

## Settings

Copy `deploy/.env.example` to `deploy/.env` and fill it in. The ones that matter first:

| Variable | What it does |
|---|---|
| `ADMIN_PASSWORD_HASH` | Required. Create it with `python -m web.auth hash`. The app refuses to start without it. |
| `PUBLIC_URL` | Required. Only this host name is accepted (protects the admin against DNS rebinding). |
| `SECRET_KEY` | Signs form tokens and the admin session. 32 or more random characters. |
| `OPENROUTER_API_KEY` | The model provider key. Never commit it. |
| `OWNER_NAME`, `LEGAL_NAME`, `LEGAL_ADDRESS`, `CONTACT_EMAIL` | Shown on the pages and the imprint. |
| `LINKEDIN_URL`, `GITHUB_URL`, `HOSTING_LABEL` | Shown on the contact page and named in the privacy text. |

The full list is in the README.

## First start

1. Get a server with 2 GB of RAM running Ubuntu 24.04 or Debian 13 and put your SSH key on it (`ssh-copy-id root@<server-ip>`).
   Many providers sell such a server for a few euros a month; choose a data centre in the region your privacy text promises.
   On Hetzner Cloud you can create it with Terraform instead: `cd deploy/hetzner && terraform init && terraform apply`,
   with a project API token exported as `TF_VAR_hcloud_token` in your own shell.
2. Run the one-time setup from your machine: `ssh root@<server-ip> 'bash -s' < deploy/setup-server.sh`. It installs Docker,
   a swap file, a firewall, fail2ban and automatic security updates, and turns off password logins once a key is installed.
   If you used the Terraform route, cloud-init and the Hetzner firewall do this part.
3. Point an A record for your domain at the server's address.
4. `deploy/deploy.sh <server-ip>` copies the committed code and your `.env` and starts the containers.
5. Open `/admin`, log in, and on the Settings page upload the CV that visitors may see quoted. On the Models page
   choose the public model and refresh the model list. Until a public CV and a priced public model are set, visitors'
   checks stay off.
6. Set the spend caps under Settings (per report, per day for visitors, per month for everything).

## Updating

Commit, then run `deploy/deploy.sh <server-ip>` again. Only committed files are copied; the database and the `.env`
live in `/opt/reverse-apply/state` and are never replaced by a deploy. The server also rebuilds weekly with fresh
base images (`update.sh`, run by cron) and reboots after security updates.

## What to know

- **One process.** Jobs (reports, chat answers, letters) run in threads of the web process, and the per-visitor limits
  are in memory. Do not run more than one worker. Moving the jobs to a queue and the data to a managed database are
  on the public roadmap as hardening items.
- **Backups.** Server backups hold whatever is on the disk: uploaded CVs, traces with full prompts, the `.env`. A deleted
  report can stay in a backup until it rolls over. Say so in your privacy text.
- **Spend caps.** Running jobs reserve a share of the caps, so parallel requests cannot all pass the check at once.
  A single call can still overshoot by its own cost. A prepaid balance at the model provider is a hard ceiling outside the app.
- **Tested:** the Docker image and compose file (app, read-only demo, Caddy with automatic HTTPS) run on a single Debian VPS.
  **Not tested:** the Terraform files for Hetzner were validated but not run end to end. Expect to adjust.

## After it is live

Check the public address with free scanners such as Webbkoll and Blacklight, and with your browser's developer tools
(Network and Application tabs): visitors' pages should set no cookies, use no local or session storage, and load
nothing from another host. The admin login sets one session cookie.

## The read-only demo (optional)

The compose file also starts a second copy of the app, `demo`, for a public "behind the scenes" view: the admin pages (dashboards, traces, models, evals) filled with made-up data for a fictional candidate, open to any reader and unable to change anything. It has no `.env`, no API key and no volume: it builds a throw-away database in memory at start from `tools/seed_demo.py` and `evals/history.json`, so it can never show the real database. Every write request is refused with a message, and every page carries a banner saying so.

To put it on a host name: add a DNS record for the name you choose (an `A` record with the server's address), put the name in `DEMO_DOMAIN` in `deploy/.env`, and deploy. Caddy gets the certificate on the first visit. Without `DEMO_DOMAIN` the demo only answers to the name `localhost` and is not reachable from outside.
