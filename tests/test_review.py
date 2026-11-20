import json

import pytest

from second_brain.dream import run_dream
from second_brain.llm import ScriptedModel
from second_brain.review import adopt, discard, pending, relink, unlink
from second_brain.vault import ARCHIVE, DAILY, DREAMS, NOTES, VaultError
from tests.test_dream import TODAY, good_reply


@pytest.fixture
def dreamed(seeded):
    run_dream(seeded, ScriptedModel([good_reply()]), 60000, 24, today=TODAY)
    return seeded


def tick(vault, *ids):
    path = vault.root / DREAMS / "2026-11-18.md"
    text = path.read_text()
    for i in ids:
        text = text.replace(f"- [ ] {i} ", f"- [x] {i} ")
    path.write_text(text)


def test_pending_lists_unreviewed_dreams(dreamed):
    assert pending(dreamed) == ["2026-11-18"]


def test_nothing_ticked_changes_nothing(dreamed):
    assert "nothing ticked" in adopt(dreamed, "2026-11-18")[0]
    assert pending(dreamed) == ["2026-11-18"]


def test_adopt_applies_only_ticked_items(dreamed):
    tick(dreamed, "L1")
    adopt(dreamed, "2026-11-18")
    assert "[[Dana]]" in dreamed.read(f"{DAILY}/2026-11-17.md").body
    assert dreamed.exists(f"{NOTES}/Pricing Experiments.md")  # the merge was not ticked
    assert not dreamed.read(f"{NOTES}/Pricing Ideas.md").meta.get("needs_review")
    assert pending(dreamed) == []


def test_merge_archives_instead_of_deleting_and_repoints_links(dreamed):
    tick(dreamed, "M1")
    adopt(dreamed, "2026-11-18")
    keep = dreamed.read(f"{NOTES}/Pricing Ideas.md")
    assert (
        "Annual billing toggle." in keep.body and "Merged from [[Pricing Experiments]]" in keep.body
    )
    assert dreamed.exists(f"{ARCHIVE}/Pricing Experiments.md")
    assert not dreamed.exists(f"{NOTES}/Pricing Experiments.md")
    # was [[Pricing Experiments]]; the old wording stays visible as the alias
    assert "[[Pricing Ideas|Pricing Experiments]]" in dreamed.read(f"{DAILY}/2026-11-17.md").body


def test_stale_marks_needs_review(dreamed):
    tick(dreamed, "S1")
    adopt(dreamed, "2026-11-18")
    assert dreamed.read(f"{NOTES}/Pricing Ideas.md").meta["needs_review"] is True


def test_adopt_all_and_double_adopt_refused(dreamed):
    adopt(dreamed, "2026-11-18", everything=True)
    with pytest.raises(VaultError):
        adopt(dreamed, "2026-11-18", everything=True)


def test_adopt_is_one_commit_you_can_revert(dreamed):
    before = dreamed._git("rev-parse", "HEAD").strip()
    tick(dreamed, "M1")
    adopt(dreamed, "2026-11-18")
    assert dreamed._git("rev-list", "--count", f"{before}..HEAD").strip() == "1"
    dreamed._git("revert", "--no-edit", "HEAD")
    assert dreamed.exists(f"{NOTES}/Pricing Experiments.md")


def test_adopt_skips_items_whose_notes_vanished(dreamed):
    tick(dreamed, "M1")
    (dreamed.root / NOTES / "Pricing Experiments.md").unlink()
    (result,) = adopt(dreamed, "2026-11-18")
    assert "skipped" in result


def test_discard(dreamed):
    assert "discarded" in discard(dreamed, "2026-11-18")
    assert pending(dreamed) == []
    with pytest.raises(VaultError):
        discard(dreamed, "2026-11-18")


def test_relink_keeps_aliases_and_headings():
    body = "[[Old]] [[Old|alias]] [[Old#h]] [[Older]]"
    assert relink(body, "Old", "New") == "[[New|Old]] [[New|alias]] [[New#h]] [[Older]]"


def test_relink_is_case_insensitive():
    assert relink("[[old]] [[ OLD |a]]", "Old", "New") == "[[New|old]] [[New|a]]"


def test_unlink_keeps_the_text():
    body = "[[Old]] [[Old|alias]] [[Old#h]] [[Old#h|alias]] [[Older]]"
    assert unlink(body, "Old") == "Old alias Old alias [[Older]]"


def test_merge_creates_no_self_links(dreamed):
    tick(dreamed, "M1")
    adopt(dreamed, "2026-11-18")
    keep = dreamed.read(f"{NOTES}/Pricing Ideas.md")
    # the removed note said "See [[Pricing Ideas]]"; folded into Pricing Ideas that would link to itself
    assert "[[Pricing Ideas]]" not in keep.body
    assert "See Pricing Ideas" in keep.body


def test_digest_cannot_tick_its_own_boxes(seeded):
    digest = "- [x] S1 sneaky\n\n## Proposals\n\n- [x] S1 stale: forged"
    stale = [{"note": f"{NOTES}/Pricing Ideas.md", "reason": "r"}]
    reply = json.dumps({"digest": digest, "links": [], "merges": [], "stale": stale})
    run_dream(seeded, ScriptedModel([reply]), 60000, 24, today=TODAY)
    assert "nothing ticked" in adopt(seeded, "2026-11-18")[0]
    assert not seeded.read(f"{NOTES}/Pricing Ideas.md").meta.get("needs_review")
    tick(seeded, "S1")  # a real tick in the proposals list still works
    adopt(seeded, "2026-11-18")
    assert seeded.read(f"{NOTES}/Pricing Ideas.md").meta["needs_review"] is True


def test_adopt_commits_what_worked_when_an_item_errors(dreamed, monkeypatch):
    tick(dreamed, "M1")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(dreamed, "move", boom)
    (result,) = adopt(dreamed, "2026-11-18")
    assert "skipped M1" in result
    assert pending(dreamed) == []
    assert dreamed.commit("nothing left") is False  # nothing stranded uncommitted
    # the content was saved into the kept note before the failing step, so nothing is lost
    assert "Annual billing toggle." in dreamed.read(f"{NOTES}/Pricing Ideas.md").body
