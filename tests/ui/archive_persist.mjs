// Archive-persistence regression test.
//
// Earlier bug: archiving a thread only updated localStorage; after a
// hard refresh the backend /threads response (which won server vs.
// client in the merge) resurrected the archived thread. The fix
// PATCHes /threads/{id} with {status:"archived"}. This test drives
// the real UI and verifies the thread stays archived across reload.
//
// Usage:
//   BASE_URL=https://<alb> EMAIL=... PASSWORD=... node archive_persist.mjs
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
  console.log(`▶ Archive-persistence test vs ${BASE}`);
  const browser = await chromium.launch({ ...(EXE ? { executablePath: EXE } : {}), headless: true });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log(`  [pageerror] ${e.message}`));

  const loginRes = await page.request.post(`${BASE}/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
    headers: { "Content-Type": "application/json" },
  });
  assert.ok(loginRes.ok(), `login failed (${loginRes.status()})`);
  pass("logged in");

  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForSelector("textarea", { timeout: 30000 });
  await page.waitForTimeout(3000);

  // Create a throwaway thread and give it recognizable content.
  const newBtn = await page.$("button:has-text('New')");
  if (newBtn) { await newBtn.click(); await page.waitForTimeout(1000); }
  const tag = `archive-regression-${Date.now()}`;
  const composer = await page.waitForSelector("textarea, [contenteditable='true']");
  await composer.click();
  await page.keyboard.type(tag);
  await page.keyboard.press("Enter");
  // Wait long enough for the thread to be persisted on the backend.
  await page.waitForTimeout(4500);
  pass(`created thread tagged "${tag}"`);

  // The thread just used is the sidebar's most-recent row. Archive
  // via the row's "×" / archive control — the sidebar renders one
  // archive button per row; we click the one on the row whose title
  // or preview contains our tag.
  const archived = await page.evaluate((needle) => {
    const rows = Array.from(document.querySelectorAll("li,[role='button']"));
    for (const row of rows) {
      if (!row.textContent?.includes(needle)) continue;
      const btn = row.querySelector(
        "button[aria-label*='rchive' i], button[title*='rchive' i], button:has(svg)",
      );
      if (btn) { btn.click(); return true; }
    }
    return false;
  }, tag.slice(0, 20));
  assert.ok(archived, `couldn't find archive button for thread tagged ${tag}`);
  await page.waitForTimeout(1500);
  pass("clicked archive");

  // Hard reload — bypasses the service-worker / cached HTML so the
  // thread list is re-hydrated from the backend.
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector("textarea", { timeout: 30000 });
  await page.waitForTimeout(3000);

  const sidebarText = await page.evaluate(() => {
    const aside = document.querySelector("aside, nav");
    return aside ? (aside.innerText || "") : document.body.innerText;
  });
  assert.ok(
    !sidebarText.includes(tag),
    `after hard refresh, archived thread "${tag}" reappeared in the sidebar — archive didn't persist`,
  );
  pass("archived thread stayed archived across reload");

  await browser.close();
  console.log("\n✓ Archive persistence test passed");
}

run().catch((e) => { console.error("✗ FAILED:", e.message); process.exit(1); });
