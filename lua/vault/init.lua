-- vault.nvim: the Neovim half of vault-lsp.
--
-- Starts the language server for markdown files inside a vault (any folder
-- with a vault.json at its root) and adds a "follow link" action that
-- creates missing notes in a folder you pick.

local M = {}

local defaults = {
  -- Command that starts the language server.
  cmd = { "vault-lsp" },
  -- File that marks the root of a vault.
  marker = "vault.json",
  -- Buffer-local key for following links in vault notes; false to disable.
  follow_key = "<return>",
  next_link_key = "<tab>",
  prev_link_key = "<S-tab>",
  -- Show note suggestions while typing after "[[". Set to false if you'd
  -- rather get them from a completion plugin (nvim-cmp, blink.cmp, ...),
  -- which can use the server's standard LSP completion directly.
  complete = true,
  -- Insert-mode key that accepts the highlighted (or first) suggestion.
  -- Without a suggestion menu open, it does whatever it did before.
  accept_key = "<tab>",
}

M.config = vim.deepcopy(defaults)

-- Neovim 0.10 renamed get_active_clients and 0.11 made client methods use ":".
local get_clients = vim.lsp.get_clients or vim.lsp.get_active_clients

local function request(client, method, params, bufnr, callback)
  local function handler(err, result)
    if err then
      vim.notify("vault: " .. (err.message or vim.inspect(err)), vim.log.levels.ERROR)
      return
    end
    if result == vim.NIL then
      result = nil
    end
    callback(result)
  end
  if vim.fn.has("nvim-0.11") == 1 then
    client:request(method, params, handler, bufnr)
  else
    client.request(method, params, handler, bufnr)
  end
end

local function execute(client, bufnr, command, arguments, callback)
  request(client, "workspace/executeCommand", { command = command, arguments = arguments }, bufnr, callback)
end

local function find_root(bufnr)
  local name = vim.api.nvim_buf_get_name(bufnr)
  if name == "" then
    return nil
  end
  local marker = vim.fs.find(M.config.marker, { upward = true, path = vim.fs.dirname(name) })[1]
  return marker and vim.fs.dirname(marker)
end

local function client_for(bufnr)
  return get_clients({ name = "vault", bufnr = bufnr })[1]
end

-- Ask which folder a new note goes in, then call `done(folder)`.
local NEW_FOLDER = "+ New folder…"

local function choose_folder(link, done)
  local choices = vim.list_extend(vim.deepcopy(link.folders), { NEW_FOLDER })
  vim.ui.select(choices, {
    prompt = ("Create '%s' in:"):format(link.target),
    format_item = function(folder)
      return folder == "." and "(vault root)" or folder
    end,
  }, function(choice)
    if choice == NEW_FOLDER then
      vim.ui.input({ prompt = "New folder (relative to the vault root): " }, function(input)
        if input and vim.trim(input) ~= "" then
          done(vim.trim(input))
        end
      end)
    elseif choice then
      done(choice)
    end
  end)
end

local function create_missing_note(client, bufnr, params)
  execute(client, bufnr, "vault.linkAtCursor", { params.textDocument.uri, params.position }, function(link)
    if not link then
      -- Not on a [[link]]: behave like the built-in gf.
      local ok, message = pcall(vim.cmd, "normal! gf")
      if not ok then
        vim.notify(message, vim.log.levels.WARN)
      end
      return
    end
    if link.exists then
      return
    end

    local function create(folder)
      execute(client, bufnr, "vault.createNote", { link.target, folder }, function(uri)
        vim.cmd.edit(vim.fn.fnameescape(vim.uri_to_fname(uri)))
      end)
    end

    if link.needs_folder then
      choose_folder(link, create)
    else
      create(".")
    end
  end)
end

--- Follow the [[wikilink]] under the cursor, creating the note if it's missing.
function M.follow()
  local bufnr = vim.api.nvim_get_current_buf()
  local client = client_for(bufnr)
  if not client then
    vim.notify("vault: this buffer isn't a note in a vault", vim.log.levels.WARN)
    return
  end

  local params = vim.lsp.util.make_position_params(0, client.offset_encoding)
  request(client, "textDocument/definition", params, bufnr, function(result)
    local location = result and (result.uri and result or result[1])
    if location then
      vim.lsp.util.show_document(location, client.offset_encoding, { focus = true })
    else
      create_missing_note(client, bufnr, params)
    end
  end)
end

local function get_cursor_pos()
  local cursor = vim.api.nvim_win_get_cursor(0)
  local line = cursor[1] - 1 -- adjust line number for 0-indexing
  local character = vim.lsp.util.character_offset(0, line, cursor[2], "utf-8")
  return { line = line, character = character }
end

local function greater_position(a, b)
  return (
    a.line > b.line or
    a.line == b.line and a.character > b.character
  )
end

local function lesser_position(a, b)
  return (
    a.line < b.line or
    a.line == b.line and a.character < b.character
  )
end

local function go_to_next_link(links)
  local window = vim.api.nvim_get_current_win()
  local cursor = get_cursor_pos()
  for _, link in ipairs(links) do
    local start = link.range.start
    if greater_position(start, cursor) then
      vim.api.nvim_win_set_cursor(window, {start.line + 1, start.character})
      return
    end
  end
  -- Go back to start
  if #links > 0 then
    local start = links[1].range.start
    vim.api.nvim_win_set_cursor(window, {start.line + 1, start.character})
  end
end

local function go_to_prev_link(links)
  local window = vim.api.nvim_get_current_win()
  local cursor = get_cursor_pos()
  for i = #links, 1, -1 do
    local start = links[i].range.start
    if lesser_position(start, cursor) then
      vim.api.nvim_win_set_cursor(window, {start.line + 1, start.character})
      return
    end
  end
  -- Go back to end
  if #links > 0 then
    local start = links[#links].range.start
    vim.api.nvim_win_set_cursor(window, {start.line + 1, start.character})
  end
end

function M.next_link()
  local bufnr = vim.api.nvim_get_current_buf()
  local client = client_for(bufnr)
  if not client then
    vim.notify("vault: this buffer isn't a note in a vault", vim.log.levels.WARN)
    return
  end

  local params = vim.lsp.util.make_position_params(0, client.offset_encoding)
  request(client, "textDocument/documentLink", params, bufnr, go_to_next_link)
end

function M.prev_link()
  local bufnr = vim.api.nvim_get_current_buf()
  local client = client_for(bufnr)
  if not client then
    vim.notify("vault: this buffer isn't a note in a vault", vim.log.levels.WARN)
    return
  end

  local params = vim.lsp.util.make_position_params(0, client.offset_encoding)
  request(client, "textDocument/documentLink", params, bufnr, go_to_prev_link)
end

-- Completion after "[[" ------------------------------------------------------
--
-- Neovim's built-in completion filters by prefix, so it would hide "Barfoo"
-- when you type "foo". Instead, every keystroke inside an unfinished link
-- asks the server, which does the fuzzy matching, and the popup shows its
-- results unfiltered.

local OPEN_LINK = "%[%[([^%[%]|#]*)$"

local completion = {
  seq = 0,           -- drops replies to requests the user has typed past
  active = false,    -- the popup on screen is ours
  skip_next = false, -- ignore the text change from accepting a suggestion
  saved_completeopt = nil,
}

-- The popup needs to show even one match and select nothing until asked.
-- 'completeopt' is global before Neovim 0.11, so swap it only while our
-- popup is open.
local function use_our_completeopt()
  if completion.saved_completeopt == nil then
    completion.saved_completeopt = vim.o.completeopt
  end
  vim.o.completeopt = "menuone,noselect"
end

local function restore_completeopt()
  if completion.saved_completeopt ~= nil then
    vim.o.completeopt = completion.saved_completeopt
    completion.saved_completeopt = nil
  end
end

local function close_popup()
  if completion.active and vim.fn.pumvisible() == 1 then
    vim.fn.complete(vim.fn.col("."), {})
  end
  completion.active = false
end

local function show_suggestions(start_col, result)
  local items = {}
  for _, item in ipairs(result and (result.items or result) or {}) do
    table.insert(items, {
      word = item.textEdit and item.textEdit.newText or item.insertText or item.label,
      abbr = item.label,
      menu = item.detail,
      equal = 1, -- the server already filtered; don't filter again
      dup = 1,
      user_data = { vault = true },
    })
  end
  if #items == 0 then
    close_popup()
    return
  end
  use_our_completeopt()
  -- complete() ends any popup already open, which runs CompleteDone, so
  -- only mark the new one as ours afterwards.
  vim.fn.complete(start_col, items)
  completion.active = true
end

local function update_suggestions(bufnr)
  if completion.skip_next then
    completion.skip_next = false
    return
  end
  local client = client_for(bufnr)
  if not client or vim.api.nvim_get_current_buf() ~= bufnr then
    return
  end

  local row, col = unpack(vim.api.nvim_win_get_cursor(0))
  local line = vim.api.nvim_get_current_line()
  local query = line:sub(1, col):match(OPEN_LINK)
  completion.seq = completion.seq + 1
  if not query then
    close_popup()
    return
  end

  local seq = completion.seq
  local start_col = col - #query + 1 -- 1-based byte column just after "[["
  local params = vim.lsp.util.make_position_params(0, client.offset_encoding)
  request(client, "textDocument/completion", params, bufnr, function(result)
    -- Ignore stale replies: the user kept typing or left insert mode.
    if seq ~= completion.seq or vim.fn.mode():sub(1, 1) ~= "i" then
      return
    end
    local now_row, now_col = unpack(vim.api.nvim_win_get_cursor(0))
    if now_row ~= row or now_col ~= col or vim.api.nvim_get_current_buf() ~= bufnr then
      return
    end
    show_suggestions(start_col, result)
  end)
end

local function on_complete_done()
  local item = vim.v.completed_item
  local ours = completion.active and type(item) == "table" and type(item.user_data) == "table"
    and item.user_data.vault
  completion.active = false
  -- Restore the user's 'completeopt', unless a new popup replaced ours.
  vim.schedule(function()
    if not completion.active then
      restore_completeopt()
    end
  end)
  if not ours or (item.word or "") == "" then
    return
  end
  -- Accepted a suggestion: don't reopen the menu for the text it inserted,
  -- and step over closing brackets that were already there.
  completion.skip_next = true
  local row, col = unpack(vim.api.nvim_win_get_cursor(0))
  if vim.api.nvim_get_current_line():sub(col + 1, col + 2) == "]]" then
    vim.api.nvim_win_set_cursor(0, { row, col + 2 })
  end
end

-- What `accept_key` did before this plugin mapped it, for when no
-- suggestions are showing.
local function fallback_keys(key, previous)
  if previous and not vim.tbl_isempty(previous) and previous.buffer ~= 1 then
    local keys
    if previous.callback then
      keys = previous.callback()
      if previous.expr ~= 1 then
        return ""
      end
    elseif previous.rhs and previous.rhs ~= "" then
      keys = previous.expr == 1 and vim.api.nvim_eval(previous.rhs) or previous.rhs
    end
    if keys then
      -- Expression mappings may return "<Tab>"-style key names (Neovim
      -- 0.11's own snippet mapping does); plain mappings always use them.
      if previous.expr ~= 1 or previous.replace_keycodes == 1 then
        keys = vim.api.nvim_replace_termcodes(keys, true, true, true)
      end
      return keys
    end
  end
  return vim.api.nvim_replace_termcodes(key, true, true, true)
end

local function enable_completion(bufnr)
  local group = vim.api.nvim_create_augroup("vault_complete_" .. bufnr, { clear = true })
  vim.api.nvim_create_autocmd({ "TextChangedI", "TextChangedP" }, {
    group = group,
    buffer = bufnr,
    callback = function(args)
      -- Moving through the menu inserts each suggestion in turn; that isn't typing.
      if args.event == "TextChangedP" and vim.fn.complete_info({ "selected" }).selected ~= -1 then
        return
      end
      update_suggestions(bufnr)
    end,
  })
  vim.api.nvim_create_autocmd("CompleteDone", { group = group, buffer = bufnr, callback = on_complete_done })
  vim.api.nvim_create_autocmd("InsertLeave", {
    group = group,
    buffer = bufnr,
    callback = function()
      completion.seq = completion.seq + 1
      completion.active = false
      completion.skip_next = false
      restore_completeopt()
    end,
  })

  local key = M.config.accept_key
  if key then
    local previous = vim.fn.maparg(key, "i", false, true)
    vim.keymap.set("i", key, function()
      if completion.active and vim.fn.pumvisible() == 1 then
        local selected = vim.fn.complete_info({ "selected" }).selected
        local keys = selected == -1 and "<C-n><C-y>" or "<C-y>"
        return vim.api.nvim_replace_termcodes(keys, true, true, true)
      end
      return fallback_keys(key, previous)
    end, { buffer = bufnr, expr = true, replace_keycodes = false, desc = "Accept [[wikilink]] suggestion" })
  end
end

--- Start (or reuse) the language server for `bufnr` if it's inside a vault.
function M.attach(bufnr)
  local root = find_root(bufnr)
  if not root then
    return
  end
  if vim.fn.executable(M.config.cmd[1]) ~= 1 then
    vim.notify_once(("vault: '%s' isn't on your PATH"):format(M.config.cmd[1]), vim.log.levels.ERROR)
    return
  end

  vim.lsp.start({
    name = "vault",
    cmd = M.config.cmd,
    root_dir = root,
    on_attach = function(_, attached)
      if M.config.follow_key then
        vim.keymap.set("n", M.config.follow_key, M.follow, { buffer = attached, desc = "Follow [[wikilink]]" })
      end
      if M.config.next_link_key then
        vim.keymap.set("n", M.config.next_link_key, M.next_link, { buffer = attached, desc = "Go to next [[wikilink]]" })
      end
      if M.config.prev_link_key then
        vim.keymap.set("n", M.config.prev_link_key, M.prev_link, { buffer = attached, desc = "Go to previous [[wikilink]]" })
      end
      if M.config.complete then
        enable_completion(attached)
      end
    end,
  }, { bufnr = bufnr })
end

-- Colors for links. The server tags every [[link]] as a "wikilink" semantic
-- token, adding the "missing" modifier when the note doesn't exist, and
-- Neovim highlights them with these groups. `default = true` means a
-- colorscheme or your own nvim_set_hl() call takes precedence.
local function set_highlights()
  vim.api.nvim_set_hl(0, "@lsp.type.wikilink", { link = "Underlined", default = true })
  vim.api.nvim_set_hl(0, "@lsp.typemod.wikilink.missing", { link = "Comment", default = true })
end

function M.setup(opts)
  M.config = vim.tbl_deep_extend("force", vim.deepcopy(defaults), opts or {})

  local group = vim.api.nvim_create_augroup("vault", { clear = true })

  set_highlights()
  -- :colorscheme clears highlight groups, so restore the defaults after it.
  vim.api.nvim_create_autocmd("ColorScheme", { group = group, callback = set_highlights })
  vim.api.nvim_create_autocmd("FileType", {
    group = group,
    pattern = "markdown",
    callback = function(args)
      M.attach(args.buf)
    end,
  })
  vim.api.nvim_create_user_command("VaultFollow", M.follow, { desc = "Follow the [[wikilink]] under the cursor" })

  -- Markdown buffers opened before setup() ran.
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_loaded(buf) and vim.bo[buf].filetype == "markdown" then
      M.attach(buf)
    end
  end
end

return M
