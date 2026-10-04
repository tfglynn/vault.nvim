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
