from pathlib import Path

import pytest

from vault_lsp.index import VaultIndex, heading_line, link_at


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
