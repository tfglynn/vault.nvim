from pathlib import Path

import pytest

from vault_lsp.index import VaultIndex, fuzzy_score, heading_line, link_at, links_in, open_link_query


# -- parsing -------------------------------------------------------------------

def test_link_at_finds_plain_link():
    line = "see [[Project Alpha]] for details"
    link = link_at(line, line.index("Alpha"))
    assert link.target == "Project Alpha"
    assert link.heading is None


def test_link_at_covers_the_brackets():
    line = "[[a]] and [[b]]"
    assert link_at(line, 0).target == "a"
    assert link_at(line, 4).target == "a"
    assert link_at(line, 5) is None
    assert link_at(line, 10).target == "b"


def test_link_at_heading_and_alias():
    link = link_at("[[Note#Goals|the goals]]", 3)
    assert (link.target, link.heading) == ("Note", "Goals")


def test_link_at_ignores_text_outside_links():
    assert link_at("no links here", 3) is None


def test_links_in_returns_spans_in_order():
    line = "x [[a]] y [[b#h|c]]"
    assert [(l.target, l.start, l.end) for l in links_in(line)] == [("a", 2, 7), ("b", 10, 19)]


# -- index ---------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path: Path) -> VaultIndex:
    (tmp_path / "vault.json").write_text("{}")
    for rel in ["Inbox.md", "projects/Alpha.md", "archive/old/Alpha.md", ".trash/Gone.md"]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.stem}\n\n## Goals\n")
    index = VaultIndex(tmp_path)
    index.scan()
    return index


def test_resolve_is_case_insensitive(vault):
    assert vault.resolve("inbox") == vault.root / "Inbox.md"


def test_resolve_prefers_shortest_path(vault):
    assert vault.resolve("Alpha") == vault.root / "projects/Alpha.md"


def test_resolve_with_folder_picks_specific_note(vault):
    assert vault.resolve("old/Alpha") == vault.root / "archive/old/Alpha.md"
    assert vault.resolve("nope/Alpha") is None


def test_hidden_folders_are_ignored(vault):
    assert vault.resolve("Gone") is None
    assert ".trash" not in vault.folders()


def test_folders_lists_root_first(vault):
    assert vault.folders() == [".", "archive", "archive/old", "projects"]


def test_create_note_in_chosen_folder(vault):
    path = vault.create_note("New Idea", "projects")
    assert path == vault.root / "projects/New Idea.md"
    assert path.read_text() == "# New Idea\n"
    assert vault.resolve("new idea") == path


def test_create_note_makes_new_folders(vault):
    path = vault.create_note("idea", "brand/new")
    assert path.is_file() and vault.resolve("idea") == path


def test_create_note_refuses_to_leave_the_vault(vault):
    with pytest.raises(ValueError):
        vault.create_note("evil", "../outside")
    with pytest.raises(ValueError):
        vault.create_note("../evil", ".")


def test_create_note_never_overwrites(vault):
    (vault.root / "Inbox.md").write_text("precious")
    vault.create_note("Inbox", ".")
    assert (vault.root / "Inbox.md").read_text() == "precious"


def test_heading_line(vault):
    assert heading_line(vault.root / "Inbox.md", "goals") == 2
    assert heading_line(vault.root / "Inbox.md", "missing") == 0


def test_add_reports_whether_note_is_new(vault):
    assert vault.add(vault.root / "Inbox.md") is False
    (vault.root / "Later.md").write_text("")
    assert vault.add(vault.root / "Later.md") is True
    assert vault.add(vault.root / "Later.md") is False


# -- completion ----------------------------------------------------------------

def test_open_link_query():
    assert open_link_query("see [[Fo") == "Fo"
    assert open_link_query("see [[") == ""
    assert open_link_query("see [[a/b c") == "a/b c"
    assert open_link_query("see [[Foo]] and") is None
    assert open_link_query("see [[Foo#Hea") is None
    assert open_link_query("see [[Foo|ali") is None
    assert open_link_query("just [text") is None


def test_fuzzy_score_ranks_kinds_of_match():
    ranked = sorted(
        ["Barfoo", "Foo", "Foobar", "Bar Foo", "F-o-o", "Nothing"],
        key=lambda name: fuzzy_score("foo", name) or (99,),
    )
    assert ranked == ["Foo", "Foobar", "Bar Foo", "Barfoo", "F-o-o", "Nothing"]
    assert fuzzy_score("foo", "Nothing") is None
    assert fuzzy_score("fb", "FooBar") is not None
    assert fuzzy_score("", "Anything") == (0, 0)


@pytest.fixture
def foo_vault(tmp_path: Path) -> VaultIndex:
    (tmp_path / "vault.json").write_text("{}")
    for rel in [
        "some/deeply/nested/folder/Foo.md",
        "Foobar.md",
        "misc/Barfoo.md",
        "Unrelated.md",
        "a/Dup.md",
        "b/c/Dup.md",
        ".trash/Food.md",
    ]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n")
    index = VaultIndex(tmp_path)
    index.scan()
    return index


def test_suggest_matches_anywhere_in_the_vault(foo_vault):
    got = [s.path.as_posix() for s in foo_vault.suggest("foo")]
    assert got == ["some/deeply/nested/folder/Foo.md", "Foobar.md", "misc/Barfoo.md"]


def test_suggest_inserts_bare_names_when_unambiguous(foo_vault):
    assert [s.name for s in foo_vault.suggest("foo")] == ["Foo", "Foobar", "Barfoo"]


def test_suggest_disambiguates_shared_names(foo_vault):
    names = sorted(s.name for s in foo_vault.suggest("dup"))
    assert names == ["Dup", "c/Dup"]
    for s in foo_vault.suggest("dup"):
        assert foo_vault.resolve(s.name) == foo_vault.root / s.path


def test_suggest_empty_query_lists_everything(foo_vault):
    assert len(foo_vault.suggest("")) == 6


def test_suggest_query_with_slash_matches_paths(foo_vault):
    assert [s.path.as_posix() for s in foo_vault.suggest("nested/fo")] == ["some/deeply/nested/folder/Foo.md"]


def test_suggest_skips_deleted_notes(foo_vault):
    (foo_vault.root / "Foobar.md").unlink()
    assert "Foobar" not in [s.name for s in foo_vault.suggest("foo")]
