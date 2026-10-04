# vault.nvim

A note-taking plugin for Neovim, built as a language server (`vault-lsp`, in
Python) plus a thin Lua plugin.

A **vault** is any folder with a `vault.json` file at its root. The file can
contain just `{}` for now; the server reads it at startup so there's a place
for settings later.

## What works so far

- The server starts automatically when you open a markdown file inside a vault.
- `gf` on a `[[wikilink]]` opens the note it points to.
  - `[[Note]]`, `[[Note|alias]]` and `[[Note#Heading]]` (jumps to the heading).
  - Matching is case-insensitive.
  - If two notes share a name, `[[Note]]` picks the one with the shortest path,
    and `[[folder/Note]]` picks a specific one.
- `gf` on a link to a note that doesn't exist asks which folder to create it
  in (or lets you type a new one), then creates and opens it. A link that
  names its own folder, like `[[ideas/Thing]]`, is created there without asking.
- `gf` anywhere else behaves like the built-in `gf`.
- Standard LSP "go to definition" (`vim.lsp.buf.definition()`) also follows
  links, so existing LSP keymaps work too.

## Install

**1. Install the server** so `vault-lsp` is on your `PATH`. With
[uv](https://docs.astral.sh/uv/):

```sh
uv tool install --editable ./server
```

or with pip in a virtualenv: `pip install -e ./server`. Editable installs mean
changes to the Python code take effect the next time Neovim starts the server.

**2. Add the plugin.** With lazy.nvim, pointing at your local checkout:

```lua
{
  dir = "~/code/vault.nvim",
  ft = "markdown",
  opts = {
    -- defaults:
    -- cmd = { "vault-lsp" },
    -- marker = "vault.json",
    -- follow_key = "gf",   -- false to disable
  },
}
```

Without a plugin manager: add the folder to `runtimepath` and call
`require("vault").setup()`.

**3. Make a vault:** `mkdir ~/notes && echo '{}' > ~/notes/vault.json`

## How it fits together

```
 Neovim (lua/vault/init.lua)                 vault-lsp (server/vault_lsp/)
 ───────────────────────────                 ─────────────────────────────
 FileType markdown → find vault.json
   → vim.lsp.start()  ─────── initialize ──→ read vault.json, scan *.md into index

 gf → textDocument/definition ────────────→ link under cursor → resolve
      ← Location (jump) ──────────────────── or null if the note is missing

      if null:
      vault.linkAtCursor ─────────────────→ { target, needs_folder, folders }
      vim.ui.select a folder
      vault.createNote(target, folder) ───→ write the file, add to index
      ← uri, :edit it
```

The folder prompt lives on the Lua side because a definition request can only
return a location; it can't ask questions. The prompt uses `vim.ui.select`, so
it picks up telescope, fzf-lua, dressing.nvim or snacks if you've configured one.

- `server/vault_lsp/index.py`: link parsing, the index and note creation. It
  has no LSP code, which keeps it easy to test.
- `server/vault_lsp/server.py`: the LSP handlers.
- `lua/vault/init.lua`: starting the server, `gf`, and the folder prompt.

## Debugging

- `:LspInfo` (with nvim-lspconfig) or `:lua print(vim.inspect(vim.lsp.get_active_clients()))`
  shows whether the server attached.
- Server logs go to Neovim's LSP log: `:lua vim.cmd.edit(vim.lsp.get_log_path())`.
- You can run `vault-lsp` in a terminal; it will sit waiting for JSON-RPC on
  stdin, which at least proves it starts.

## Tests

```sh
cd server && pytest                                     # index and parsing
nvim --headless -u NONE -c "luafile tests/e2e.lua"      # real Neovim + real server
```

The end-to-end test builds a throwaway vault and drives the plugin. It
passes on Neovim 0.9.5 and 0.12.

## Known limits of the stub

- The index only grows: files deleted or renamed outside Neovim are skipped
  when resolving, but stay in memory until the server restarts.
- New files appear in the index when opened or saved in Neovim, or created by
  `gf`. Files added by other programs need a server restart (`:LspRestart`,
  or reopen Neovim).

## Next steps

1. Completion after `[[`, from the index (`textDocument/completion`).
2. Backlinks via `textDocument/references`.
3. Watch the vault for outside changes (`workspace/didChangeWatchedFiles`).
4. Move the index into SQLite (links, tags, frontmatter tables).
5. Rename a note and update every link to it (`textDocument/rename`).
