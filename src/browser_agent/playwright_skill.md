# Browser control with Playwright CLI

Use the `playwright` tool to run one official Playwright CLI command. Supply the
command and a list of literal arguments. The host supplies the executable,
session, visible-browser configuration and JSON output option. Do not include
`playwright-cli`, `npx`, shell syntax, or shell quoting in the arguments.

For example, `playwright({"command":"fill","args":["e5","Armenia"]})`
runs `playwright-cli fill e5 Armenia` in the current browser session. Text with
spaces remains one argument. Each result is the CLI's actual output, not a claim
that the whole task succeeded.

## Choose the browser

- `list` lists available Playwright sessions.
- `open` opens a visible browser, optionally at a URL. The default profile
  persists login state across sessions.
- `attach` uses the CLI's own attachment support. Supply a discovered session
  name, `--cdp=chrome`, a known `--cdp=<endpoint>`, or `--extension=chrome` when
  the user has the Playwright extension. Read `help attach` for exact options.
- `detach` leaves an attached external browser running.
- `close` closes the current browser session.

Choose according to the task. An ordinary browser may need the extension or
remote debugging enabled before attachment works. Ask for manual help only when
that blocks the requested task; do not assume access to another account.

## Find, act, inspect

Use `find` with visible text to get matching accessibility nodes and element
references. Use `snapshot` for the full page structure, `snapshot e12` for one observed
element, or `snapshot --depth=4` for a shallower view. These commands are supplied
by Playwright; references such as `e12` identify the actual observed elements.

Use references from current CLI results. Do not manufacture CSS selectors from
long labels. If a reference is missing or stale, inspect the current page with
`find` or `snapshot` and choose an appropriate next action. Do not repeat an
action whose outcome is uncertain without checking the page first.

A scoped snapshot replaces the active reference set. A reference outside that
scope requires a new full snapshot or `find`; never combine refs from unrelated
scopes. Snapshot and list responses are previews of controls and page structure,
not proof of a task outcome. Read focused details or the returned artifact when
the full content or read/status state matters, then verify the requested outcome
directly after the relevant action.

The CLI may return a snapshot file path after an action. Use
`read_browser_artifact` to read a bounded excerpt when its contents are useful;
continue from `next_offset` only when more content is needed. Use
`search_browser_artifact` with a literal case-insensitive query to find relevant
lines in a large snapshot instead of paging through the whole document. Search
returns bounded excerpts and never refreshes the browser.

Use `screenshot` when a page is unfamiliar and needs orientation, when a click's
visual change or ambiguous text needs understanding, when image-only content
matters, or when progress is unclear. The host attaches the resulting image to
the tool response. A screenshot does not supply element references: use `find`
or `snapshot` to identify a control. Screenshots are agent-chosen; there is no
automatic initial, before/after, or fixed screenshot routine.

## Commands

Arguments below are individual strings in `args`:

| Command | Arguments | Purpose |
| --- | --- | --- |
| `goto` | URL | Navigate the current tab. |
| `find` | text, or `--regex` and pattern | Locate matching nodes and references. |
| `snapshot` | optional reference, optional `--depth=N` | Read accessibility structure. |
| `click` / `dblclick` | reference, optional button | Click the observed target. |
| `fill` | reference, text | Replace an input's contents. |
| `type` | text | Type into the focused editable control. |
| `press` | key, such as `Enter` or `Escape` | Send a key to the focused control. |
| `check` / `uncheck` | reference | Change a checkbox. |
| `select` | reference, value | Select a dropdown option. |
| `hover` | reference | Reveal hover controls. |
| `drag` | source reference, destination reference | Drag between observed targets. |
| `mousewheel` | horizontal delta, vertical delta | Scroll. |
| `mousemove` | x, y | Move the pointer. |
| `mousedown` / `mouseup` | optional button | Press or release a mouse button. |
| `dialog-accept` / `dialog-dismiss` | optional prompt text for acceptance | Respond to a browser dialog. |
| `tab-list` | none | List tabs. |
| `tab-new` | optional URL | Open a tab. |
| `tab-select` / `tab-close` | tab index | Select or close a tab. |
| `go-back` / `go-forward` / `reload` | none | Browser history and reload. |
| `resize` | width, height | Resize the viewport. |
| `screenshot` | optional reference | Capture the page or an element as an image. |
| `console` | optional severity | Inspect browser console messages. |
| `help` | optional command | Read upstream command help. |

`fill --submit` also presses Enter, so it can submit a form. The security reviewer
checks the immediate effect of the entire command. Submit the intended command
normally; the host handles any required user approval before executing it.

This application exposes browser interaction commands only. General shell,
arbitrary JavaScript, cookie export, network mocking and process-wide session
deletion are outside this tool's interface. Use `ask_user` for required manual
login or other blocking user input, and `finish` to report the observed outcome.

## Source

Adapted for this application's structured command runner from the official
[Playwright CLI skill](https://github.com/microsoft/playwright-cli/blob/655530f6d0dc71a0d6bf46ae165877d3c7311099/skills/playwright-cli/SKILL.md)
and [CLI documentation](https://github.com/microsoft/playwright-cli/blob/655530f6d0dc71a0d6bf46ae165877d3c7311099/README.md).
The installed runtime is pinned to `@playwright/cli@0.1.19`.
