import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
const builder = new URL('../bin/build-ui.mjs', import.meta.url);
test('self-contained build preserves literal dollar expressions and nested CSS assets', async () => {
 const root = await mkdtemp(join(tmpdir(), 'integral-ui-'));
 try {
  await mkdir(join(root, 'styles'));
  await writeFile(join(root, 'styles', 'icon.svg'), '<svg xmlns="http://www.w3.org/2000/svg"/>');
  await writeFile(join(root, 'styles', 'app.css'), 'body{background:url(icon.svg)}');
  await writeFile(join(root, 'app.js'), 'const replacement = "$&";');
  await writeFile(join(root, 'index.html'), '<link rel="stylesheet" href="styles/app.css"><script data-integral-sdk></script><script src="app.js"></script>');
  execFileSync(process.execPath, [builder.pathname, join(root, 'index.html'), join(root, 'out.html')]);
  const html = await readFile(join(root, 'out.html'), 'utf8');
  assert.ok(html.includes('const replacement = "$&";'));
  assert.ok(html.includes('data:image/svg+xml;base64,'));
  assert.ok(html.includes('globalThis.IntegralSDK'));
  assert.ok(!html.includes('<script src='));
  await writeFile(join(root, 'index.html'), '<script src="https://evil.test/script.js"></script>');
  assert.throws(() => execFileSync(process.execPath, [builder.pathname, join(root, 'index.html'), join(root, 'bad.html')], { stdio: 'pipe' }), /local bundled assets/);
 } finally { await rm(root, { recursive: true, force: true }); }
});
