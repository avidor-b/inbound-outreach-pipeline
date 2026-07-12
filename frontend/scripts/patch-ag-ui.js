/**
 * Patches @ag-ui/langgraph to fix "Message not found" errors on multi-turn.
 *
 * Root cause: CopilotKit's regenerate detection compares client message count
 * vs checkpoint message count. Tool call/result messages inflate the checkpoint
 * count, triggering a regenerate path that fails to find messages by ID.
 *
 * Patches both CJS (.js) and ESM (.mjs) builds:
 * 1. Disable regenerate detection (always false condition)
 * 2. Make getCheckpointByMessage return null instead of throwing
 */
const fs = require('fs');
const path = require('path');

const dir = path.join(__dirname, '..', 'node_modules', '@ag-ui', 'langgraph', 'dist');

if (!fs.existsSync(dir)) {
  console.log('[patch] @ag-ui/langgraph not found, skipping');
  process.exit(0);
}

const files = ['index.js', 'index.mjs'].map(f => path.join(dir, f)).filter(f => fs.existsSync(f));

const regen = '.length>r.filter(v=>v.role!=="system").length';
const msgThrow = 'if(!g)throw new Error("Message not found")';

for (const file of files) {
  let content = fs.readFileSync(file, 'utf8');
  let patched = false;
  const ext = path.extname(file);

  if (content.includes(regen)) {
    content = content.replace(regen, '.length>99999999');
    patched = true;
  }

  if (content.includes(msgThrow)) {
    content = content.replace(msgThrow, 'if(!g)return null');
    patched = true;
  }

  if (patched) {
    fs.writeFileSync(file, content);
    console.log(`[patch] ${ext} patched`);
  } else if (content.includes('99999999')) {
    console.log(`[patch] ${ext} already patched`);
  }
}
