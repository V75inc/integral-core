#!/usr/bin/env node
/** Package local, prebundled UI assets into one CSP-compatible HTML document. */
import { readFile, writeFile, mkdir, realpath } from 'node:fs/promises';
import { dirname, resolve, relative, extname } from 'node:path';

const [input, output] = process.argv.slice(2);
if (!input || !output) throw new Error('Usage: integral-sdk-build source/index.html output/index.html');
const root = await realpath(dirname(resolve(input)));
async function local(path, base = root) {
  if (/^(?:[a-z]+:|\/\/|\/)/i.test(path)) throw new Error('UI builds require local bundled assets.');
  const file = await realpath(resolve(base, path));
  if (relative(root, file).startsWith('..')) throw new Error('UI assets must stay inside the source directory.');
  return file;
}
const escapeScript = text => text.replace(/<\/script/gi, '<\\/script');
const sdk = await readFile(new URL('../dist/integral-sdk.js', import.meta.url), 'utf8');
let html = await readFile(resolve(input), 'utf8');
html = html.replace(/<script\s+data-integral-sdk\s*>\s*<\/script>/gi, () => `<script>${escapeScript(sdk)}</script>`);
for (const match of [...html.matchAll(/<script\s+src=["']([^"']+)["']\s*>\s*<\/script>/gi)]) {
  const text = await readFile(await local(match[1]), 'utf8');
  if (/\bimport\s*(?:\(|[{*"'])/.test(text)) throw new Error('Bundle JavaScript imports before packaging the UI.');
  html = html.replace(match[0], () => `<script>${escapeScript(text)}</script>`);
}
for (const match of [...html.matchAll(/<link\s+rel=["']stylesheet["']\s+href=["']([^"']+)["']\s*\/?>/gi)]) {
  let css = await readFile(await local(match[1]), 'utf8');
  const cssRoot = dirname(await local(match[1]));
  if (/@import/i.test(css)) throw new Error('Bundle CSS imports before packaging the UI.');
  for (const asset of [...css.matchAll(/url\(\s*["']?([^"')\s]+)["']?\s*\)/gi)]) {
    if (asset[1].startsWith('data:')) continue;
    const uri = await dataUri(await local(asset[1], cssRoot));
    css = css.replace(asset[0], () => `url("${uri}")`);
  }
  html = html.replace(match[0], () => `<style>${css.replace(/<\/style/gi, '<\\/style')}</style>`);
}
for (const match of [...html.matchAll(/(<img\b[^>]*\bsrc=)["']([^"']+)["']/gi)]) {
  if (match[2].startsWith('data:')) continue;
  const uri = await dataUri(await local(match[2]));
  html = html.replace(match[0], () => `${match[1]}"${uri}"`);
}
if (/<script\b[^>]*\bsrc\s*=|<link\b[^>]*\bhref\s*=|\bsrcset\s*=/i.test(html)) throw new Error('Unsupported external UI assets; emit a self-contained build.');
await mkdir(dirname(resolve(output)), { recursive: true });
await writeFile(resolve(output), html);
console.log(`Built ${resolve(output)}`);
async function dataUri(file) {
  const mime = { '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif', '.woff': 'font/woff', '.woff2': 'font/woff2' }[extname(file)];
  if (!mime) throw new Error('Unsupported bundled UI asset type.');
  return `data:${mime};base64,${(await readFile(file)).toString('base64')}`;
}
