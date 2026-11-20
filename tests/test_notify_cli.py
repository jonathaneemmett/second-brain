import http.server
import threading

import pytest

from second_brain.cli import main, run_job
from second_brain.config import Settings
from second_brain.notify import notify


class Catcher(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Catcher.seen.append((self.headers["Title"], body.decode()))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_notify_posts_title_and_message():
    server = http.server.HTTPServer(("127.0.0.1", 0), Catcher)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/topic"
        assert notify(url, "dream failed", "boom") is True
    finally:
        server.shutdown()
    assert Catcher.seen == [("dream failed", "boom")]


def test_notify_never_raises_and_skips_when_unconfigured():
    assert notify("", "t", "m") is False
    assert notify("http://127.0.0.1:1/nope", "t", "m") is False


def test_run_job_returns_1_and_alerts_on_failure(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(
        "second_brain.cli.notify", lambda url, title, msg: sent.append((title, msg))
    )
    settings = Settings.from_env()

    def boom():
        raise RuntimeError("model down")

    assert run_job("dream", settings, boom) == 1
    assert "model down" in capsys.readouterr().err
    assert sent and "dream" in sent[0][0]
    assert run_job("dream", settings, lambda: "ok") == 0


def test_vault_flag_overrides_the_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "from-env"))
    assert main(["--vault", str(tmp_path / "from-flag"), "init"]) == 0
    assert (tmp_path / "from-flag" / "00-inbox").is_dir()
    assert not (tmp_path / "from-env").exists()


def test_cli_init_review_and_missing_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "v"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert main(["init"]) == 0
    assert main(["review"]) == 0
    assert "no dreams waiting" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["capture"])  # no key configured: fails with a clear message
    assert main(["adopt", "2026-01-01"]) == 1


def test_cli_demo_runs_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "4 proposals" in out
    assert main(["demo"]) == 1  # refuses to overwrite


def test_missing_vault_is_an_error_not_an_empty_result(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "nowhere"))
    assert main(["review"]) == 1
    assert "no vault at" in capsys.readouterr().err
