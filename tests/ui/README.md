# UI regression suites

Playwright-driven end-to-end tests that drive the real app through a
browser and observe the `/copilotkit` SSE stream. Pytest unit tests
never touch the streaming-adapter layer where our worst bugs have
lived; these do.

## Suites

| File | Guards |
|---|---|
| `thread_switch.mjs` | `useThreadHydration` race when CopilotChat's `connectAgent` wipes messages mid-hydrate. 12 sidebar-row clicks must each render that thread's prior content, not the empty-state suggestions. |
| `submissions_multi.mjs` | `get_submissions_bulk` + the streaming-adapter class of bug. HARD assertion is data-independent: the SSE stream must never emit a synthetic `"reason":"missing_terminal_event"` tool result — that's only fabricated when the backend crashes mid-tool. |
| `submissions_single.mjs` | Same stream-integrity check for the plain `get_submissions(<one company>)` path. |
| `archive_persist.mjs` | Archive state persists across a hard refresh (`PATCH /threads/{id}` with `status:"archived"`). |
| `flow_test.mjs` | Full pipeline walk (ingest → enrich → score → draft). Long (~4 min) — opt-in via `scripts/ui_flow_test.sh`. |
| `ui_regression.mjs` | App-shell smoke: pipeline stats card, SDR-handoff tile, no ES2023 regressions. |

## Env vars

Every suite takes a base URL and an auth token. Most accept either a
pre-minted JWT or an email/password the suite will trade for a cookie.

| Var | Default | Used by |
|---|---|---|
| `BASE_URL` | `http://localhost:3131` | all |
| `JWT` | (required for some) | `thread_switch`, `flow_test`, `ui_regression` |
| `EMAIL` + `PASSWORD` | (required for others) | `submissions_multi`, `submissions_single`, `archive_persist` |
| `CHROMIUM_PATH` | Playwright-managed browser | all |
| `REQUIRE_DATA` | unset | `ui_regression` — turns on the extended engagements assertion |

## Running one-off against the deployed ALB

```bash
BASE_URL=https://<alb-dns> \
EMAIL=you@example.com PASSWORD='…' \
node tests/ui/submissions_multi.mjs
```

## Running the whole local staging suite

`scripts/ui_thread_switch.sh` mints a JWT from the staging secret and
runs the thread-switch suite. Use that as a template; the rest run the
same way (env vars differ slightly).
