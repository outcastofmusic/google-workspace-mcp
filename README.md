# google-workspace-mcp

Six aligned MCP servers for Google Workspace: **Gmail**, **Calendar**,
**Sheets**, **Docs**, **Drive**, and **Slides**. They share one credential store and one runtime core, so they behave
identically and support:

- **Persistent auth**: log in once; tokens are reused and access tokens refresh
  silently. Server restarts never re-prompt for consent.
- **Multiple accounts in parallel**: every tool takes an `account` argument
  (email or alias). Calls for different accounts use separate, cached clients and
  never interfere.
- **Many operations**: ~44 Gmail tools, ~27 Calendar tools, ~65 Sheets tools (incl. text editing),
  ~40 Docs tools, ~29 Drive tools, 8 Slides tools, plus shared `list_accounts` / `whoami` /
  `auth_status` on every server.

## Architecture

```
google-auth-core      shared ~/.google token store + authorized-service cache
        |                     (also used by gmail-cli and google-calendar-cli)
        v
google_workspace_mcp/
  core/      build_server, account resolution + warm-client cache, error
             mapping, the read-only gate, the response envelope, common tools
  gmail/     wraps gmail_cli.api.GmailAPI            -> gmail-mcp
  calendar/  wraps google_calendar_cli.api.CalendarAPI -> gcal-mcp
  sheets/    SheetsAPI (Sheets API v4)               -> gsheets-mcp
  docs/      DocsAPI (Docs API v1)                   -> gdocs-mcp
  drive/     DriveAPI (Drive API v3)                 -> gdrive-mcp
             + extract.py: PDF (pypdf), docx, pptx, xlsx text
  slides/    SlidesAPI (Slides API v1)               -> gslides-mcp
  contacts/  PeopleAPI (People API v1)               -> search_contacts on gmail-mcp + gcal-mcp
```

Every tool: takes `account`, resolves it, gets a cached per-account client,
runs the Google call through shared error mapping, and returns
`{"ok": true, "account": "<resolved>", "data": ...}`.

### Docs (`gdocs-mcp`)

- **Markdown in/out** — `create_document_from_markdown`, `append_markdown`, and
  `replace_document_with_markdown` let an agent write plain markdown while Google
  converts it to native headings, lists, links, and tables; `read_document_as_markdown`
  round-trips it back.
- **Edits that can't land at stale positions**: `find_text` returns each match's
  start/end index, a short context snippet, and the doc's `revisionId` without
  dumping the whole document (table cells included, UTF-16 indices). `format_matches`
  styles or links every match in one call, resolving positions on the server and
  guarding the write with that read's revision, e.g. `pattern="LIG-\d+"`, `regex=true`,
  `link_url_template="https://linear.app/acme/issue/{match}"`; `clear_link=true`
  removes links instead. Every index-based write (`batch_update`, `format_text`,
  `insert_text`, `delete_range`, table and paragraph tools) accepts
  `required_revision_id`: pass the `revisionId` from `find_text`, `get_content_map`
  or `get_document`, and if the doc changed since, the write fails with "re-read"
  instead of hitting shifted text.

### Sheets (`gsheets-mcp`)

- **Human-readable layouts** — `write_table` writes data and formats it as a native
  table (or banded range) in one call; `optimize_layout` sizes every column to its
  content with a width cap, wraps only what must wrap, and auto-fits row heights.
  Header freezing is opt-in.
- **Rules and guards without raw batch_update**: `add_conditional_format` (value
  comparisons, text contains, custom formula, color scale), `list_conditional_formats`,
  `delete_conditional_format`; `protect_range` (a range or a whole sheet, editors or
  warning-only), `list_protected_ranges`, `unprotect_range`; and `add_named_range`,
  `list_named_ranges`, `update_named_range`, `delete_named_range`.

### Drive (`gdrive-mcp`)

- **Document text inline**: `read_file_text` returns the text of plain-text files, PDF, Word (docx), PowerPoint (pptx) and Excel (xlsx) files, and native Docs, Sheets and Slides, without saving anything to disk. Pages, slides and sheets are marked `[page 3]`, `[slide 3]` and `[sheet Budget]`; speaker notes follow their slide; spreadsheet rows are tab-separated and every sheet is included. `pages="3-7"` (or `"1,4,9-10"`) reads part of a PDF or presentation, and the result reports `total` and `selected`. Long text pages with `max_chars`/`offset`. Documents are read up to 25 MiB and plain-text files up to 1 MiB; anything larger, and images, need `download_file`. Scanned PDFs have no text layer: their pages come back marked, with a note.
- **Several files at once**: `read_files(file_ids=[...])` reads each file as above, cuts each at `max_chars_per_file` (default 100,000) and stops at `max_chars_total` (default 400,000), listing the rest under `skipped` and failures under `errors`.
- **Folder trees**: `list_folder_tree(folder_id=...)` lists every file under a folder and its subfolders, with `path`, `modifiedTime`, `md5Checksum` and `webViewLink`, up to `max_files` (default 500) and `max_depth` (default 10).
- **Untrusted files**: Office XML that declares a DOCTYPE is refused, an Office file may expand to at most 200 MiB, and password-protected PDFs fail with an error that says so.

### Slides (`gslides-mcp`)

- **A deck from an outline in one call**: `create_presentation_from_outline` takes a
  `title`, an optional `folder_id` and a list of `slides`, each `{title, body?, notes?, layout?}`.
  A body is plain lines: a line that starts with `- ` is a bullet, and two spaces of indent
  before the dash nest it one level; any other line is a paragraph. `notes` become the speaker
  notes. Slides use the deck's own layouts, so the theme applies: `layout` names one of them
  (`TITLE`, `TITLE_AND_BODY`, `SECTION_HEADER`, `TITLE_ONLY`, `BLANK` in the default theme, or
  its display name); the default is `TITLE_AND_BODY`, or `TITLE` for a slide with no body. An
  unknown layout fails the call and lists the deck's layouts. With `folder_id` the deck is moved
  into that folder, and trashed if the move or the build fails. `add_slides` adds the same items
  to an existing deck, at the end or at `position`. A call takes at most 100 slides.

```json
{
  "name": "create_presentation_from_outline",
  "arguments": {
    "title": "DDB status",
    "folder_id": "0BwDdbFolderId",
    "slides": [
      {"title": "DDB status", "body": "October 2026", "layout": "TITLE"},
      {"title": "Done", "body": "OCR pipeline\n- done\n  - OCR timeout fixed\n  - retry on 429",
       "notes": "Mention the OCR fix"},
      {"title": "Next", "body": "- ship the expense API\n- review with DDB"}
    ]
  }
}
```

- **Bounded reads**: `read_presentation` returns the deck's title, `revisionId`, its layouts,
  and per slide its `id`, `layout`, `title`, `body` (in the outline format above), the text of
  other shapes and tables under `other`, and `notes`. It returns whole slides up to `max_chars`
  (default 20,000); with `truncated: true`, call again with `offset` set to `next_offset`, the
  index of the first slide left out. The deck's title is cut at 300 characters and its layouts at
  30 names, since they come from the deck; one slide larger than the budget comes back cut, with
  `cut: true`.
- **Edits that do not overwrite the user's**: `update_slide_text` replaces a slide's title, body
  or notes (each only when given; `""` clears it), `replace_text_in_deck` replaces every match in the
  deck and returns `occurrencesChanged`, and `delete_slide` deletes one slide. Every write takes
  `revision_id`: pass the `revisionId` of the read the write follows, and if someone edited the
  deck since, the call fails with "changed after revision" and writes nothing. Each write returns
  the new `revisionId`. `batch_update` sends raw Slides requests (images, tables, shapes,
  styling) for what the other tools do not cover.
  Under a client scope with Drive folders, every file a raw request names (a linked Sheets
  chart, a Drive video) must lie inside the scope too.
- **Setup**: the token's `drive` scope covers the Slides API, so no account logs in again, but
  enable the **Google Slides API** in your OAuth client's Cloud project. Export a deck to PDF
  or pptx with the Drive server's `export_file`; `read_file_text` also reads a deck's text.

### Contact lookup (`gmail-mcp`, `gcal-mcp`)

- **Name to email** — `search_contacts(query="Dana")` lets an agent told "email Dana"
  or "invite Dana" find the address first. It searches saved contacts, people the
  account has emailed with ("other contacts"), and, on Workspace accounts, the
  company directory. Each result has `name`, `emails`, `phone`, and `source`
  (`contact`, `other`, `directory`); `skipped` lists any source that could not be
  searched and why (a personal Gmail account has no directory).
- **Setup** — enable the **People API** in your OAuth client's Cloud project. The
  token needs `contacts.readonly`, `contacts.other.readonly`, and
  `directory.readonly`; tokens created before these were added must be refreshed
  with `google-auth login <account>`. Until then the tool returns an error naming
  the missing scopes.

## Install

This fork is installed from its git tags, not from PyPI: the name
`google-workspace-suite-mcp` on PyPI is the upstream project, which has none
of this fork's changes. Each release is a tag `vX.Y.Z` with its wheel attached
to the GitHub release.

```bash
pip install "git+https://github.com/outcastofmusic/google-workspace-mcp@v0.8.0"
```

This pulls in the shared `google-auth-core` token store and the
`gmail-cli-oauth` / `google-calendar-cli` clients automatically, and installs
console scripts onto your PATH: the six servers (`gmail-mcp`, `gcal-mcp`,
`gsheets-mcp`, `gdocs-mcp`, `gdrive-mcp`, `gslides-mcp`), the combined
`gworkspace-mcp`, `gcal-busy-sync`, and the `google-auth` CLI used to log in
(see below).

If you prefer an isolated install with [pipx](https://pipx.pypa.io):

```bash
pipx install "git+https://github.com/outcastofmusic/google-workspace-mcp@v0.8.0"
```

For local development from a clone:

```bash
pip install -e .[dev]
```

## Authenticate (once)

Auth is **out-of-band**: the servers are non-interactive token consumers. You
log in once with the bundled `google-auth` CLI, which writes a unified token to
`~/.google/`; every server then reads and silently refreshes it.

**Quick start (recommended):**

```bash
google-auth setup you@example.com   # wizard: OAuth client → login → API check → .mcp.json
google-auth doctor                  # verify setup anytime
```

The wizard walks you through creating a Desktop OAuth client in Google Cloud
Console, logging in, confirming the five APIs are enabled (enable the Google
Slides API yourself if you use `gslides-mcp`), and prints
ready-to-paste `.mcp.json` snippets that point at locally installed scripts.
To run the servers through `uvx` instead, use the pinned form below.
The wizard and `doctor` come from `google-auth-core` and suggest
`pip install google-workspace-suite-mcp` when the scripts are missing; that
name is upstream's package, so install from the git tag above instead.

You bring your **own** Google OAuth client. There is no shared client embedded
in the package: Gmail and Drive are Google "restricted" scopes, so a shared
published client would require Google's security assessment. Your own client
used just by you skips that review.

**Avoid "Testing" status.** Google expires refresh tokens after 7 days for an
External app left in Testing, so you'd have to re-run `google-auth login` every
week. Instead:

- **Google Workspace account:** set the consent screen's user type to
  **Internal**. No test users, no warning screen, tokens don't expire weekly.
  This needs the Cloud project to belong to your Workspace organization, and
  only accounts in that organization can sign in.
- **Personal Gmail, or accounts from several domains:** use **External**, then
  set publishing status to **In production** (no need to submit for
  verification). Each `google-auth login` shows an "unverified app" warning:
  click **Advanced**, then **Go to (your app name) (unsafe)**. Google caps
  unverified apps at 100 users in total, which doesn't matter for personal use.

Already in Testing? Switch to In production and run `google-auth login` again
for each account to get a token that doesn't expire weekly.

### Manual setup (appendix)

**1. Create an OAuth client** in the [Google Cloud Console](https://console.cloud.google.com):

- Create or select a project.
- Enable the APIs you need: Gmail, Google Calendar, Google Sheets, Google Docs,
  Google Drive, Google Slides, and People (for contact lookup).
- *APIs & Services → OAuth consent screen*: pick **Internal** (Workspace
  account) or **External** (personal Gmail). For External, click
  **Publish app** so the status is **In production** (see above for why).
- *APIs & Services → Credentials → Create credentials → OAuth client ID* →
  application type **Desktop app** → **Download JSON**.
- Save that file as `~/.google/credentials.json`.

**2. Log in:**

```bash
google-auth login you@example.com      # opens a browser, writes a unified token
google-auth alias work you@example.com # optional short alias
google-auth login you@personal.com     # add more accounts
google-auth list                       # show accounts, default, aliases
google-auth status                     # show token + scope health
google-auth doctor                     # diagnose setup issues
```

A single login grants Gmail + Calendar + Sheets + Docs + Drive scopes, shared by
both the CLIs and the MCP servers. If a token is ever missing or scope-short, a
tool returns an actionable error (`Run: google-auth login <account>`) rather
than blocking the protocol.

## Register with Claude

`claude mcp add` or a `.mcp.json` like:

```json
{
  "mcpServers": {
    "google-workspace": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/outcastofmusic/google-workspace-mcp@v0.8.0", "gworkspace-mcp"]
    }
  }
}
```

`gworkspace-mcp` serves all six services from one process (~130MB instead of
~450MB for five separate servers). Tools keep their names; the few that two
services share get the service as a prefix (`drive_list_changes`,
`sheets_add_comment`, `slides_batch_update`), whichever toolset is on. If you only want some services, register their servers
separately instead: `gmail-mcp`, `gcal-mcp`, `gsheets-mcp`, `gdocs-mcp`,
`gdrive-mcp`, `gslides-mcp`, each as its own entry with the same `uvx --from`
args.

The `@v0.8.0` tag means launches come from uv's cache after the first one:
fast startup, works offline until the uv cache is cleaned, and a new release
only runs once you choose it. To upgrade, change the tag and restart the
client. To follow `main` instead, which uv fetches again on every launch:

```json
"args": ["--refresh", "--from", "git+https://github.com/outcastofmusic/google-workspace-mcp@main", "gworkspace-mcp"]
```

uv needs `git` on the machine to install from a tag.

GUI clients such as Claude Desktop and Cursor do not inherit your shell PATH,
so use the absolute `uvx` path (`which uvx`). Register at user scope to make
the servers available in every project.

Restart Claude after editing.

## Multiple accounts

Pass `account` to any tool (omit it to use the default):

```
list_messages(account="work", query="is:unread")
create_event(account="you@personal.com", summary="Dinner", start_time=..., end_time=...)
read_range(account="work", spreadsheet_id="...", range="Sheet1!A1:C10")
```

## Search across accounts

Three read tools query every configured account in one call and merge the results; pass `accounts=["work", "me@home.com"]` to limit them:

- `search_messages_all_accounts(query=...)` (Gmail): newest first.
- `list_events_all_accounts(time_min=..., time_max=...)` (Calendar): each account's primary calendar as one agenda, sorted by start time.
- `search_files_all_accounts(name=..., full_text=..., ...)` (Drive): newest first.

Every item carries an `account` key. An account that fails appears under `errors`; the other accounts still return.

## Busy-time sync across calendars

Each calendar only knows its own meetings, so people booking your work calendar cannot see your personal appointments. `sync_busy_blocks` (Calendar server) and the `gcal-busy-sync` command mirror busy time: every busy event on one account's calendar becomes a private "Busy" block on the calendars of all your other accounts.

- **Only the time slot is copied.** A block has no title, description, location or attendees from its source, and its visibility is private.
- **Real events are never changed.** Blocks carry a private marker; the sync creates, moves and deletes only marked events, and never treats a block as a source.
- **Skipped:** free (transparent) events, declined invitations, cancelled events, working-location events and, unless `include_all_day` is set, all-day events. A meeting already on a calendar gets no block there.
- **One calendar per account:** the primary calendar, unless you name another. That calendar is both read for busy time and written with blocks. Find IDs with `list_calendars`.
- **Window:** now to `days_ahead` days ahead (default 14, maximum 90).
- **Failure-safe:** if any account cannot be listed, that run creates and moves blocks but deletes none.

Run it once to check the plan, then on a schedule:

```bash
gcal-busy-sync --dry-run                 # show what would change
gcal-busy-sync                           # sync once (all accounts)
gcal-busy-sync --accounts work personal  # only these accounts
gcal-busy-sync --calendar work=team@group.calendar.google.com  # non-primary calendar for "work"
gcal-busy-sync --interval 10             # keep running, sync every 10 minutes
```

Scheduled with cron (every 10 minutes):

```
*/10 * * * * /path/to/gcal-busy-sync >> ~/.gcal-busy-sync.log 2>&1
```

From the agent, pass the same choice as `calendars={"work": "team@group.calendar.google.com"}`.

If you later switch an account to a different calendar, the blocks already on the old calendar stay there; delete them by hand (they are the private "Busy" events).

Use the absolute path from `which gcal-busy-sync`; cron does not load your shell PATH. On macOS, a launchd agent with `StartInterval` 600 does the same while you are logged in.

## Read-only mode

Set `GOOGLE_MCP_READONLY=1` in a server's environment to hide every mutating
tool (writes, deletes, clears) from that server. Destructive tools are also
clearly marked in their descriptions.

Every tool advertises MCP-native `ToolAnnotations` (`readOnlyHint`,
`destructiveHint`) in `list_tools`, derived from the same flags that drive the
read-only gate. Annotations are hints for well-behaved clients (auto-allow reads,
confirm destructive writes); `GOOGLE_MCP_READONLY` remains the enforcement
mechanism — clients must not rely on annotations alone for security.
Tools that send mail, share files, or overwrite content are marked destructive,
and those that reach people outside the account also set `openWorldHint`.

## Local file access

Tools that read or write local files (mail attachments, Drive upload/download/export, Gmail attachment download) go through one shared path guard: paths are resolved (including symlinks) and anything inside the `~/.google` token store is refused. Hidden paths (any `.`-prefixed component, such as `~/.ssh`, `~/.aws` or `.env`) and system directories (`/etc`, `/proc`, `/sys`, `/dev`, `/boot`) are refused too, so an instruction hidden in an email cannot attach a key file or write into a dotfile. Set `GOOGLE_MCP_FILE_ROOT=/some/dir` to confine all local reads and writes to that directory instead (downloads then need an explicit path inside it), or `GOOGLE_MCP_ALLOW_HIDDEN_PATHS=1` to lift the hidden-path rule. Downloads never replace an existing file unless the call passes `overwrite=true`; a clash in the default temp dir gets a new name instead.

## Safety policy

An agent reads untrusted content and may follow instructions hidden in it. These env vars limit what a mutating tool can do; set them in each server's `env` block.

| Variable | Effect |
|---|---|
| `GOOGLE_MCP_REQUIRE_ACCOUNT` | `auto` (default): when more than one account is configured, mutating tools refuse a call that omits `account`, so nothing is sent from the default account by accident. `1` always requires it; `0` never does. |
| `GOOGLE_MCP_READONLY_ACCOUNTS` | Comma-separated emails or aliases. Mutating tools refuse these accounts; other accounts stay writable. |
| `GOOGLE_MCP_ALLOWED_RECIPIENTS` | Comma-separated addresses or domains (`bob@x.com`, `x.com`, `@x.com`; a domain also matches its subdomains). Mutating tools refuse any `to`/`cc`/`bcc`, attendee, share target or filter `forward` address outside the list, and refuse "anyone" links. |
| `GOOGLE_MCP_ALLOW_IMPLICIT_RECIPIENTS` | With an allowlist set, `reply_to_message` and `send_draft` are refused because their recipients are not named in the call. Set `1` to allow them. |
| `GOOGLE_MCP_AUDIT_LOG` | Path of a JSON-lines file (created with mode `0600`). Every mutating call is appended with time, tool, account, arguments (strings cut at 200 characters) and outcome, including calls the policy refused. |

A refused call returns an error starting `Blocked by policy:` that names the setting involved.

### Client scope

A client that serves several projects from one server process can limit each tool call to some accounts and some Drive folders. The client, not the model, puts the scope in the request's `_meta` under the key `io.github.outcastofmusic.workspace/scope`; the model writes only the tool arguments, so no argument can reach outside the scope. A `tools/call` request with a scope:

```json
{
  "name": "read_file_text",
  "arguments": {"file_id": "1AbC..."},
  "_meta": {
    "io.github.outcastofmusic.workspace/scope": {
      "accounts": ["work"],
      "drive_folders": [{"account": "me@work.com", "id": "0BwDdbFolderId"}]
    }
  }
}
```

Both parts are optional, and each limits only when present:

- `accounts` (emails or aliases): every tool refuses an account outside the list, reads as well as writes. Tools that span accounts (`*_all_accounts`, `sync_busy_blocks`) use only the listed accounts, and `list_accounts` lists only them.
- `drive_folders` (`{account, id}` pairs): Drive, Docs, Sheets and Slides tools refuse any `file_id`, `file_ids`, `folder_id`, `parent_id`, `new_parent_id`, `document_id`, `spreadsheet_id` or `presentation_id` that is not one of the account's scope folders or below one through its parent folders (also inside the `items` of the batch tools). A file only shared with the user has no parent in the account's view, so it is outside. `list_drives`, `list_changes` (which lists changes across the whole Drive, file names included) and a `drive_id` argument are refused. `search_files`, `list_files` and `search_files_all_accounts` return only files inside the scope, so a page may hold fewer than `page_size` files while `next_page_token` still continues. Tools that create a file must name a parent inside the scope: `parent_id` for `upload_file`, `create_folder`, `copy_file` and `batch_create_folders` items, `folder_id` for `create_document`, `create_document_from_markdown`, `create_spreadsheet`, `create_presentation` and `create_presentation_from_outline`. Gmail and Calendar tools are limited by `accounts` only.

A refused call returns an error starting `Outside scope:` that names the account, file or missing parameter. A scope of the wrong shape fails the call with an error naming the problem. A call without a scope behaves as before; set `GOOGLE_MCP_REQUIRE_SCOPE=1` to refuse such calls when every client is known to send one. With `GOOGLE_MCP_AUDIT_LOG` set, each entry records the call's `scope` (`null` without one).

Cost: the folder check reads a file's parents with one Drive `files.get` (fields `id,parents`) per ancestor not yet cached. Parents are cached per account for 10 minutes, at most 5,000 entries, and dropped for any file this server moves or trashes; a move made elsewhere is seen after at most 10 minutes. A scoped search over a large Drive can return sparse pages, so search with `parent_id` of a scope folder, or use `list_folder_tree`.

Over stdio the scope is as trustworthy as the process that started the server. Over HTTP it is only as trustworthy as whoever can connect.

## Smaller tool set

The full servers expose about 200 tools, and every tool schema costs agent
context. Set `GOOGLE_MCP_TOOLSET=core` in a server's environment to register
only the everyday tools (4-16 per server: read/search/get, basic
create and update, Gmail send/reply/drafts, Calendar event CRUD, Sheets range
reads/writes and `write_table`, Docs markdown create/read/append, Drive
search/read/upload/move, Slides `read_presentation`,
`create_presentation_from_outline`, `add_slides` and `update_slide_text`). `list_accounts`, `whoami` and `auth_status` are always
included. The default is `all`. It combines with `GOOGLE_MCP_READONLY`: with
both set you get the read-only part of the core set.

## Testing

```bash
pip install -e .[dev]
pytest                     # unit, protocol (in-memory), isolation, persistence
ruff check .               # minimal lint (import order, syntax, pyflakes)
GOOGLE_MCP_LIVE=1 pytest   # opt-in live smoke tests (needs a real test account)
```

## Notes

- Auth is out-of-band: servers are non-interactive token consumers. If a token
  is missing or scope-short, a tool returns an actionable error
  (`Run: google-auth login <account>`) instead of blocking the protocol.
- The six servers are packaged as one distribution with one console-script
  entry point each (a single `pip install`), rather than six separate packages.
  Alignment comes from the shared `core`, not from separate packaging.
