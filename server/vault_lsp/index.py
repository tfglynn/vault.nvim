"""The vault index: which notes exist, and how [[wikilinks]] resolve to them.

Nothing in this module knows about LSP, so it can be unit-tested directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CONFIG_FILE = "vault.json"

# [[target]]  [[target#heading]]  [[target|alias]]  [[target#heading|alias]]
WIKILINK = re.compile(
    r"\[\["
    r"(?P<target>[^\[\]|#]+)"
    r"(?:#(?P<heading>[^\[\]|]*))?"
    r"(?:\|(?P<alias>[^\[\]]*))?"
    r"\]\]"
)


@dataclass(frozen=True)
class Link:
    target: str           # "Project Alpha" or "projects/Project Alpha"
    heading: str | None   # "Goals" for [[Project Alpha#Goals]]
    start: int            # column of the opening "[[" (in code points)
    end: int              # column just past the closing "]]"


def links_in(line: str) -> list[Link]:
    """Every wikilink on `line`, left to right."""
    links = []
    for m in WIKILINK.finditer(line):
        heading = (m.group("heading") or "").strip() or None
        links.append(Link(m.group("target").strip(), heading, m.start(), m.end()))
    return links


def link_at(line: str, column: int) -> Link | None:
    """Return the wikilink that covers `column` on `line`, if any."""
    for link in links_in(line):
        if link.start <= column < link.end:
            return link
    return None


def heading_line(path: Path, heading: str) -> int:
    """Line number of a markdown heading in `path`, or 0 if it isn't found."""
    wanted = heading.strip().lower()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for number, line in enumerate(lines):
        if line.startswith("#") and line.lstrip("#").strip().lower() == wanted:
            return number
    return 0


def _is_hidden(relative: Path) -> bool:
    return any(part.startswith(".") for part in relative.parts)


# The unfinished link before the cursor: "[[" followed by a name with no
# closing brackets, heading or alias yet.
OPEN_LINK = re.compile(r"\[\[(?P<query>[^\[\]|#]*)$")


def open_link_query(before_cursor: str) -> str | None:
    """The partial note name being typed after "[[", or None if not in a link.

    `before_cursor` is the line up to the cursor. "see [[Fo" gives "Fo".
    """
    m = OPEN_LINK.search(before_cursor)
    return m.group("query") if m else None


def fuzzy_score(query: str, text: str) -> tuple[int, int] | None:
    """How well `query` matches `text`, lower is better; None if it doesn't.

    Case-insensitive. The query's characters must appear in order, but not
    necessarily together. Exact names beat prefixes, prefixes beat matches
    at the start of a word, which beat matches mid-word, which beat
    scattered characters (fewer gaps first).
    """
    q, t = query.lower(), text.lower()
    if not q:
        return (0, 0)
    if t == q:
        return (0, 0)
    if t.startswith(q):
        return (1, 0)
    position = t.find(q)
    if position >= 0:
        # Prefer a match that starts a word: "Bar Foo", "bar-foo", "barFoo".
        while position >= 0:
            if not text[position - 1].isalnum() or (text[position].isupper() and text[position - 1].islower()):
                return (2, position)
            position = t.find(q, position + 1)
        return (3, t.find(q))

    # Scattered characters: count the gaps between matched characters.
    gaps = 0
    start = 0
    previous = -1
    for char in q:
        found = t.find(char, start)
        if found < 0:
            return None
        if previous >= 0 and found != previous + 1:
            gaps += 1
        previous, start = found, found + 1
    return (4, gaps)


@dataclass(frozen=True)
class Suggestion:
    name: str        # what to insert after "[[": "Foo" or "nested/Foo" if "Foo" is ambiguous
    path: Path       # relative to the vault root, with ".md"


class VaultIndex:
    """An in-memory map of note name -> paths.

    Names are matched case-insensitively. Two notes may share a name if they
    live in different folders; a bare [[name]] then resolves to the one with
    the shortest path, and [[folder/name]] picks a specific one.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._notes: dict[str, set[Path]] = {}  # lowercased stem -> relative paths

    # -- building the index -------------------------------------------------

    def scan(self) -> int:
        """(Re)build the index from disk. Returns the number of notes found."""
        self._notes.clear()
        for path in self.root.rglob("*.md"):
            self.add(path)
        return sum(len(paths) for paths in self._notes.values())

    def add(self, path: Path) -> bool:
        """Add a note to the index. Returns True if it wasn't there before."""
        relative = self._relative(path)
        if relative is None or relative.suffix != ".md" or _is_hidden(relative):
            return False
        paths = self._notes.setdefault(relative.stem.lower(), set())
        if relative in paths:
            return False
        paths.add(relative)
        return True

    def _relative(self, path: Path) -> Path | None:
        try:
            return path.resolve().relative_to(self.root)
        except ValueError:  # outside the vault
            return None

    # -- queries ------------------------------------------------------------

    def resolve(self, target: str) -> Path | None:
        """Absolute path of the note a link target points to, or None."""
        parts = [p for p in target.strip().removesuffix(".md").split("/") if p]
        if not parts:
            return None
        wanted = [p.lower() for p in parts]

        candidates = []
        for relative in self._notes.get(wanted[-1], ()):
            # Notes deleted outside the editor linger in the index; skip them.
            if not (self.root / relative).is_file():
                continue
            # [[a/b]] must match the last path components exactly.
            have = [p.lower() for p in relative.with_suffix("").parts]
            if have[-len(wanted):] == wanted:
                candidates.append(relative)

        if not candidates:
            return None
        best = min(candidates, key=lambda p: (len(p.parts), p.as_posix().lower()))
        return self.root / best

    def link_name(self, relative: Path) -> str:
        """The shortest link text that resolves to `relative`.

        Usually just the file name; if another note with the same name wins
        a bare [[name]], prefix as many folders as it takes to tell them apart.
        """
        parts = relative.with_suffix("").parts
        for count in range(1, len(parts) + 1):
            name = "/".join(parts[-count:])
            if self.resolve(name) == self.root / relative:
                return name
        return "/".join(parts)

    def suggest(self, query: str, limit: int = 50) -> list[Suggestion]:
        """Notes whose names fuzzy-match `query`, best first.

        Matches against the file name wherever the note lives. A query with
        a "/" in it ("proj/al") matches against the whole path instead.
        """
        query = query.strip()
        scored = []
        for relatives in self._notes.values():
            for relative in relatives:
                if not (self.root / relative).is_file():
                    continue
                if "/" in query:
                    score = fuzzy_score(query, relative.with_suffix("").as_posix())
                else:
                    score = fuzzy_score(query, relative.stem)
                if score is not None:
                    key = (score, len(relative.stem), len(relative.parts), relative.as_posix().lower())
                    scored.append((key, relative))
        scored.sort()
        return [Suggestion(self.link_name(relative), relative) for _, relative in scored[:limit]]

    def folders(self) -> list[str]:
        """Every non-hidden folder in the vault, relative to the root ("." first)."""
        found = sorted(
            relative.as_posix()
            for path in self.root.rglob("*")
            if path.is_dir() and not _is_hidden(relative := path.relative_to(self.root))
        )
        return [".", *found]

    # -- changes ------------------------------------------------------------

    def create_note(self, target: str, folder: str) -> Path:
        """Create the note a link points to, inside `folder`.

        If the link itself names folders ([[projects/idea]]), they are created
        beneath `folder`. Returns the note's absolute path. If the note already
        exists, it is left untouched.
        """
        parts = [p.strip() for p in target.strip().removesuffix(".md").split("/") if p.strip()]
        if not parts or any(p in (".", "..") for p in parts):
            raise ValueError(f"not a valid note name: {target!r}")

        directory = (self.root / folder.strip() / Path(*parts[:-1])).resolve()
        if directory != self.root and self.root not in directory.parents:
            raise ValueError(f"folder is outside the vault: {folder!r}")

        path = directory / f"{parts[-1]}.md"
        if not path.exists():
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {parts[-1]}\n", encoding="utf-8")
        self.add(path)
        return path
