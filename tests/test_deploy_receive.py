"""deploy/deploy-receive.sh is the only thing the GitHub deploy key can run on the server: it must refuse a bad request, never touch state/.env, and keep the old app when the upload is bad."""
import io
import os
import subprocess
import tarfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "deploy-receive.sh"
WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "tests.yml"


def tar_of(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def run(root, command, upload):
    env = {**os.environ, "REVERSE_APPLY_ROOT": str(root), "SSH_ORIGINAL_COMMAND": command}
    return subprocess.run(["bash", str(SCRIPT)], input=upload, env=env, capture_output=True)


def server(tmp_path, with_env=True):
    (tmp_path / "state").mkdir()
    if with_env:
        (tmp_path / "state" / ".env").write_text("OPENROUTER_API_KEY=keep-me")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "old.txt").write_text("old")
    return tmp_path


GOOD = {"deploy/update.sh": "#!/bin/sh\n", "web.py": "new"}


def test_good_upload_replaces_the_app_and_leaves_the_env_alone(tmp_path):
    root = server(tmp_path)
    done = run(root, "0123456789ab", tar_of(GOOD))
    assert done.returncode == 0, done.stderr
    assert (root / "app" / "web.py").read_text() == "new"
    assert not (root / "app" / "old.txt").exists()
    assert (root / "app" / "VERSION").read_text().strip() == "0123456"
    assert (root / "state" / ".env").read_text() == "OPENROUTER_API_KEY=keep-me"


def test_a_command_that_is_not_a_commit_id_is_refused(tmp_path):
    root = server(tmp_path)
    for command in ("", "bash", "echo hi", "abc; rm -rf /", "ABCDEF1"):
        assert run(root, command, tar_of(GOOD)).returncode != 0
    assert (root / "app" / "old.txt").exists()


def test_an_upload_without_update_sh_keeps_the_old_app(tmp_path):
    root = server(tmp_path)
    done = run(root, "abcdef1", tar_of({"web.py": "new"}))
    assert done.returncode != 0
    assert (root / "app" / "old.txt").exists()
    assert not (root / "app.new").exists()


def test_without_a_server_env_nothing_is_deployed(tmp_path):
    root = server(tmp_path, with_env=False)
    assert run(root, "abcdef1", tar_of(GOOD)).returncode != 0
    assert (root / "app" / "old.txt").exists()


def test_the_deploy_job_waits_for_tests_main_and_the_production_environment():
    text = WORKFLOW.read_text()
    job = text[text.index("  deploy:"):]
    assert "needs: pytest" in job
    assert "github.ref == 'refs/heads/main'" in job and "github.event_name == 'push'" in job
    assert "environment: production" in job
    assert "StrictHostKeyChecking=yes" in job
