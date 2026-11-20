"""Review loop: adopt the proposals you ticked, or discard the dream. Each is one git commit."""

import json
import re
from datetime import date
from pathlib import Path

from .dream import DREAM_FOLDERS, Action, sidecar_path
from .vault import ARCHIVE, DREAMS, Note, Vault, VaultError

TICKED = re.compile(r"^- \[[xX]\] ([LMS]\d+)\b", re.MULTILINE)
PROPOSALS_HEADING = "\n## Proposals\n"


def _ticked(body: str) -> set[str]:
    """Ids ticked in the proposals list. The digest above it is model text, so never read it."""
    start = body.rfind(PROPOSALS_HEADING)  # render() writes the real heading last
    return set(TICKED.findall(body[start:])) if start != -1 else set()


def pending(vault: Vault) -> list[str]:
    days = []
    for rel in vault.list_notes((DREAMS,)):
        if vault.read(rel).meta.get("status") == "pending":
            days.append(Path(rel).stem)
    return days


def _load(vault: Vault, day: str) -> tuple[Note, list[Action]]:
    note = vault.read(f"{DREAMS}/{day}.md")
    side = vault.root / sidecar_path(day)
    actions = (
        [Action(**a) for a in json.loads(side.read_text(encoding="utf-8"))] if side.exists() else []
    )
    return note, actions


def _link_pattern(name: str) -> str:
    return r"\[\[\s*(" + re.escape(name) + r")\s*"


def relink(body: str, old: str, new: str) -> str:
    """Point links at `old` to `new` (case-insensitive), keeping the original wording visible."""
    pattern = re.compile(_link_pattern(old) + r"(\]\]|[|#])", re.IGNORECASE)

    def swap(m: re.Match) -> str:
        shown, tail = m.group(1), m.group(2)
        return f"[[{new}|{shown}]]" if tail == "]]" else f"[[{new}{tail}"

    return pattern.sub(swap, body)


def unlink(body: str, name: str) -> str:
    """Turn links to `name` into plain text (the alias if there is one)."""
    pattern = re.compile(_link_pattern(name) + r"(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]", re.IGNORECASE)
    return pattern.sub(lambda m: m.group(2) or m.group(1), body)


def _add_related(vault: Vault, rel: str, target: str) -> str:
    note = vault.read(rel)
    if target in note.links:
        return f"skipped: {note.stem} already links to {target}"
    body = note.body.rstrip()
    if "## Related" not in body:
        body += "\n\n## Related\n"
    note.body = body + f"\n- [[{target}]]\n"
    vault.write(note)
    return f"linked {note.stem} -> {target}"


def _merge(vault: Vault, keep_rel: str, remove_rel: str, day: str) -> str:
    if not (vault.exists(keep_rel) and vault.exists(remove_rel)):
        return f"skipped: {keep_rel} or {remove_rel} no longer exists"
    gone = vault.read(remove_rel)
    keep = vault.read(keep_rel)
    keep_stem = Path(keep_rel).stem
    # Order matters if something fails midway: the content is saved into `keep` first and the
    # original is archived last, so a partial merge duplicates text but never loses it.
    keep_body = unlink(keep.body, gone.stem).rstrip()
    gone_body = unlink(unlink(gone.body, gone.stem), keep_stem).strip()
    keep.body = f"{keep_body}\n\n## Merged from [[{gone.stem}]] ({day})\n\n{gone_body}\n"
    vault.write(keep)
    for rel in vault.list_notes(DREAM_FOLDERS):
        if rel in (remove_rel, keep_rel):
            continue
        other = vault.read(rel)
        new_body = relink(other.body, gone.stem, keep_stem)
        if new_body != other.body:
            other.body = new_body
            vault.write(other)
    vault.move(remove_rel, f"{ARCHIVE}/{Path(remove_rel).name}")
    return f"merged {gone.stem} into {keep_stem} (original kept in {ARCHIVE}/)"


def _stale(vault: Vault, rel: str) -> str:
    note = vault.read(rel)
    note.meta["needs_review"] = True
    vault.write(note)
    return f"marked {note.stem} needs_review"


def _apply(vault: Vault, action: Action, day: str) -> str:
    try:
        if action.kind == "link":
            return _add_related(vault, action.data["note"], action.data["target"])
        if action.kind == "merge":
            return _merge(vault, action.data["keep"], action.data["remove"], day)
        return _stale(vault, action.data["note"])
    except (VaultError, OSError, KeyError) as exc:
        return f"skipped {action.id}: {exc}"


def adopt(vault: Vault, day: str, everything: bool = False) -> list[str]:
    note, actions = _load(vault, day)
    if note.meta.get("status") != "pending":
        raise VaultError(f"dream {day} is {note.meta.get('status')}, not pending")
    wanted = {a.id for a in actions} if everything else _ticked(note.body)
    chosen = [a for a in actions if a.id in wanted]
    if not chosen:
        return ["nothing ticked: tick boxes in the dream note, or pass --all"]
    results = [f"{a.id}: {_apply(vault, a, day)}" for a in chosen]
    note = vault.read(f"{DREAMS}/{day}.md")
    note.meta["status"] = "adopted"
    note.meta["reviewed"] = date.today().isoformat()
    vault.write(note)
    vault.commit(f"adopt dream {day}: {', '.join(a.id for a in chosen)}")
    return results


def discard(vault: Vault, day: str) -> str:
    note, _ = _load(vault, day)
    if note.meta.get("status") != "pending":
        raise VaultError(f"dream {day} is {note.meta.get('status')}, not pending")
    note.meta["status"] = "discarded"
    vault.write(note)
    vault.commit(f"discard dream {day}")
    return f"discarded dream {day}"
