import pytest

from second_brain.vault import (
    ARCHIVE,
    DREAMS,
    NOTES,
    Note,
    VaultError,
    join_frontmatter,
    safe_name,
    split_frontmatter,
)


def test_frontmatter_round_trip():
    text = join_frontmatter({"title": "A", "tags": ["x", "y"]}, "Body here")
    meta, body = split_frontmatter(text)
    assert meta == {"title": "A", "tags": ["x", "y"]}
    assert body == "Body here"


def test_no_frontmatter_leaves_text_alone():
    assert split_frontmatter("just text") == ({}, "just text")
    assert split_frontmatter("---\nnot closed") == ({}, "---\nnot closed")


def test_bad_yaml_is_treated_as_body():
    meta, body = split_frontmatter("---\n: : :\n---\nhello")
    assert meta == {}


@pytest.mark.parametrize(
    "bad", ["../outside.md", "/etc/passwd", "20-notes/../../x.md", "", ".git/config"]
)
def test_resolve_refuses_paths_outside_the_vault(vault, bad):
    with pytest.raises(VaultError):
        vault.resolve(bad)


def test_write_then_read(vault):
    vault.write(
        Note(
            path=f"{NOTES}/A.md",
            body="hello [[B]] and [[C|alias]] and [[D#head]]",
            meta={"tags": ["t"]},
        )
    )
    note = vault.read(f"{NOTES}/A.md")
    assert note.body.startswith("hello")
    assert note.meta["tags"] == ["t"]
    assert note.links == {"B", "C", "D"}
    assert note.stem == "A"


def test_write_leaves_no_temp_files(vault):
    vault.write(Note(path=f"{NOTES}/A.md", body="x"))
    assert [p.name for p in (vault.root / NOTES).iterdir()] == ["A.md"]


def test_move_never_overwrites(vault):
    vault.write(Note(path=f"{NOTES}/A.md", body="first"))
    vault.write(Note(path="00-inbox/A.md", body="second"))
    final = vault.move("00-inbox/A.md", f"{NOTES}/A.md")
    assert final == f"{NOTES}/A 2.md"
    assert vault.read(f"{NOTES}/A.md").body == "first"


def test_list_notes_skips_hidden_and_obsidian_dirs(vault):
    vault.write(Note(path=f"{NOTES}/A.md", body="x"))
    (vault.root / ".obsidian").mkdir()
    (vault.root / ".obsidian" / "x.md").write_text("config")
    (vault.root / NOTES / ".hidden.md").write_text("hidden")
    assert vault.list_notes() == [f"{NOTES}/A.md"]


def test_search_ranks_title_over_body(seeded):
    hits = seeded.search("pricing")
    assert [rel for _, rel in hits][0].startswith(NOTES)
    assert seeded.search("zzzz") == []
    assert seeded.search("") == []


def test_find_by_stem_is_case_insensitive(seeded):
    assert seeded.find_by_stem("pricing ideas") == f"{NOTES}/Pricing Ideas.md"
    assert seeded.find_by_stem("nope") is None


def test_commit_reports_whether_anything_changed(vault):
    assert vault.commit("nothing") is False
    vault.write(Note(path=f"{NOTES}/A.md", body="x"))
    assert vault.commit("add") is True


def test_safe_name_strips_path_characters():
    assert safe_name("a/b\\c:d?") == "abcd"
    assert safe_name("../..") == "untitled"
    assert safe_name("", fallback="x") == "x"


def test_search_ignores_dreams_and_archive(seeded):
    seeded.write(Note(path=f"{DREAMS}/2026-11-18.md", body="pricing pricing pricing"))
    seeded.write(Note(path=f"{ARCHIVE}/Old.md", body="pricing pricing pricing"))
    hits = [rel for _, rel in seeded.search("pricing")]
    assert hits  # the real notes still match
    assert not any(rel.startswith((DREAMS, ARCHIVE)) for rel in hits)
