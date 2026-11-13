"""gunicorn -c gunicorn.conf.py 'app:create_app()'  (one worker: see docs/DEPLOYMENT.md)"""
import os

# Without a password hash the admin trusts any request that looks like it comes from this machine, and behind a proxy
# that can be forged. Refuse to start a public server that way; ALLOW_LOOPBACK_ADMIN=1 is for a local trial only. The public demo
# (DEMO_READONLY=1) has fictional data, opens its admin to readers and refuses every write instead.
if not os.environ.get("ALLOW_LOOPBACK_ADMIN"):
    if not os.environ.get("ADMIN_PASSWORD_HASH") and not os.environ.get("DEMO_READONLY"):
        raise SystemExit("Refusing to start: set ADMIN_PASSWORD_HASH (python -m web.auth hash), or ALLOW_LOOPBACK_ADMIN=1 for a local trial.")
    if not os.environ.get("PUBLIC_URL"):
        raise SystemExit("Refusing to start: set PUBLIC_URL (https://your-domain): only that host name is accepted.")

workers = 1
threads = 8
timeout = 120
control_socket_disable = True    # gunicorn's control interface needs a writable home folder; the container's file system is read-only


def post_worker_init(worker):
    from app import maintain
    maintain(worker.wsgi)
