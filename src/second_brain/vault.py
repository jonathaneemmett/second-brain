"""The vault: plain markdown files in a git repo. Everything else goes through here."""

import os
import re
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

INBOX = "00-inbox"
DAILY = "10-daily"
NOTES = "20-notes"
PEOPLE = "30-people"
DREAMS = "90-dreams"
ARCHIVE = "99-archive"
FOLDERS = (INBOX, DAILY, NOTES, PEOPLE, DREAMS, ARCHIVE)
SKIP_DIRS = {".obsidian", ".git", ".trash"}

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
UNSAFE_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]')
# Opening and closing fences must sit on their own lines; the block may be empty.
FRONTMATTER = re.compile(r"\A---\r?\n(?:(.*?)\r?\n)?---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


class VaultError(Exception):
    pass


def safe_name(name: str, fallback: str = "untitled") -> str:
    """Make a string usable as a note filename stem: no path characters, no leading dots."""
    cleaned = UNSAFE_NAME.sub("", name).strip().strip(".").strip()
    return cleaned or fallback


@dataclass
class Note:
    path: str  # posix path relative to the vault root
    body: str
    meta: dict = field(default_factory=dict)

    @property
    def stem(self) -> str:
        return Path(self.path).stem

    @property
    def links(self) -> set[str]:
        return {m.strip() for m in WIKILINK.findall(self.body)}

    @property
    def tags(self) -> list[str]:
        """Frontmatter tags as a list, whether written as a list or a plain string."""
        raw = self.meta.get("tags") or []
        if not isinstance(raw, (list, tuple)):
            raw = re.split(r"[,\s]+", str(raw))
        return [str(t) for t in raw if str(t)]


def split_frontmatter(text: str) -> tuple[dict, str]:
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1) or "") or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, text[match.end() :].lstrip("\n")


def join_frontmatter(meta: dict, body: str) -> str:
    if not meta:
        return body
    head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{head}\n---\n{body}"


class Vault:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    # -- setup ---------------------------------------------------------------
    def init(self) -> None:
        for folder in FOLDERS:
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        gitignore = self.root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(".obsidian/workspace*.json\n.trash/\n.DS_Store\n.tmp-*\n")
        if not (self.root / ".git").exists():
            self._git("init", "-b", "main")
        self.commit("init: vault structure")

    # -- paths ---------------------------------------------------------------
    def resolve(self, rel: str) -> Path:
        """Turn a vault-relative path into an absolute one, refusing anything outside."""
        if not rel or Path(rel).is_absolute() or "\x00" in rel:
            raise VaultError(f"bad path: {rel!r}")
        full = (self.root / rel).resolve()
        if not full.is_relative_to(self.root):
            raise VaultError(f"path escapes the vault: {rel!r}")
        if any(p in SKIP_DIRS or p.startswith(".") for p in full.relative_to(self.root).parts):
            raise VaultError(f"path is not a note: {rel!r}")
        return full

    def exists(self, rel: str) -> bool:
        try:
            return self.resolve(rel).is_file()
        except VaultError:
            return False

    # -- reading -------------------------------------------------------------
    def read(self, rel: str) -> Note:
        full = self.resolve(rel)
        if full.suffix != ".md" or not full.is_file():
            raise VaultError(f"not a markdown note: {rel!r}")
        meta, body = split_frontmatter(full.read_text(encoding="utf-8"))
        return Note(path=full.relative_to(self.root).as_posix(), body=body, meta=meta)

    def list_notes(self, folders: tuple[str, ...] | None = None) -> list[str]:
        out = []
        for path in sorted(self.root.rglob("*.md")):
            rel = path.relative_to(self.root)
            if any(p in SKIP_DIRS or p.startswith(".") for p in rel.parts):
                continue
            if folders and rel.parts[0] not in folders:
                continue
            out.append(rel.as_posix())
        return out

    def find_by_stem(self, stem: str) -> str | None:
        wanted = stem.strip().lower()
        for rel in self.list_notes():
            if Path(rel).stem.lower() == wanted:
                return rel
        return None

    def recent(self, hours: float, folders: tuple[str, ...] | None = None) -> list[str]:
        cutoff = time.time() - hours * 3600
        out = []
        for rel in self.list_notes(folders):
            try:
                if (self.root / rel).stat().st_mtime >= cutoff:
                    out.append(rel)
            except OSError:
                continue  # deleted between listing and stat
        return out

    def search(self, query: str, limit: int = 5) -> list[tuple[int, str]]:
        terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 1]
        if not terms:
            return []
        scored = []
        for rel in self.list_notes():
            if rel.startswith((ARCHIVE + "/", DREAMS + "/")):
                continue  # proposals and archived notes are not knowledge
            try:
                note = self.read(rel)
            except (VaultError, OSError, ValueError):
                continue  # one unreadable note must not break the whole search
            title = note.stem.lower()
            tags = " ".join(note.tags).lower()
            body = note.body.lower()
            score = sum(3 * (t in title) + 2 * (t in tags) + min(body.count(t), 5) for t in terms)
            if score:
                scored.append((score, rel))
        scored.sort(key=lambda s: (-s[0], s[1]))
        return scored[:limit]

    # -- writing -------------------------------------------------------------
    def write(self, note: Note) -> None:
        """Write atomically: a crash mid-write never leaves half a note."""
        full = self.resolve(note.path)
        full.parent.mkdir(parents=True, exist_ok=True)
        text = join_frontmatter(note.meta, note.body)
        # mkstemp creates 0600 files; keep the note's existing mode (or a normal one for new notes).
        mode = stat.S_IMODE(full.stat().st_mode) if full.exists() else 0o644
        fd, tmp = tempfile.mkstemp(dir=full.parent, prefix=".tmp-", suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, full)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def move(self, src: str, dst: str) -> str:
        """Move a note, picking a free name if dst is taken. Returns the final path."""
        source = self.resolve(src)
        target = self.resolve(dst)
        if not source.is_file():
            raise VaultError(f"not a note: {src!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        n = 2
        # samefile: moving a note onto itself (or a case-only rename) is not a collision
        while target.exists() and not target.samefile(source):
            target = target.with_name(f"{Path(dst).stem} {n}{Path(dst).suffix}")
            n += 1
        os.replace(source, target)
        return target.relative_to(self.root).as_posix()

    # -- git -----------------------------------------------------------------
    def _git(self, *args: str) -> str:
        result = subprocess.run(
            [
                "git",
                "-c",
                "user.name=second-brain",
                "-c",
                "user.email=agent@localhost",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise VaultError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def commit(self, message: str) -> bool:
        """Commit everything that changed. Returns False when there was nothing to commit."""
        self._git("add", "-A")
        if not self._git("status", "--porcelain").strip():
            return False
        self._git("commit", "-m", message)
        return True
