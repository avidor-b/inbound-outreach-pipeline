// Single-contact submission lookup — smoke test for the simple path.
// `submissions_multi.mjs` covers the bulk path. This test guards the
// plain `get_submissions(contact_id=<one name>)` flow plus the same
// SSE stream-integrity invariant: if the tool path hangs or crashes,
// the frontend synthesizes a `missing_terminal_event` result and
// this test fails loud.
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
  console.log(`▶ Single-contact submission test vs ${BASE}`);
  const browser = await chromium.launch({ ...(EXE ? { executablePath: EXE } : {}), headless: true });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await ctx.newPage();

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

  // Discover one company by mining thread history for ingest-tool
  // output lines ("Foo Corp (contact_id: 123, engagement: #4)").
  let company = process.env.COMPANY || "";
  if (!company) {
    try {
      const threadsRes = await page.request.get(`${BASE}/threads`);
      const rows = await threadsRes.json();
      outer: for (const r of (rows || []).slice(0, 15)) {
        const h = await page.request.get(`${BASE}/threads/${r.id}/history`);
        const msgs = await h.json();
        for (const m of (Array.isArray(msgs) ? msgs : [])) {
          const txt = typeof m.content === "string" ? m.content : "";
          for (const line of txt.split(/\r?\n/)) {
            const match = line.match(
              /^\s*(?:[-*•]\s+)?([A-Za-z][A-Za-z0-9 &'\-.,]{1,80}?)\s*\(contact_id:/,
            );
            if (match) { company = match[1].trim(); break outer; }
          }
        }
      }
    } catch { /* fall through */ }
  }
  if (!company) {
    company = "Acme Widgets Inc";
    console.log(`  (no ingested contacts found — using synthetic name)`);
  }
  pass(`using company: ${JSON.stringify(company)}`);

  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForSelector("textarea", { timeout: 30000 });
  await page.waitForTimeout(3000);
  const newBtn = await page.$("button:has-text('New')");
  if (newBtn) { await newBtn.click(); await page.waitForTimeout(800); }

  const composer = await page.waitForSelector("textarea, [contenteditable='true']");
  await composer.click();
  await page.keyboard.type(`Show submission history for ${company}`);
  await page.keyboard.press("Enter");

  const start = Date.now();
  while (Date.now() - start < 45000) {
    await page.waitForTimeout(1000);
    const done = await page.evaluate(() => {
      const ta = document.querySelector("textarea, [contenteditable='true']");
      return !!ta && !document.querySelector("[data-run-status='running']");
    });
    if (done) break;
  }
  await page.waitForTimeout(2000);

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

  const body = await page.evaluate(() => document.body.innerText);
  const snippet = body.slice(-400);
  await browser.close();

  assert.equal(
    terminated.length, 0,
    `SSE stream produced ${terminated.length} synthetic "missing_terminal_event" ` +
    `tool result(s). Chat tail: ...${snippet}`,
  );
  assert.equal(
    runErrors.length, 0,
    `SSE stream reported ${runErrors.length} RUN_ERROR event(s).`,
  );
  pass("SSE stream terminated cleanly");

  const rendered = body.includes(`for ${company}`) ||
                   /\bSUBMISSIONS\b/.test(body);
  const emptyMsg = /No submissions found for contact/.test(body) ||
                   body.includes("No submissions found for the requested contacts");
  assert.ok(
    rendered || emptyMsg,
    `no card or empty-state rendered. Chat tail: ${snippet}`,
  );
  pass(rendered ? "submission card rendered" : "empty-state rendered (no seed data)");
  console.log("\n✓ Single-contact submission test passed");
}

run().catch((e) => { console.error("✗ FAILED:", e.message); process.exit(1); });
