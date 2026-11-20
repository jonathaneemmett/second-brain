import argparse
import dataclasses
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from .capture import capture, describe
from .config import Settings
from .dream import run_dream
from .llm import Model, OpenRouterModel
from .notify import notify
from .review import adopt, discard, pending
from .vault import Vault, VaultError

log = logging.getLogger("second_brain")


def make_model(settings: Settings) -> Model:
    if not settings.api_key or not settings.model:
        raise SystemExit("Set OPENROUTER_API_KEY and OPENROUTER_MODEL in .env (see .env.example).")
    return OpenRouterModel(settings.api_key, settings.model, settings.base_url)


def run_job(name: str, settings: Settings, job: Callable[[], str]) -> int:
    """Run a scheduled job. On failure, say so loudly: a silent nightly job is a dead one."""
    try:
        print(job())
        return 0
    except Exception as exc:
        message = f"{name} failed: {exc!r}"
        print(message, file=sys.stderr)
        notify(settings.notify_url, f"second-brain: {name} failed", message)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="second-brain")
    parser.add_argument("--vault", help="vault folder, overriding VAULT_PATH")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the vault folders and git repo")
    sub.add_parser("capture", help="file notes from 00-inbox")
    dream = sub.add_parser("dream", help="write tonight's dream proposals")
    dream.add_argument("--force", action="store_true", help="dream again even if today's exists")
    sub.add_parser("review", help="list dreams waiting for review")
    adopt_cmd = sub.add_parser("adopt", help="apply the proposals you ticked")
    adopt_cmd.add_argument("day", help="dream date, YYYY-MM-DD")
    adopt_cmd.add_argument("--all", action="store_true", help="apply every proposal")
    discard_cmd = sub.add_parser("discard", help="reject a whole dream")
    discard_cmd.add_argument("day")
    mcp_cmd = sub.add_parser("mcp", help="serve the vault read-only over MCP")
    mcp_cmd.add_argument(
        "--http",
        action="store_true",
        help="streamable HTTP on localhost instead of stdio",
    )
    demo = sub.add_parser(
        "demo", help="seed demo-vault/ and run capture and a dream with no API key"
    )
    demo.add_argument("--reset", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    if args.vault:
        settings = dataclasses.replace(settings, vault=Path(args.vault).expanduser().resolve())
    vault = Vault(settings.vault)

    if args.command not in ("init", "demo") and not vault.root.is_dir():
        print(
            f"error: no vault at {vault.root} (set VAULT_PATH in .env or pass --vault)",
            file=sys.stderr,
        )
        return 1

    if args.command == "init":
        vault.init()
        print(f"vault ready at {vault.root}")
        return 0
    if args.command == "capture":
        return run_job("capture", settings, lambda: describe(capture(vault, make_model(settings))))
    if args.command == "dream":

        def job() -> str:
            result = run_dream(
                vault,
                make_model(settings),
                settings.max_input_chars,
                settings.lookback_hours,
                force=args.force,
            )
            return (
                f"dream: {result.skipped}"
                if result.skipped
                else f"dream: wrote {result.path} ({result.actions} proposals)"
            )

        return run_job("dream", settings, job)
    if args.command == "review":
        days = pending(vault)
        print("\n".join(f"pending: 90-dreams/{d}.md" for d in days) or "no dreams waiting")
        return 0
    if args.command in ("adopt", "discard"):
        try:
            if args.command == "adopt":
                print("\n".join(adopt(vault, args.day, args.all)))
            else:
                print(discard(vault, args.day))
        except (VaultError, FileNotFoundError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.command == "mcp":
        from .mcp_server import build_server, serve_http

        server = build_server(vault)
        if args.http:
            serve_http(server, settings.mcp_port, settings.mcp_allowed_hosts)
        else:
            server.run("stdio")
        return 0
    if args.command == "demo":
        return run_demo(args.reset)
    return 2


def run_demo(reset: bool) -> int:
    import shutil
    from datetime import date

    from . import demo

    root = demo.demo_root(Path.cwd())
    if root.exists() and reset:
        shutil.rmtree(root)
    if root.exists():
        print(f"{root} exists; run with --reset to start over", file=sys.stderr)
        return 1
    vault = Vault(root)
    demo.seed(vault)
    print(describe(capture(vault, demo.capture_model())))
    result = run_dream(vault, demo.dream_model(), 60000, 24, today=date(2026, 11, 18))
    print(f"dream: wrote {result.path} ({result.actions} proposals)")
    print(f"\nOpen {root} in Obsidian, read 90-dreams/2026-11-18.md, tick a box, then:")
    print("  uv run second-brain --vault demo-vault adopt 2026-11-18")
    return 0


if __name__ == "__main__":
    sys.exit(main())
