import json
from datetime import date

import anyio
from mcp.client import Client
from mcp.server.mcpserver import MCPServer

from second_brain import mcp_server
from second_brain.cli import main
from second_brain.dream import run_dream
from second_brain.llm import ScriptedModel
from second_brain.mcp_server import MAX_RESULTS, build_server, transport_security
from second_brain.vault import ARCHIVE, DREAMS, NOTES, PEOPLE, Note

TOOLS = {"search_notes", "read_note", "list_notes", "recent_notes", "list_pending_dreams"}


def call(vault, name, **arguments):
    async def go():
        async with Client(build_server(vault)) as client:
            return await client.call_tool(name, arguments)

    return anyio.run(go)


def listed(vault, name, **arguments):
    result = call(vault, name, **arguments)
    assert not result.is_error
    return result.structured_content["result"]


def test_exposes_only_read_only_tools(vault):
    async def go():
        async with Client(build_server(vault)) as client:
            return (await client.list_tools()).tools

    tools = anyio.run(go)
    assert {t.name for t in tools} == TOOLS
    assert all(t.annotations.read_only_hint for t in tools)


def test_search_notes_returns_ranked_paths(seeded):
    hits = listed(seeded, "search_notes", query="pricing")
    assert hits[0]["score"] >= hits[-1]["score"]
    assert {h["path"] for h in hits} == {
        f"{NOTES}/Pricing Ideas.md",
        f"{NOTES}/Pricing Experiments.md",
        "10-daily/2026-11-17.md",
    }
    assert len(listed(seeded, "search_notes", query="pricing", limit=1)) == 1
    assert len(listed(seeded, "search_notes", query="pricing", limit=0)) == 1  # clamped up
    assert len(listed(seeded, "search_notes", query="pricing", limit=10_000)) <= MAX_RESULTS


def test_search_notes_leaves_out_dreams_and_archive(seeded):
    seeded.write(Note(path=f"{DREAMS}/2026-11-18.md", body="pricing pricing pricing"))
    seeded.write(Note(path=f"{ARCHIVE}/Old.md", body="pricing pricing pricing"))
    paths = [h["path"] for h in listed(seeded, "search_notes", query="pricing")]
    assert paths and not any(p.startswith((DREAMS, ARCHIVE)) for p in paths)


def test_read_note_returns_body_meta_and_links(vault):
    meta = {"tags": ["t"], "when": date(2026, 11, 20)}
    vault.write(Note(path=f"{NOTES}/A.md", body="see [[B]] and [[C|alias]]", meta=meta))
    note = call(vault, "read_note", path=f"{NOTES}/A.md").structured_content
    assert note["path"] == f"{NOTES}/A.md"
    assert note["body"] == "see [[B]] and [[C|alias]]"
    assert note["meta"] == {"tags": ["t"], "when": "2026-11-20"}  # dates become strings
    assert note["links"] == ["B", "C"]


def test_read_note_refuses_paths_outside_the_vault(vault):
    for bad in ["../outside.md", "/etc/passwd", ".git/config", f"{NOTES}/missing.md", NOTES]:
        assert call(vault, "read_note", path=bad).is_error, bad


def test_list_and_recent_notes_filter_by_folder(seeded):
    assert listed(seeded, "list_notes", folder=PEOPLE) == [f"{PEOPLE}/Dana.md"]
    assert len(listed(seeded, "list_notes")) == 4
    assert len(listed(seeded, "recent_notes", hours=1)) == 4
    assert listed(seeded, "recent_notes", hours=1, folder=PEOPLE) == [f"{PEOPLE}/Dana.md"]
    assert listed(seeded, "recent_notes", hours=-5) == []  # negative windows count as zero


def test_list_notes_rejects_unknown_folders(seeded):
    assert call(seeded, "list_notes", folder="../..").is_error
    assert call(seeded, "list_notes", folder="nope").is_error


def test_list_pending_dreams(seeded):
    assert listed(seeded, "list_pending_dreams") == []
    reply = json.dumps({"digest": "d", "links": [], "merges": [], "stale": []})
    run_dream(seeded, ScriptedModel([reply]), 60000, 24, today=date(2026, 11, 20))
    assert listed(seeded, "list_pending_dreams") == ["2026-11-20"]


def test_every_tool_leaves_the_vault_untouched(seeded):
    before = {rel: seeded.read(rel).body for rel in seeded.list_notes()}
    call(seeded, "search_notes", query="pricing")
    call(seeded, "read_note", path=f"{NOTES}/Pricing Ideas.md")
    call(seeded, "list_notes")
    call(seeded, "recent_notes")
    call(seeded, "list_pending_dreams")
    assert seeded._git("status", "--porcelain") == ""
    assert {rel: seeded.read(rel).body for rel in seeded.list_notes()} == before


def test_transport_security_allows_only_local_and_listed_hosts():
    settings = transport_security(("mini.tail1234.ts.net",))
    assert settings.enable_dns_rebinding_protection
    assert "localhost:*" in settings.allowed_hosts
    assert "127.0.0.1:*" in settings.allowed_hosts
    assert "mini.tail1234.ts.net" in settings.allowed_hosts
    assert "mini.tail1234.ts.net:*" in settings.allowed_hosts
    assert "https://mini.tail1234.ts.net" in settings.allowed_origins
    assert "evil.example.com" not in settings.allowed_hosts
    assert "0.0.0.0:*" not in settings.allowed_hosts


def test_serve_http_binds_to_loopback_only(vault, monkeypatch):
    seen = {}

    def fake_run(self, transport="stdio", **kwargs):
        seen.update(transport=transport, **kwargs)

    monkeypatch.setattr(MCPServer, "run", fake_run)
    mcp_server.serve_http(build_server(vault), 9999, ("mini.ts.net",))
    assert seen["transport"] == "streamable-http"
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 9999
    assert "mini.ts.net" in seen["transport_security"].allowed_hosts


def test_cli_mcp_defaults_to_stdio(vault, monkeypatch):
    seen = []
    monkeypatch.setattr(
        MCPServer, "run", lambda self, transport="stdio", **kw: seen.append(transport)
    )
    assert main(["--vault", str(vault.root), "mcp"]) == 0
    assert seen == ["stdio"]


def test_cli_mcp_http_uses_configured_port_and_hosts(vault, monkeypatch):
    seen = {}
    monkeypatch.setenv("MCP_PORT", "9123")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "a.ts.net, b.ts.net")
    monkeypatch.setattr(
        mcp_server, "serve_http", lambda server, port, hosts: seen.update(port=port, hosts=hosts)
    )
    assert main(["--vault", str(vault.root), "mcp", "--http"]) == 0
    assert seen == {"port": 9123, "hosts": ("a.ts.net", "b.ts.net")}
