# second-brain

A self-hosted second brain: a folder of plain markdown notes (an Obsidian vault) kept in git, with two small LLM agents that look after it.

- **Capture** files whatever lands in your inbox into the right folder, adding a title, tags, and a one-line summary. It never rewrites your words.
- **Dream** runs overnight, reads your recent notes, and *proposes* improvements: missing links, notes that should be merged, notes that look stale. It never edits a note.
- **Review** is you. You open the dream note in Obsidian, tick the proposals you want, and `adopt` applies only those, as a single git commit you can revert.

Everything is plain files and git, so you can read, edit, and undo all of it by hand.

## How it works

The vault has a fixed layout:

```
00-inbox/     drop raw notes here
10-daily/     journal-style entries
20-notes/     everything else
30-people/    notes about one person
90-dreams/    nightly proposals (one note per day)
99-archive/   merged-away notes, kept rather than deleted
```

1. **Capture.** `second-brain capture` sends each inbox note to the model, which picks a folder (`20-notes`, `30-people`, or `10-daily`), a title, tags, and a summary. The note is moved there and committed.
2. **Dream.** `second-brain dream` builds an index of every note plus the full text of recently changed ones, and asks the model for a digest and proposals. It writes `90-dreams/YYYY-MM-DD.md` with a checklist, and a hidden sidecar file with the machine-readable proposals. If nothing changed, it makes no model call.
3. **Review.** `second-brain review` lists dreams waiting for you. Tick boxes in the dream note, then `second-brain adopt YYYY-MM-DD`. Links are added under a `## Related` heading, merges fold one note into another and move the original to `99-archive/`, and stale notes get `needs_review: true` in their frontmatter. `second-brain discard YYYY-MM-DD` rejects a whole dream.

### Safety model

Note text is treated as untrusted data, and the model's output is too.

- The model can only choose a folder from a short allowed list, a title, and tags. It cannot write anywhere else.
- Proposals that point at missing notes, paths outside the vault, or folders out of scope are dropped.
- Only the boxes you tick in the proposals list count. Ticks written into the model's digest are ignored.
- Merges archive the original instead of deleting it, and every capture, dream, and adopt is its own git commit.

## Quick start (no API key)

The demo seeds a small vault and runs capture and a dream with a scripted model, so you can see the whole loop first.

```
uv sync
uv run second-brain demo
```

Open `demo-vault/` in Obsidian, read `90-dreams/2026-11-18.md`, tick a box, then:

```
uv run second-brain --vault demo-vault adopt 2026-11-18
```

Run `uv run second-brain demo --reset` to start over.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```
uv sync
cp .env.example .env
```

Edit `.env`:

| Variable | Purpose | Default |
| --- | --- | --- |
| `VAULT_PATH` | Where your vault lives | `vault` |
| `OPENROUTER_API_KEY` | OpenRouter API key | required for capture and dream |
| `OPENROUTER_MODEL` | Model to use, as an OpenRouter model id | required for capture and dream |
| `OPENROUTER_BASE_URL` | API base URL | `https://openrouter.ai/api/v1` |
| `DREAM_MAX_INPUT_CHARS` | Cap on text sent to the model per dream | `60000` |
| `DREAM_LOOKBACK_HOURS` | How far back "recent" notes go | `30` |
| `NOTIFY_URL` | Optional ntfy.sh topic URL (or any endpoint accepting a plain POST) for failure alerts | none |
| `MCP_PORT` | Port for the MCP server over HTTP | `8765` |
| `MCP_ALLOWED_HOSTS` | Comma-separated hostnames allowed to reach the MCP server | none |

Then create the vault:

```
uv run second-brain init
```

Open that folder in Obsidian. `.env`, `vault/`, and `demo-vault/` are git-ignored in this repo; the vault is its own separate git repo.

## Daily use

```
uv run second-brain capture             # file everything in 00-inbox
uv run second-brain dream               # write tonight's proposals (--force to redo today's)
uv run second-brain review              # list dreams waiting for review
uv run second-brain adopt 2026-11-18    # apply the boxes you ticked (--all for everything)
uv run second-brain discard 2026-11-18  # reject a dream
```

Pass `--vault PATH` before the command to use a different vault than `VAULT_PATH`.

### Scheduling

`capture` and `dream` are meant to run unattended. If a scheduled job fails, it prints the error and, when `NOTIFY_URL` is set, posts an alert, so a silent failure doesn't go unnoticed. For example, with cron:

```
0 2 * * *  cd /path/to/second-brain && uv run second-brain capture
30 2 * * * cd /path/to/second-brain && uv run second-brain dream
```

### Undoing things

Every change to the vault is a git commit. To undo an adopt, run `git revert HEAD` inside the vault.

## MCP server

`second-brain mcp` serves the vault read-only to AI clients over [MCP](https://modelcontextprotocol.io). Nothing it exposes can write, move, or delete a note; every tool is marked read-only.

| Tool | What it does |
| --- | --- |
| `search_notes(query, limit=5)` | Rank notes by title, tags, and body (archive and dreams are left out) |
| `read_note(path)` | Read one note: body, frontmatter, and its `[[links]]` |
| `list_notes(folder=None)` | List note paths, optionally for one folder |
| `recent_notes(hours=24, folder=None)` | List notes changed recently |
| `list_pending_dreams()` | List dreams waiting for your review |

**stdio** (the default) is what desktop clients launch themselves. For Claude Desktop, add this to its MCP config, with your own paths:

```json
{
  "mcpServers": {
    "second-brain": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/second-brain", "second-brain", "mcp"],
      "env": { "VAULT_PATH": "/path/to/vault" }
    }
  }
}
```

**HTTP** runs a long-lived server for remote clients:

```
uv run second-brain mcp --http     # http://127.0.0.1:8765/mcp (MCP_PORT)
```

It only listens on `127.0.0.1` and only answers requests addressed to `localhost`, `127.0.0.1`, or a host listed in `MCP_ALLOWED_HOSTS`; anything else gets a `421 Invalid Host header`. To reach it from another machine, put a proxy in front, such as `tailscale serve`, and add that machine's hostname (for example `mini.your-tailnet.ts.net`) to `MCP_ALLOWED_HOSTS`. The server itself has no login, so only expose it somewhere you already trust, like your tailnet.

## Development

```
uv run pytest        # tests use a scripted model, no network or API key needed
uv run ruff check src tests
uv run ruff format src tests
```

Code lives in `src/second_brain/`: `vault.py` (files, frontmatter, search, git), `capture.py`, `dream.py`, `review.py`, `mcp_server.py`, `llm.py` (model seam), `notify.py`, `config.py`, `cli.py`, and `demo.py`.
