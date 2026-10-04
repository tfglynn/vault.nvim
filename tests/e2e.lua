-- End-to-end check: real Neovim + real vault-lsp against a throwaway vault.
-- Run from the repo root:
--   nvim -u NONE -c "luafile tests/e2e.lua" 2>results.txt
-- Results go to stderr and the exit code is non-zero on failure. Run it with
-- a UI rather than --headless: Neovim 0.9 only draws link colors on redraw.

local repo = vim.fn.getcwd()
vim.opt.runtimepath:prepend(repo)
vim.cmd("filetype on")

local failures = 0
local function check(name, ok, detail)
  io.stderr:write((ok and "PASS " or "FAIL ") .. name .. (ok and "" or ("  -- " .. tostring(detail))) .. "\n")
  if not ok then
    failures = failures + 1
  end
end

-- Build a vault.
local root = vim.fn.resolve(vim.fn.tempname())
local function write(rel, lines)
  vim.fn.mkdir(vim.fs.dirname(root .. "/" .. rel), "p")
  vim.fn.writefile(lines, root .. "/" .. rel)
end
write("vault.json", { "{}" })
write("index.md", {
  "# Index",
  "Go to [[Alpha#Goals]] or [[alpha]].",
  "Missing: [[Brand New]] and [[ideas/Deep Idea]] and [[Fresh Folder Note]].",
  "Plain text here.",
  "Emoji 🙂🙂 then [[Alpha]] ok",
})
write("projects/Alpha.md", { "# Alpha", "", "## Goals", "ship it" })

require("vault").setup()

local function current() return vim.fn.resolve(vim.api.nvim_buf_get_name(0)) end
local function cursor_on(text)
  if current() ~= root .. "/index.md" then
    vim.cmd.edit(vim.fn.fnameescape(root .. "/index.md"))
  end
  vim.fn.cursor(1, 1)
  assert(vim.fn.search("\\V" .. text, "cw") > 0, "text not found: " .. text)
end
-- Semantic-token highlight groups at the start of `text` in index.md.
local function groups_at(text)
  cursor_on(text)
  local row, col = unpack(vim.api.nvim_win_get_cursor(0))
  -- Neovim 0.9 only places token highlights while redrawing the screen.
  vim.cmd.redraw()
  local groups = {}
  for _, token in ipairs(vim.inspect_pos(0, row - 1, col).semantic_tokens) do
    table.insert(groups, token.opts.hl_group)
  end
  return table.concat(groups, " ")
end
local function wait_groups(text, pattern, plain_absent)
  local got
  vim.wait(3000, function()
    got = groups_at(text)
    if plain_absent then return got ~= "" and not got:find(pattern, 1, true) end
    return got:find(pattern, 1, true) ~= nil
  end, 50)
  return got
end

local function follow_and_wait(expected)
  require("vault").follow()
  return vim.wait(3000, function() return current() == expected end, 20)
end

vim.cmd.edit(vim.fn.fnameescape(root .. "/index.md"))
local attached = vim.wait(5000, function()
  local get = vim.lsp.get_clients or vim.lsp.get_active_clients
  return #get({ name = "vault", bufnr = 0 }) > 0
end, 50)
check("server attaches inside a vault", attached)
vim.wait(1000) -- let the initial scan finish

-- 0. Link colors.
local g = wait_groups("Alpha#Goals", "@lsp.type.wikilink")
check("existing link is a wikilink token", g:find("@lsp.type.wikilink", 1, true) and not g:find("missing"), g)
g = wait_groups("Brand New", "@lsp.typemod.wikilink.missing")
check("missing link has the missing modifier", g:find("@lsp.typemod.wikilink.missing", 1, true), g)
g = wait_groups("Alpha]] ok", "@lsp.type.wikilink")
check("token lines up after emoji", g:find("@lsp.type.wikilink", 1, true), g)
check("no token on plain text after emoji", groups_at("then") == "", groups_at("then"))
check("no token after the link", groups_at("ok") == "", groups_at("ok"))
check("default highlight for wikilinks", vim.api.nvim_get_hl(0, { name = "@lsp.type.wikilink" }).link == "Underlined")

-- 1. Existing link with a heading.
cursor_on("Alpha#Goals")
local ok = follow_and_wait(root .. "/projects/Alpha.md")
check("follows [[Alpha#Goals]]", ok, current())
check("lands on the heading line", vim.fn.line(".") == 3, vim.fn.line("."))

-- 2. Case-insensitive link.
cursor_on("alpha]]")
check("follows [[alpha]]", follow_and_wait(root .. "/projects/Alpha.md"), current())

-- 3. Missing link -> pick an existing folder.
local offered
vim.ui.select = function(items, opts, cb)
  offered = vim.tbl_map(opts.format_item, items)
  cb("projects")
end
cursor_on("Brand New")
check("creates missing note in chosen folder", follow_and_wait(root .. "/projects/Brand New.md"), current())
check("offered root, projects and new-folder", offered and vim.deep_equal(offered, { "(vault root)", "projects", "+ New folder…" }), vim.inspect(offered))
check("new note has a title", vim.fn.readfile(root .. "/projects/Brand New.md")[1] == "# Brand New")

g = wait_groups("Brand New", "missing", true)
check("creating a note recolors links to it", g ~= "" and not g:find("missing"), g)

-- 4. Following the same link again now resolves without prompting.
local prompted = false
vim.ui.select = function() prompted = true end
cursor_on("Brand New")
check("new note is indexed", follow_and_wait(root .. "/projects/Brand New.md") and not prompted)

-- 5. Missing link -> new folder via vim.ui.input.
vim.ui.select = function(items, _, cb) cb(items[#items]) end
vim.ui.input = function(_, cb) cb("inbox/later") end
cursor_on("Fresh Folder Note")
check("creates note in a brand-new folder", follow_and_wait(root .. "/inbox/later/Fresh Folder Note.md"), current())

-- 6. Link that names its own folder skips the prompt.
prompted = false
vim.ui.select = function() prompted = true end
cursor_on("Deep Idea")
check("[[ideas/Deep Idea]] creates without asking", follow_and_wait(root .. "/ideas/Deep Idea.md") and not prompted, current())

-- 7. Not on a link: falls back to gf, which fails quietly with a warning.
local warned
local notify = vim.notify
vim.notify = function(msg) warned = msg end
cursor_on("Plain")
require("vault").follow()
vim.wait(1000, function() return warned ~= nil end, 20)
vim.notify = notify
check("off-link falls back to gf", warned ~= nil and current() == root .. "/index.md", warned)

-- 8. Markdown outside a vault doesn't start the server.
local outside = vim.fn.tempname() .. ".md"
vim.fn.writefile({ "# x" }, outside)
vim.cmd.edit(vim.fn.fnameescape(outside))
vim.wait(300)
local get = vim.lsp.get_clients or vim.lsp.get_active_clients
check("no server outside a vault", #get({ name = "vault", bufnr = 0 }) == 0)

io.stderr:write((failures == 0 and "ALL PASSED" or (failures .. " FAILED")) .. "\n")
vim.cmd(failures == 0 and "qa!" or "cq!")
