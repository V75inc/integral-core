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
  a04ScopeProbe: null,
  a04RevocationProbe: null,
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

  await check('foreign workspace scope is denied on published API', async () => {
    const requesterToken = await page.evaluate(() => localStorage.getItem('t75_token'));
    if (!requesterToken) throw new Error('Signed-in browser session has no access token.');

    const ownerEmail = `c6-scope-owner-${randomUUID()}@example.com`;
    const ownerPassword = `C6-scope-${randomUUID()}aZ7!`;
    const ownerWorkspaceName = `C6 Scope Owner Workspace ${Date.now()}`;
    const ownerSignup = await page.request.post(`${baseURL}/api/auth/signup`, {
      data: {
        email: ownerEmail,
        password: ownerPassword,
        name: 'C6 Scope Owner',
        workspaceName: ownerWorkspaceName,
      },
    });
    if (!ownerSignup.ok()) {
      throw new Error(`Second test account signup failed with HTTP ${ownerSignup.status()}.`);
    }
    const owner = await ownerSignup.json();
    const foreignWorkspaceId = owner.workspace?.id;
    if (!owner.access_token || !foreignWorkspaceId) {
      throw new Error('Second test account did not return its workspace and token.');
    }

    const foreignScopeHeaders = {
      Authorization: `Bearer ${requesterToken}`,
      'X-Integral-Scope': `ws:${foreignWorkspaceId}`,
    };
    const forbiddenTitle = `C6 Forbidden Foreign Scope Track ${Date.now()}`;
    const denied = await page.request.post(`${baseURL}/api/tracks`, {
      headers: foreignScopeHeaders,
      data: { title: forbiddenTitle },
    });
    if (denied.status() !== 403) {
      throw new Error(`Foreign workspace Track creation returned HTTP ${denied.status()}, expected 403.`);
    }

    const ownerHeaders = {
      Authorization: `Bearer ${owner.access_token}`,
      'X-Integral-Scope': `ws:${foreignWorkspaceId}`,
    };
    const allowedTitle = `C6 Scoped Owner Track ${Date.now()}`;
    const allowed = await page.request.post(`${baseURL}/api/tracks`, {
      headers: ownerHeaders,
      data: { title: allowedTitle },
    });
    const allowedBody = await allowed.json().catch(() => null);
    if (allowed.status() !== 200 || allowedBody?.track?.workspace_id !== foreignWorkspaceId) {
      throw new Error(`Workspace owner Track creation failed or escaped its workspace (HTTP ${allowed.status()}).`);
    }

    const ownerTracks = await page.request.get(`${baseURL}/api/tracks`, { headers: ownerHeaders });
    const ownerTracksBody = await ownerTracks.json().catch(() => null);
    const listedTracks = ownerTracksBody?.tracks || [];
    if (!ownerTracks.ok() || !listedTracks.some(track => track.title === allowedTitle)) {
      throw new Error('Correctly scoped owner Track was not visible in that Workspace.');
    }
    if (listedTracks.some(track => track.title === forbiddenTitle)) {
      throw new Error('Rejected foreign-scope Track creation left a Track behind.');
    }

    const requesterHeaders = { Authorization: `Bearer ${requesterToken}` };
    const requesterWorkspacesResponse = await page.request.get(`${baseURL}/api/workspaces`, {
      headers: requesterHeaders,
    });
    const requesterWorkspacesBody = await requesterWorkspacesResponse.json().catch(() => null);
    const requesterWorkspace = requesterWorkspacesBody?.workspaces?.find(workspace =>
      workspace.kind === 'organization' && workspace.your_role === 'owner',
    );
    if (!requesterWorkspacesResponse.ok() || !requesterWorkspace?.id) {
      throw new Error('Signed-in workspace owner could not be resolved for revocation probe.');
    }

    const addMember = await page.request.post(
      `${baseURL}/api/workspaces/${requesterWorkspace.id}/members`,
      {
        headers: requesterHeaders,
        data: { member_user_id: owner.user?.id, role: 'member', can_create_tracks: true },
      },
    );
    if (addMember.status() !== 200) {
      throw new Error(`Could not add the synthetic member to the test Workspace (HTTP ${addMember.status()}).`);
    }

    const memberHeaders = {
      Authorization: `Bearer ${owner.access_token}`,
      'X-Integral-Scope': `ws:${requesterWorkspace.id}`,
    };
    const privateTitle = `C6 Revoked Private Track ${Date.now()}`;
    const privateTrack = await page.request.post(`${baseURL}/api/tracks`, {
      headers: memberHeaders,
      data: { title: privateTitle, visibility: 'private' },
    });
    const privateTrackBody = await privateTrack.json().catch(() => null);
    if (privateTrack.status() !== 200 || !privateTrackBody?.track?.id) {
      throw new Error(`Synthetic member could not create a private Track before revocation (HTTP ${privateTrack.status()}).`);
    }

    const publicTitle = `C6 Revoked Public Track ${Date.now()}`;
    const publicTrack = await page.request.post(`${baseURL}/api/tracks`, {
      headers: memberHeaders,
      data: { title: publicTitle, visibility: 'public' },
    });
    const publicTrackBody = await publicTrack.json().catch(() => null);
    if (publicTrack.status() !== 200 || !publicTrackBody?.track?.id) {
      throw new Error(`Synthetic member could not create a public Track before revocation (HTTP ${publicTrack.status()}).`);
    }

    const privateId = privateTrackBody.track.id;
    const publicId = publicTrackBody.track.id;
    const memberPrivateBefore = await page.request.get(`${baseURL}/api/tracks/${privateId}`, {
      headers: memberHeaders,
    });
    if (memberPrivateBefore.status() !== 200) {
      throw new Error(`Synthetic member could not read its private Track before revocation (HTTP ${memberPrivateBefore.status()}).`);
    }

    const removed = await page.request.delete(
      `${baseURL}/api/workspaces/${requesterWorkspace.id}/members/${owner.user.id}`,
      { headers: requesterHeaders },
    );
    if (removed.status() !== 200) {
      throw new Error(`Workspace owner could not revoke the synthetic member (HTTP ${removed.status()}).`);
    }

    const revokedPrivateScoped = await page.request.get(`${baseURL}/api/tracks/${privateId}`, {
      headers: memberHeaders,
    });
    const revokedPrivateUnscoped = await page.request.get(`${baseURL}/api/tracks/${privateId}`, {
      headers: { Authorization: `Bearer ${owner.access_token}` },
    });
    if (revokedPrivateScoped.status() !== 403 || revokedPrivateUnscoped.status() !== 403) {
      throw new Error(`Revoked member retained private Track access (scoped ${revokedPrivateScoped.status()}, unscoped ${revokedPrivateUnscoped.status()}).`);
    }

    const missionControl = await page.request.get(`${baseURL}/api/me/mission-control`, {
      headers: { Authorization: `Bearer ${owner.access_token}` },
    });
    const missionControlBody = await missionControl.json().catch(() => null);
    const privateTrackListed = (missionControlBody?.tracks || []).some(track => track.id === privateId);
    if (!missionControl.ok() || privateTrackListed) {
      throw new Error('Mission Control still exposed a private Track after workspace revocation.');
    }

    const deniedEntry = await page.request.post(`${baseURL}/api/entries`, {
      headers: memberHeaders,
      data: { track_id: privateId, title: `C6 Denied Revoked Entry ${Date.now()}` },
    });
    if (deniedEntry.status() !== 403) {
      throw new Error(`Revoked member Entry creation returned HTTP ${deniedEntry.status()}, expected 403.`);
    }

    const publicRead = await page.request.get(`${baseURL}/api/tracks/${publicId}`, {
      headers: { Authorization: `Bearer ${owner.access_token}` },
    });
    const publicUpdate = await page.request.put(`${baseURL}/api/tracks/${publicId}`, {
      headers: { Authorization: `Bearer ${owner.access_token}` },
      data: { purpose: 'Must not be editable after Workspace revocation' },
    });
    if (publicRead.status() !== 200 || publicUpdate.status() !== 403) {
      throw new Error(`Public Track should remain read-only after revocation (read ${publicRead.status()}, update ${publicUpdate.status()}).`);
    }

    evidence.a04ScopeProbe = {
      foreignWorkspaceCreateStatus: denied.status(),
      ownerCreateStatus: allowed.status(),
      ownerTrackWorkspaceMatches: allowedBody.track.workspace_id === foreignWorkspaceId,
      forbiddenTrackVisibleAfterDenial: false,
    };
    evidence.a04RevocationProbe = {
      memberPrivateReadBeforeRemoval: memberPrivateBefore.status(),
      scopedPrivateReadAfterRemoval: revokedPrivateScoped.status(),
      unscopedPrivateReadAfterRemoval: revokedPrivateUnscoped.status(),
      missionControlPrivateTrackVisible: privateTrackListed,
      entryCreateAfterRemoval: deniedEntry.status(),
      publicTrackReadAfterRemoval: publicRead.status(),
      publicTrackUpdateAfterRemoval: publicUpdate.status(),
    };
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
