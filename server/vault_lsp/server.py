"""The vault language server.

Standard LSP features:
  textDocument/definition   follow the [[wikilink]] under the cursor

Custom commands (called through workspace/executeCommand by the Lua plugin):
  vault.linkAtCursor        describe the link under the cursor, for missing notes
  vault.createNote          create a missing note in a chosen folder
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from lsprotocol import types
from pygls.lsp.server import LanguageServer
from pygls.uris import from_fs_path, to_fs_path

from . import __version__
from .index import CONFIG_FILE, Link, VaultIndex, heading_line, link_at

log = logging.getLogger("vault-lsp")


class VaultServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__("vault-lsp", __version__)
        self.index: VaultIndex | None = None
        self.config: dict = {}

    def link_under_cursor(self, uri: str, position: types.Position) -> Link | None:
        document = self.workspace.get_text_document(uri)
        if position.line >= len(document.lines):
            return None
        # The client counts columns in UTF-16 units; Python strings count code
        # points. The document's codec converts between them.
        server_position = document.position_from_client_units(position)
        return link_at(document.lines[server_position.line], server_position.character)


server = VaultServer()


@server.feature(types.INITIALIZED)
def on_initialized(ls: VaultServer, params: types.InitializedParams) -> None:
    root = ls.workspace.root_path
    if root is None:
        ls.window_show_message(types.ShowMessageParams(
            type=types.MessageType.Error, message="vault-lsp: no vault root was given"))
        return

    config_path = Path(root) / CONFIG_FILE
    try:
        text = config_path.read_text(encoding="utf-8").strip()
        ls.config = json.loads(text) if text else {}
    except (OSError, json.JSONDecodeError) as error:
        ls.window_show_message(types.ShowMessageParams(
            type=types.MessageType.Warning, message=f"vault-lsp: couldn't read {config_path}: {error}"))

    ls.index = VaultIndex(Path(root))
    count = ls.index.scan()
    ls.window_log_message(types.LogMessageParams(
        type=types.MessageType.Info, message=f"vault-lsp: indexed {count} notes in {root}"))


@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
def on_did_open(ls: VaultServer, params: types.DidOpenTextDocumentParams) -> None:
    if ls.index:
        ls.index.add(Path(to_fs_path(params.text_document.uri)))


@server.feature(types.TEXT_DOCUMENT_DID_SAVE)
def on_did_save(ls: VaultServer, params: types.DidSaveTextDocumentParams) -> None:
    # A note created with :edit only exists on disk once it's saved.
    if ls.index:
        ls.index.add(Path(to_fs_path(params.text_document.uri)))


@server.feature(types.TEXT_DOCUMENT_DEFINITION)
def definition(ls: VaultServer, params: types.DefinitionParams) -> types.Location | None:
    if ls.index is None:
        return None
    link = ls.link_under_cursor(params.text_document.uri, params.position)
    if link is None:
        return None
    path = ls.index.resolve(link.target)
    if path is None:
        return None  # missing note: the client asks vault.linkAtCursor next

    line = heading_line(path, link.heading) if link.heading else 0
    start = types.Position(line=line, character=0)
    return types.Location(uri=from_fs_path(str(path)), range=types.Range(start=start, end=start))


@server.command("vault.linkAtCursor")
def link_at_cursor(ls: VaultServer, uri: str, position: types.Position) -> dict | None:
    """Describe the link under the cursor so the client can offer to create it."""
    if ls.index is None:
        return None
    link = ls.link_under_cursor(uri, position)
    if link is None:
        return None
    return {
        "target": link.target,
        "exists": ls.index.resolve(link.target) is not None,
        # [[projects/idea]] already says where it lives; [[idea]] doesn't.
        "needs_folder": "/" not in link.target,
        "folders": ls.index.folders(),
    }


@server.command("vault.createNote")
def create_note(ls: VaultServer, target: str, folder: str) -> str:
    """Create the note for `target` inside `folder`. Returns its URI."""
    if ls.index is None:
        raise RuntimeError("vault-lsp hasn't finished starting")
    path = ls.index.create_note(target, folder)
    log.info("created %s", path)
    return from_fs_path(str(path))


def main() -> None:
    # stdout carries the protocol, so logs go to stderr. Neovim records them
    # in its LSP log (:lua vim.cmd.edit(vim.lsp.get_log_path())).
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    server.start_io()


if __name__ == "__main__":
    main()
