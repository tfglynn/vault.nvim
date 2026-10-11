"""End-to-end check of [[ completion: real Neovim + real vault-lsp, driven by
keystrokes the way a person would type them.

Run from the repo root, with vault-lsp and pynvim installed (both come with
`pip install -e './server[test]'`):

    python tests/completion_e2e.py            # uses `nvim` on PATH
    python tests/completion_e2e.py path/to/nvim

Prints PASS/FAIL lines; the exit code is non-zero on failure.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import pynvim

REPO = Path(__file__).resolve().parent.parent
NOTES = [
    "some/deeply/nested/folder/Foo.md",
    "Foobar.md",
    "misc/Barfoo.md",
    "Unrelated.md",
    "a/Dup.md",
    "b/c/Dup.md",
    "notes/The Split Stump.md",
]

failures = 0


def check(name: str, ok: bool, detail: object = "") -> None:
    global failures
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -- {detail}"))
    failures += not ok


def wait_for(condition, timeout: float = 3.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


def main() -> int:
    nvim_bin = sys.argv[1] if len(sys.argv) > 1 else "nvim"
    root = Path(tempfile.mkdtemp())
    (root / "vault.json").write_text("{}")
    (root / "index.md").write_text("# Index\n")
    for rel in NOTES:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("# note\n")

    nvim = pynvim.attach("child", argv=[nvim_bin, "-u", "NONE", "--embed", "--headless"])
    # The popup menu is only drawn, and pumvisible() only true, with a UI.
    nvim.ui_attach(100, 30, rgb=True)
    nvim.command(f"set rtp^={REPO} | filetype on")
    nvim.exec_lua("require('vault').setup()")
    nvim.command(f"edit {root / 'index.md'}")

    attached = wait_for(lambda: nvim.exec_lua(
        "local get = vim.lsp.get_clients or vim.lsp.get_active_clients; return #get({ name = 'vault' })") > 0, 5)
    check("server attaches", attached)
    time.sleep(0.5)  # initial scan

    def menu() -> list[str]:
        if not nvim.funcs.pumvisible():
            return []
        return [item["abbr"] for item in nvim.funcs.complete_info(["items"])["items"]]

    def type_keys(keys: str, settle: float = 0.3) -> None:
        nvim.input(keys)
        time.sleep(settle)

    def new_line(keys: str) -> None:
        type_keys("<Esc>o" + keys)

    original_completeopt = nvim.options["completeopt"]

    # Fuzzy matches from anywhere in the vault, best first.
    new_line("see [[foo")
    wait_for(lambda: menu())
    check("[[foo suggests Foo, Foobar and Barfoo in that order",
          menu() == ["Foo", "Foobar", "Barfoo"], menu())

    type_keys("<Tab>")
    check("Tab accepts the first suggestion and closes the link",
          nvim.current.line == "see [[Foo]]", nvim.current.line)
    check("menu closes after accepting", not menu(), menu())
    check("'completeopt' is restored", nvim.options["completeopt"] == original_completeopt,
          nvim.options["completeopt"])

    # The menu narrows as you type.
    new_line("[[f")
    wait_for(lambda: menu())
    type_keys("oob")
    wait_for(lambda: menu() == ["Foobar"])
    check("menu narrows while typing", menu() == ["Foobar"], menu())
    type_keys("<Esc>")

    # Picking another suggestion with <C-n>, then Tab.
    new_line("[[bar")
    wait_for(lambda: menu())
    type_keys("<C-n><C-n><Tab>")
    check("Tab accepts the highlighted suggestion", nvim.current.line == "[[Foobar]]", nvim.current.line)

    # Closing brackets already there (typed first, or auto-paired).
    new_line("[[]]<Left><Left>fo")
    wait_for(lambda: menu())
    type_keys("<Tab>x")
    check("existing ]] isn't doubled and the cursor skips it",
          nvim.current.line == "[[Foo]]x", nvim.current.line)

    # Names with spaces: the query can match any word, and keep matching
    # after a space or a backspace.
    new_line("[[sp")
    wait_for(lambda: menu())
    check("[[sp finds 'The Split Stump'", menu() == ["The Split Stump"], menu())
    type_keys("<BS>")
    check("backspace keeps the menu", "The Split Stump" in menu(), menu())
    type_keys("<C-u>[[the sp")
    wait_for(lambda: menu() == ["The Split Stump"])
    check("[[the sp still matches across the space", menu() == ["The Split Stump"], menu())
    type_keys("<Tab>")
    check("Tab inserts the whole name with spaces", nvim.current.line == "[[The Split Stump]]",
          nvim.current.line)

    # Notes that share a name get a folder prefix where needed.
    new_line("[[dup")
    wait_for(lambda: menu())
    type_keys("<C-n><C-n><Tab>")
    check("second Dup is inserted with its folder", nvim.current.line == "[[c/Dup]]", nvim.current.line)

    # Columns are UTF-16 on the wire; emoji before the link mustn't shift them.
    new_line("🙂🙂 [[nested/fo")
    wait_for(lambda: menu())
    type_keys("<Tab>")
    check("completion lines up after emoji", nvim.current.line == "🙂🙂 [[Foo]]", nvim.current.line)

    # No menu outside a link, and Tab still inserts a tab there.
    new_line("[[Foo]] then")
    check("no menu after a finished link", not menu(), menu())
    type_keys("<Tab>")
    check("Tab without a menu does what it did before", nvim.current.line == "[[Foo]] then\t",
          repr(nvim.current.line))

    new_line("[[nothing like this")
    check("no menu when nothing matches", not menu(), menu())

    # Tab in normal mode still jumps between links.
    type_keys("<Esc>gg0<Tab>")
    check("normal-mode Tab still cycles links", nvim.funcs.getline(".") == "see [[Foo]]",
          nvim.funcs.getline("."))

    try:
        nvim.command("qa!")
    except Exception:  # the connection closes as Neovim exits
        pass
    print("ALL PASSED" if failures == 0 else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
