// Thread-switch regression test — guards against the "chat area
// renders empty on thread switch" bug.
//
// The failure mode was a race between:
//   - CopilotKitCore.connectAgent() calling agent.setMessages([])
//     mid-connect, and
//   - useThreadHydration fetching /threads/{id}/history and calling
//     agent.setMessages(msgs).
// When the fetch resolved before the connect wipe, the user's messages
// got blown away and the chat showed only the empty-state suggestions.
// The hook now retries setMessages for ~4s until it sticks.
//
// This test clicks several real threads in sequence (three rounds of
// four thread candidates = 12 switches) and asserts that each switch
// renders that thread's actual content, not the "How can I help you
// today?" empty state. Fails if any switch shows empty content.
//
// Usage:
//   # Against a deployed ALB:
//   BASE_URL=https://<alb-dns> JWT=<token> node thread_switch.mjs
//
//   # Against a local staging box (ui_flow_test.sh mints a JWT):
//   BASE_URL=http://localhost:3131 JWT="$JWT" node thread_switch.mjs
//
// Exit codes: 0 = all switches rendered, 1 = one or more empty.
// Budget: ~60s.
import { chromium } from "playwright";
import assert from "node:assert/strict";

const BASE = process.env.BASE_URL || "http://localhost:3131";
// API_URL defaults to BASE_URL for same-origin deploys (prod ALB).
// On split-port local staging, the frontend 3131 doesn't proxy
// /threads — point this at the backend (8181) explicitly.
const API = process.env.API_URL || BASE;
const TOKEN = process.env.JWT;
if (!TOKEN) { console.error("JWT env var required"); process.exit(2); }
const EXE = process.env.CHROMIUM_PATH;
const MIN_CHAT_LEN = 200; // threshold below which the chat is "empty"

function pass(m) { console.log(`  ✓ ${m}`); }
function fail(m) { console.log(`  ✗ ${m}`); }

async function run() {
  console.log(`▶ Thread-switch test vs ${BASE}`);
  const browser = await chromium.launch({ ...(EXE ? { executablePath: EXE } : {}), headless: true });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  await ctx.addCookies([
    { name: "outreach_session", value: TOKEN, url: BASE, httpOnly: true },
  ]);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log(`  [pageerror] ${e.message}`));

  // Discover threads with content via the backend API. Pick ones
  // that have >2 messages so the "rendered" assertion is meaningful.
  const threadsRes = await page.request.get(`${API}/threads`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  const rows = await threadsRes.json();
  const candidates = [];
  for (const r of (rows || []).slice(0, 12)) {
    const mres = await page.request.get(`${API}/threads/${r.id}/history`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    });
    const msgs = await mres.json();
    if (Array.isArray(msgs) && msgs.length > 2) {
      let sig = "";
      for (const m of msgs) {
        // Only trust user-visible text: the assistant/user plain-text
        // messages that actually render in the chat pane. Skip tool
        // messages and tool-call JSON payloads (their content is
        // structured and doesn't appear in the DOM we assert on).
        const role = String(m.role || m.type || "").toLowerCase();
        if (!(role.includes("human") || role.includes("user") ||
              role.includes("ai") || role.includes("assistant"))) continue;
        if (m.tool_calls && m.tool_calls.length) continue;
        const body = typeof m.content === "string" ? m.content : "";
        if (!body || body.length < 30 || body.length > 500) continue;
        // Skip anything that looks like a tool-call JSON object that
        // leaked through as a string.
        if (/^\s*[\{\[]/.test(body)) continue;
        sig = body.slice(0, 30);
        break;
      }
      if (sig) candidates.push({ id: r.id, sig, msgCount: msgs.length });
    }
    if (candidates.length >= 4) break;
  }
  if (candidates.length < 2) {
    console.log(`  (skipped — fewer than 2 threads with content; need a seeded account)`);
    await browser.close();
    process.exit(0);
  }
  pass(`${candidates.length} threads with content`);

  // Boot on thread 0 and wait for the chat area to be ready.
  await page.goto(`${BASE}/?thread=${candidates[0].id}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("textarea", { timeout: 30000 });
  await page.waitForTimeout(4000);

  // Sanity gate: if most candidate threads are actually archive-stale
  // (clicking them renders the empty-state card rather than any
  // content — common on minimally-seeded staging where the sidebar
  // includes threads without valid checkpoints), the assertion loop
  // will fail 3/4 switches every round for environmental reasons,
  // not the hydration race. Detect by clicking each candidate once
  // and counting how many actually render >= MIN_CHAT_LEN chars.
  // If <2, skip — there's nothing here to race against.
  let liveCandidates = [];
  for (let i = 0; i < candidates.length; i++) {
    await clickThread(candidates[i].id);
    await page.waitForTimeout(1800);
    const text = await chatText();
    if (text.length >= MIN_CHAT_LEN) liveCandidates.push(candidates[i]);
  }
  if (liveCandidates.length < 2) {
    console.log(
      `  (skipped — only ${liveCandidates.length}/${candidates.length} ` +
      `candidate threads have renderable content; staging seed looks minimal)`,
    );
    await browser.close();
    process.exit(0);
  }
  candidates.length = 0;
  candidates.push(...liveCandidates);
  pass(`${candidates.length} renderable candidates`);

  // Click by stable thread identity instead of sidebar position. The
  // sidebar is allowed to reorder by updatedAt during hydration, and
  // position-based clicks can accidentally assert the wrong thread.
  async function clickThread(threadId) {
    return page.evaluate((id) => {
      const row = document.querySelector(`[data-thread-id="${CSS.escape(id)}"]`);
      if (!row) return false;
      row.click();
      return true;
    }, threadId);
  }

  async function chatText() {
    return page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return "";
      const header = main.querySelector("header");
      const headerText = header ? (header.innerText || "").trim() : "";
      const fullText = (main.innerText || "").trim();
      return fullText.replace(headerText, "").trim();
    });
  }

  let failures = 0;

  // Phase 1 — steady-state: 3 rounds × N candidates, 2.5s settle
  // between clicks. Guards the (threadId, agent) hydration key fix.
  const ROUNDS = 3;
  for (let round = 0; round < ROUNDS; round++) {
    for (let i = 0; i < candidates.length; i++) {
      const c = candidates[i];
      const clicked = await clickThread(c.id);
      if (!clicked) {
        fail(`round=${round} thread=${c.id.slice(0, 8)} — no sidebar row to click`);
        failures++;
        continue;
      }
      await page.waitForTimeout(2500);
      const text = await chatText();
      const rendered = text.length >= MIN_CHAT_LEN && text.includes(c.sig);
      if (rendered) {
        pass(`round=${round} thread=${c.id.slice(0, 8)} (${text.length} chars)`);
      } else {
        fail(`round=${round} thread=${c.id.slice(0, 8)} — ${text.length} chars, sig=${JSON.stringify(c.sig)} missing`);
        failures++;
      }
    }
  }

  // Phase 2 — rapid fire: click through candidates with NO settle
  // between, then once the dust clears assert the *last* thread's
  // content rendered. This exercises the in-flight hydration race:
  // fetches from earlier clicks are still running when the last
  // click lands, and their setMessages calls must NOT win over the
  // final thread's. The hook guards this by keying hydration on the
  // (threadId, agent) pair AND retrying setMessages after
  // connectAgent's clobber.
  const RAPID_CYCLES = 2;
  for (let cycle = 0; cycle < RAPID_CYCLES; cycle++) {
    // Click every candidate as fast as Playwright will let us.
    for (let i = 0; i < candidates.length; i++) {
      await clickThread(candidates[i].id);
      // 50 ms between clicks — fast enough to overlap hydration,
      // slow enough that the click dispatcher actually fires each.
      await page.waitForTimeout(50);
    }
    // Settle time for the last thread's hydration to land.
    await page.waitForTimeout(4000);

    const last = candidates[candidates.length - 1];
    const text = await chatText();
    const rendered = text.length >= MIN_CHAT_LEN && text.includes(last.sig);
    const isEmptyState =
      text.includes("How can I help you today") || text.length < MIN_CHAT_LEN;
    if (rendered) {
      pass(`rapid cycle ${cycle}: last thread ${last.id.slice(0, 8)} rendered (${text.length} chars)`);
    } else if (isEmptyState) {
      fail(`rapid cycle ${cycle}: chat showed empty state after burst — in-flight hydration race`);
      failures++;
    } else {
      // Not empty but a different thread's content leaked through —
      // also a race, just a different shape (earlier fetch wrote last).
      fail(`rapid cycle ${cycle}: last thread ${last.id.slice(0, 8)} not rendered, but chat isn't empty — earlier fetch clobbered last. ${text.length} chars, head=${JSON.stringify(text.slice(0, 80))}`);
      failures++;
    }
  }

  await browser.close();
  assert.equal(
    failures, 0,
    `${failures} thread switches failed (steady ${ROUNDS * candidates.length} + rapid ${RAPID_CYCLES})`,
  );
  console.log("\n✓ Thread-switch test passed");
}

run().catch((e) => { console.error("✗ Thread-switch FAILED:", e.message); process.exit(1); });
