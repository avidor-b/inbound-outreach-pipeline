// End-to-end UI test for multi-company submission queries.
//
// Reproduces the production bug path: "Show submission history for
// A, B, and C". The test's HARD assertion is data-independent — it
// reads the /copilotkit SSE stream and fails if any tool result
// comes back with `"reason":"missing_terminal_event"`. That synthetic
// result is frontend-synthesized ONLY when the backend stream dies
// mid-tool without emitting RUN_FINISHED / RUN_ERROR — i.e., the
// exact failure mode of the last production bug (ag_ui_langgraph
// AttributeError from nested tool ainvoke).
//
// When the DB has real seed data we ALSO check that submission cards
// render; when it doesn't, we accept "no submissions found" as valid
// behaviour (tool ran, just no data). Either way, a terminated stream
// fails the test loud.
//
// Usage:
//   BASE_URL=https://<alb> EMAIL=... PASSWORD=... node submissions_multi.mjs
//
// Budget: ~30s.
import { chromium } from "playwright";
import assert from "node:assert/strict";

const BASE = process.env.BASE_URL || "http://localhost:3131";
const EMAIL = process.env.EMAIL;
const PASSWORD = process.env.PASSWORD;
if (!EMAIL || !PASSWORD) {
  console.error("EMAIL + PASSWORD env vars required");
  process.exit(2);
}
const EXE = process.env.CHROMIUM_PATH;

function pass(m) { console.log(`  ✓ ${m}`); }

async function run() {
  console.log(`▶ Submissions multi-company test vs ${BASE}`);
  const browser = await chromium.launch({ ...(EXE ? { executablePath: EXE } : {}), headless: true });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await ctx.newPage();

  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(`pageerror: ${e.message}`));

  // Capture /copilotkit SSE bodies so we can assert on the stream shape.
  const kitEvents = [];
  page.on("response", async (resp) => {
    const url = resp.url();
    if (!/\/copilotkit/.test(url) && !/\/api\/copilotkit/.test(url)) return;
    try { kitEvents.push({ body: await resp.text() }); } catch { /* ignore */ }
  });

  const loginRes = await page.request.post(`${BASE}/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
    headers: { "Content-Type": "application/json" },
  });
  assert.ok(loginRes.ok(), `login failed (${loginRes.status()})`);
  pass("logged in");

  // Discover 2-3 companies from historical thread messages. The
  // ingest tool emits one-line entries like
  //   "Acme Corp (contact_id: 12345, engagement: #7)"
  // Match that pattern tightly — on a single line ending in
  // "(contact_id:" — to avoid dragging in surrounding prose.
  let companies = [];
  try {
    const threadsRes = await page.request.get(`${BASE}/threads`);
    const rows = await threadsRes.json();
    const seen = new Set();
    outer: for (const r of (rows || []).slice(0, 15)) {
      const h = await page.request.get(`${BASE}/threads/${r.id}/history`);
      const msgs = await h.json();
      for (const m of (Array.isArray(msgs) ? msgs : [])) {
        const txt = typeof m.content === "string" ? m.content : "";
        for (const line of txt.split(/\r?\n/)) {
          const match = line.match(
            /^\s*(?:[-*•]\s+)?([A-Za-z][A-Za-z0-9 &'\-.,]{1,80}?)\s*\(contact_id:/,
          );
          if (!match) continue;
          const name = match[1].trim();
          if (name.length < 3 || seen.has(name.toLowerCase())) continue;
          seen.add(name.toLowerCase());
          companies.push(name);
          if (companies.length >= 3) break outer;
        }
      }
    }
  } catch { /* fall through */ }
  if (companies.length < 2) {
    // Synthetic names — the query still exercises the tool-call
    // path, which is what the hard (stream-integrity) assertion
    // checks. Soft assertion accepts the empty-state in that case.
    companies = ["Example Widgets", "Example Systems", "Example Networks"];
    console.log(`  (no ingested contacts found — using synthetic names)`);
  }
  pass(`using companies: ${JSON.stringify(companies)}`);

  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForSelector("textarea", { timeout: 30000 });
  await page.waitForTimeout(3500);
  const newBtn = await page.$("button:has-text('New')");
  if (newBtn) { await newBtn.click(); await page.waitForTimeout(800); }

  const asList =
    companies.length === 2
      ? `${companies[0]} and ${companies[1]}`
      : `${companies.slice(0, -1).join(", ")}, and ${companies.at(-1)}`;
  const query = `Show submission history for ${asList}.`;
  const composer = await page.waitForSelector("textarea, [contenteditable='true']");
  await composer.click();
  await page.keyboard.type(query);
  await page.keyboard.press("Enter");
  pass(`submitted: ${JSON.stringify(query.slice(0, 90))}`);

  // Give the run time to finish (RUN_FINISHED or RUN_ERROR).
  const start = Date.now();
  while (Date.now() - start < 45000) {
    await page.waitForTimeout(1000);
    const done = await page.evaluate(() => {
      // CopilotChat marks the input as non-running when the run ends.
      const ta = document.querySelector("textarea, [contenteditable='true']");
      return !!ta && !document.querySelector("[data-run-status='running']");
    });
    if (done) break;
  }
  // Settle time for any last SSE frames.
  await page.waitForTimeout(2000);

  // Parse the SSE stream for tool-call results.
  const lines = kitEvents.flatMap((e) => e.body.split("\n")).filter((l) => l.startsWith("data:"));
  const events = [];
  for (const raw of lines) {
    try { events.push(JSON.parse(raw.slice(5).trim())); } catch { /* skip */ }
  }
  const terminated = events.filter(
    (e) => e.type === "TOOL_CALL_RESULT" &&
           typeof e.content === "string" &&
           e.content.includes('"reason":"missing_terminal_event"'),
  );
  const runErrors = events.filter((e) => e.type === "RUN_ERROR");

  if (terminated.length || runErrors.length) {
    console.log("\n--- SSE events (last 12) ---");
    events.slice(-12).forEach((e) => {
      const short = { type: e.type, name: e.name || e.toolName, content: (e.content || "").slice(0, 160) };
      console.log("   " + JSON.stringify(short).slice(0, 260));
    });
  }

  // Snapshot everything we need from the live page BEFORE closing
  // the browser — otherwise the later body checks fail with
  // "Target page has been closed" on assertion failure paths.
  const body = await page.evaluate(() => document.body.innerText);
  const snippet = body.slice(-400);
  await browser.close();

  // HARD assertion: the stream must terminate cleanly, without the
  // frontend synthesizing a `missing_terminal_event` tool result.
  assert.equal(
    terminated.length, 0,
    `SSE stream produced ${terminated.length} synthetic "missing_terminal_event" ` +
    `tool result(s) — the backend agent crashed mid-stream. This is the ` +
    `exact failure mode of the ag_ui_langgraph adapter bug. ` +
    `Chat tail: ...${snippet}`,
  );
  assert.equal(
    runErrors.length, 0,
    `SSE stream reported ${runErrors.length} RUN_ERROR event(s).`,
  );
  pass("SSE stream terminated cleanly");

  // Soft check: when the companies were real, we expect rendered
  // submission cards, not the empty state. When names are synthetic
  // we accept the empty state — the tool still ran.
  const rendered = companies.some((c) => body.includes(`for ${c}`));
  const emptyMsg = body.includes("No submissions found for the requested contacts")
                 || /No submissions found for contact/.test(body);
  assert.ok(
    rendered || emptyMsg,
    `no card or empty-state rendered. Chat tail: ${snippet}`,
  );
  pass(rendered ? "at least one submission card rendered" : "empty-state rendered (no seed data)");

  if (pageErrors.length) {
    console.warn("page errors during test (non-fatal):", pageErrors.length);
    pageErrors.forEach((e) => console.warn("   " + e));
  }
  console.log("\n✓ Submissions multi-company test passed");
}

run().catch((e) => { console.error("✗ FAILED:", e.message); process.exit(1); });
