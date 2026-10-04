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
  follow_key = "gf",
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
