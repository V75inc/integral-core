import { createRequire } from 'node:module';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE_PATH || 'playwright');
const baseURL = (process.env.WEB_URL || 'http://web').replace(/\/$/, '');
const evidenceDir = process.env.EVIDENCE_DIR || '/evidence';
const appName = `C6 Browser App ${Date.now()}`;
const trackName = `C6 Browser Track ${Date.now()}`;
const entryTitle = `C6 Browser Entry ${Date.now()}`;
const browserErrors = [];
const evidence = {
  sourceRevision: process.env.GITHUB_SHA || null,
  apiImage: process.env.API_IMAGE || null,
  webImage: process.env.WEB_IMAGE || null,
  result: 'running',
  checks: [],
  browserErrors,
  appRequests: [],
};

await mkdir(evidenceDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on('console', message => {
  if (message.type() !== 'error') return;
  const text = message.text();
  if (
    process.env.ALLOW_UNTRUSTED_ORIGIN_WARNINGS === '1' &&
    text.includes('Cross-Origin-Opener-Policy header has been ignored')
  ) return;
  browserErrors.push(`console: ${text}`);
});
page.on('pageerror', error => browserErrors.push(`page: ${error.message}`));

async function recordScreenshot(filename) {
  await page.screenshot({ path: path.join(evidenceDir, filename), fullPage: true });
}

async function check(name, action) {
  await action();
  evidence.checks.push(name);
}

try {
  await check('signup', async () => {
    await page.goto(`${baseURL}/signup`, { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Display name').fill('C6 Browser Qualification');
    await page.getByLabel('Email', { exact: true }).fill(`c6-${randomUUID()}@example.com`);
    await page.getByLabel('Password', { exact: true }).fill(`C6-browser-${randomUUID()}aZ7!`);
    await page.getByLabel('Collaborative workspace (optional)').fill('C6 Browser Workspace');
    await page.getByRole('button', { name: 'Create account', exact: true }).click();
    await page.getByRole('button', { name: 'Not now', exact: true }).waitFor({ timeout: 1_500 }).catch(() => {});
    const notNow = page.getByRole('button', { name: 'Not now', exact: true });
    if (await notNow.isVisible().catch(() => false)) await notNow.click();
    await page.goto(`${baseURL}/apps`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { name: 'No Apps', exact: true }).waitFor({ timeout: 20_000 });
    await page.getByRole('button', { name: 'Manage apps', exact: true }).first().waitFor({ timeout: 20_000 });
  });

  await check('create blank App', async () => {
    await page.getByRole('button', { name: 'Manage apps', exact: true }).first().click();
    await page.getByRole('button', { name: /Create blank app/ }).click();
    await page.locator('#app-name').fill(appName);
    const createResponse = page.waitForResponse(response =>
      response.request().method() === 'POST' && /\/api\/apps\/?$/.test(new URL(response.url()).pathname),
    );
    await page.getByRole('button', { name: 'Create', exact: true }).last().click();
    const created = await createResponse;
    const createdBody = await created.json().catch(() => null);
    evidence.appRequests.push({
      phase: 'create',
      status: created.status(),
      appId: createdBody?.app?.id || null,
      workspaceId: createdBody?.app?.workspace_id || null,
    });
    await page.getByText('App created', { exact: true }).waitFor({ timeout: 20_000 });
    await page.getByRole('link', { name: 'View app', exact: true }).click();
    await page.getByRole('button', { name: 'New track', exact: true }).first().waitFor({ timeout: 20_000 });
  });

  await check('create Track and Post', async () => {
    await page.getByRole('button', { name: 'New track', exact: true }).first().click();
    await page.getByRole('button', { name: /None \/ Default/ }).click();
    await page.getByRole('button', { name: 'Next →', exact: true }).click();
    await page.locator('#track-name').fill(trackName);
    await page.getByRole('button', { name: 'Create Track', exact: true }).click();
    const trackLink = page.getByRole('link', { name: new RegExp(trackName) });
    await trackLink.waitFor({ timeout: 20_000 });
    await trackLink.click();
    await page.getByRole('button', { name: 'New Post', exact: true }).click();
    await page.getByLabel('Title', { exact: true }).fill(entryTitle);
    await page.getByLabel('Body', { exact: true }).fill('Published digest browser qualification entry.');
    await page.getByRole('button', { name: 'Post', exact: true }).click();
    await page.getByRole('heading', { name: entryTitle, exact: true }).waitFor({ timeout: 20_000 });
  });

  await check('global Tracks lists only the Track', async () => {
    await page.goto(`${baseURL}/tracks`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { name: 'Tracks', exact: true }).waitFor({ timeout: 20_000 });
    const matchingTracks = page.getByRole('link', { name: new RegExp(trackName) });
    await matchingTracks.waitFor({ timeout: 20_000 });
    const trackCount = await matchingTracks.count();
    evidence.globalTrackCount = trackCount;
    if (trackCount !== 1) throw new Error('Expected exactly one global Track result.');
    evidence.appShownAsTrack = await page.getByText(appName, { exact: true }).count() !== 0;
    if (evidence.appShownAsTrack) {
      throw new Error('The global Tracks page displayed the App as a Track.');
    }
    await recordScreenshot('global-tracks.png');
    await matchingTracks.click();
    await page.getByRole('heading', { name: entryTitle, exact: true }).waitFor({ timeout: 20_000 });
    await recordScreenshot('track-entry.png');
  });

  if (browserErrors.length) throw new Error(`Browser reported ${browserErrors.length} error(s).`);
  evidence.result = 'passed';
} catch (error) {
  evidence.result = 'failed';
  evidence.failure = error instanceof Error ? error.message : String(error);
  await recordScreenshot('browser-failure.png').catch(() => {});
  throw error;
} finally {
  await writeFile(path.join(evidenceDir, 'browser-smoke.json'), `${JSON.stringify(evidence, null, 2)}\n`);
  await browser.close();
}

console.log(JSON.stringify(evidence, null, 2));
