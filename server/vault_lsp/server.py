"""The vault language server.

Standard LSP features:
  textDocument/definition       follow the [[wikilink]] under the cursor
  textDocument/completion       after "[[", suggest notes whose names fuzzy-match
  textDocument/semanticTokens   mark each [[wikilink]] as existing or missing,
                                so the editor can color them differently

Custom commands (called through workspace/executeCommand by the Lua plugin):
  vault.linkAtCursor        describe the link under the cursor, for missing notes
  vault.createNote          create a missing note in a chosen folder
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from lsprotocol import types
from pygls.lsp.server import LanguageServer
from pygls.uris import from_fs_path, to_fs_path
from pygls.workspace.position_codec import ServerTextPosition

from . import __version__
from .index import CONFIG_FILE, Link, VaultIndex, heading_line, link_at, links_in, open_link_query

# Semantic token legend. Neovim highlights these as @lsp.type.wikilink and,
# for missing notes, also @lsp.typemod.wikilink.missing.
TOKEN_TYPES = ["wikilink"]
TOKEN_MODIFIERS = ["missing"]
MISSING = 1 << TOKEN_MODIFIERS.index("missing")
LINK = re.compile(r"\[\[[\w |/]+\]\]")


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

    def refresh_link_colors(self) -> None:
        """Ask the editor to re-request semantic tokens for open notes.

        Needed when a note is created: links to it elsewhere change from
        missing to existing even though their text didn't change.
        """
        workspace = self.client_capabilities.workspace
        tokens = workspace.semantic_tokens if workspace else None
        if tokens and tokens.refresh_support:
            self.workspace_semantic_tokens_refresh(None)


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
    path = Path(to_fs_path(params.text_document.uri))
    # A buffer for a file that isn't on disk yet gets added when it's saved.
    if ls.index and path.is_file():
        ls.index.add(path)


@server.feature(types.TEXT_DOCUMENT_DID_SAVE)
def on_did_save(ls: VaultServer, params: types.DidSaveTextDocumentParams) -> None:
    if ls.index and ls.index.add(Path(to_fs_path(params.text_document.uri))):
        ls.refresh_link_colors()


@server.feature(
    types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
    types.SemanticTokensLegend(token_types=TOKEN_TYPES, token_modifiers=TOKEN_MODIFIERS),
)
def semantic_tokens(ls: VaultServer, params: types.SemanticTokensParams) -> types.SemanticTokens:
    if ls.index is None:
        return types.SemanticTokens(data=[])
    document = ls.workspace.get_text_document(params.text_document.uri)

    # Each token is five integers: line and start column (both relative to
    # the previous token), length, token type, and a bitmask of modifiers.
    data: list[int] = []
    previous_line = previous_start = 0
    for number, line in enumerate(document.lines):
        for link in links_in(line):
            start = document.position_to_client_units(ServerTextPosition(number, link.start))
            end = document.position_to_client_units(ServerTextPosition(number, link.end))
            line_delta = number - previous_line
            start_delta = start.character - previous_start if line_delta == 0 else start.character
            modifiers = 0 if ls.index.resolve(link.target) else MISSING
            data += [line_delta, start_delta, end.character - start.character, 0, modifiers]
            previous_line, previous_start = number, start.character
    return types.SemanticTokens(data=data)


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


@server.feature(types.TEXT_DOCUMENT_COMPLETION, types.CompletionOptions(trigger_characters=["["]))
def completion(ls: VaultServer, params: types.CompletionParams) -> types.CompletionList | None:
    if ls.index is None:
        return None
    document = ls.workspace.get_text_document(params.text_document.uri)
    if params.position.line >= len(document.lines):
        return None
    position = document.position_from_client_units(params.position)
    line = document.lines[position.line].rstrip("\r\n")
    query = open_link_query(line[: position.character])
    if query is None:
        return None

    # Replace what's been typed after "[[" and close the link, unless the
    # closing brackets are already there (typed earlier, or auto-paired).
    start = document.position_to_client_units(
        ServerTextPosition(position.line, position.character - len(query)))
    replace = types.Range(start=start, end=params.position)
    close = "" if line[position.character:].startswith("]]") else "]]"

    items = []
    for rank, suggestion in enumerate(ls.index.suggest(query)):
        folder = suggestion.path.parent.as_posix()
        items.append(types.CompletionItem(
            label=suggestion.path.stem,
            kind=types.CompletionItemKind.File,
            detail=None if folder == "." else folder,
            # The server has already ranked and filtered; keep its order and
            # make sure clients that filter again don't drop fuzzy matches.
            sort_text=f"{rank:05d}",
            filter_text=query + suggestion.name,
            text_edit=types.TextEdit(range=replace, new_text=suggestion.name + close),
        ))
    # Results depend on the whole query, so ask again as the user types.
    return types.CompletionList(is_incomplete=True, items=items)


@server.feature(types.TEXT_DOCUMENT_DOCUMENT_LINK)
def document_links(params: types.DocumentLinkParams) -> list[types.DocumentLink]:
    """Return a list of links contained in the document."""
    items = []
    document_uri = params.text_document.uri
    document = server.workspace.get_text_document(document_uri)

    for linum, line in enumerate(document.lines):
        for match in LINK.finditer(line):
            start_char, end_char = match.span()
            items.append(
                types.DocumentLink(
                    range=types.Range(
                        start=types.Position(line=linum, character=start_char),
                        end=types.Position(line=linum, character=end_char),
                    ),
                ),
            )

    return items


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
    ls.window_log_message(types.LogMessageParams(type=types.MessageType.Info, message=f"vault-lsp: created {path}"))
    ls.refresh_link_colors()
    return from_fs_path(str(path))


def main() -> None:
    # stdout carries the protocol, so Python logging goes to stderr, which
    # Neovim records in its LSP log as errors. Keep it to real problems:
    # routine messages use window_log_message instead. (pygls also logs every
    # message it sends at INFO level, which would flood the log.)
    logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")
    server.start_io()


if __name__ == "__main__":
    main()
