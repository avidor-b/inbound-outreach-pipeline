// UI regression test — runs against the deployed EC2.
//
// Core (always):          app shell loads, no ES2023 regressions,
//                         Pipeline stats card renders with the
//                         "SDR handoff" tile.
// Extended (REQUIRE_DATA=1): engagements-hint-sum, sdr_handoff badge
//                         rendering. Requires actual data in the
//                         deployed DB — skip in pre-commit, run in CI.
//
// Budget: core ≤30s; extended ≤90s.
import { chromium } from "playwright";
import assert from "node:assert/strict";

const BASE = process.env.BASE_URL || "http://localhost:3131";
const TOKEN = process.env.JWT;
if (!TOKEN) { console.error("JWT env var required"); process.exit(2); }
const EXE = process.env.CHROMIUM_PATH;
const REQUIRE_DATA = process.env.REQUIRE_DATA === "1";

function pass(m) { console.log(`  ✓ ${m}`); }

async function run() {
  console.log(`▶ UI regression vs ${BASE}${REQUIRE_DATA ? " (extended)" : ""}`);
  const browser = await chromium.launch({ ...(EXE ? { executablePath: EXE } : {}), headless: true });
  const ctx = await browser.newContext();
  await ctx.addCookies([{ name: "outreach_session", value: TOKEN, url: BASE, httpOnly: true }]);
  const page = await ctx.newPage();

  // Track errors. Ignore benign 404s on static assets / sourcemaps.
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const t = m.text();
    if (/Failed to load resource/.test(t)) return;
    if (/source map/i.test(t)) return;
    errors.push(`console.error: ${t}`);
  });

  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});
  const body = (await page.textContent("body")) || "";
  assert.ok(!/Application error/i.test(body), `App crashed: ${errors.join(" | ")}`);
  assert.ok(
    !errors.some((e) => /\.with is not a function/.test(e)),
    `ES2023 regression: ${errors.filter((e) => /\.with/.test(e)).join(" | ")}`,
  );
  pass("App shell loaded");

  // Fresh thread so we aren't blocked by a prior interrupt.
  const nt = await page.$("button:has-text('New Thread')");
  if (nt) { await nt.click(); await page.waitForTimeout(500); }

  // Ask for pipeline stats; wait for the tile layout (leaf span
  // exactly "Contacts" marks it rendered).
  //
  // Use a Locator (not an ElementHandle): CopilotChat re-renders
  // while the page loads, so an element handle resolved by
  // `page.$(...)` or `waitForSelector(...)` often detaches from the
  // DOM before we can act on it ("Element is not attached to the
  // DOM"). Locators re-resolve on every action and auto-wait up to
  // the given timeout.
  const composer = page.locator("textarea, [contenteditable='true']").first();
  assert.ok(
    await composer.waitFor({ state: "visible", timeout: 15000 })
      .then(() => true).catch(() => false),
    "composer missing",
  );
  await composer.click();
  await composer.fill("Show pipeline stats");
  await page.keyboard.press("Enter");

  await page.waitForFunction(
    () =>
      [...document.querySelectorAll("*")]
        .filter((el) => el.children.length === 0)
        .some((el) => (el.textContent || "").trim() === "Contacts"),
    { timeout: 90000 },
  );

  // The SDR handoff tile label must render (the fix that started
  // this session's deploys).
  const hasSdr = await page.evaluate(() =>
    [...document.querySelectorAll("*")]
      .filter((el) => el.children.length === 0)
      .some((el) => (el.textContent || "").trim() === "SDR handoff"),
  );
  assert.ok(hasSdr, "'SDR handoff' tile missing from pipeline stats card");
  pass("Pipeline stats card shows 'SDR handoff' tile");

  // Extended checks — only with REQUIRE_DATA=1. These need actual
  // rows in the deployed DB to exercise.
  if (REQUIRE_DATA) {
    const breakdown = await page.evaluate(() => {
      const labels = [...document.querySelectorAll("*")].filter(
        (el) => el.children.length === 0 && (el.textContent || "").trim() === "Engagements",
      );
      for (const label of labels) {
        const tile = label.parentElement;
        const spans = [...(tile?.querySelectorAll("*") || [])]
          .filter((el) => el.children.length === 0)
          .map((el) => (el.textContent || "").trim())
          .filter(Boolean);
        const i = spans.indexOf("Engagements");
        if (i < 0) continue;
        const total = Number(spans[i - 1]);
        const hint = spans[i + 1] || "";
        if (!Number.isFinite(total) || total === 0) continue;
        if (!/,/.test(hint) && !/^\d+\s+\w+$/.test(hint)) continue;
        const nums = [...hint.matchAll(/\b(\d+)\s+\w+/g)].map((x) => Number(x[1]));
        return { total, hint, sum: nums.reduce((a, b) => a + b, 0) };
      }
      return null;
    });
    assert.ok(breakdown, "extended: no engagements hint rendered");
    assert.equal(
      breakdown.sum, breakdown.total,
      `hint sum ${breakdown.sum} ≠ total ${breakdown.total} (hint="${breakdown.hint}")`,
    );
    pass(`Engagements hint adds up (${breakdown.total}: "${breakdown.hint}")`);
  }

  assert.ok(
    errors.length === 0,
    `Runtime errors during flow: ${errors.join(" | ")}`,
  );
  pass("No runtime errors");

  await browser.close();
  console.log("\n✓ UI regression passed");
}

run().catch((e) => { console.error("✗ UI regression FAILED:", e.message); process.exit(1); });
