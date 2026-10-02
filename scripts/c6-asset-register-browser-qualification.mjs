import { createRequire } from 'node:module';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE_PATH || 'playwright');
const baseURL = (process.env.WEB_URL || 'http://localhost:9006').replace(/\/$/, '');
const evidenceDir = process.env.EVIDENCE_DIR || '/evidence';
const unique = randomUUID();
const title = `C6 A12 UI asset ${unique}`;
const assetTag = `C6-A12-${unique.slice(0, 12)}`;
const checks = [];
const browserErrors = [];
const evidence = {
  sourceRevision: process.env.GITHUB_SHA || null,
  apiImage: process.env.API_IMAGE || null,
  webImage: process.env.WEB_IMAGE || null,
  assetArchiveSha256: process.env.ASSET_ARCHIVE_SHA256 || null,
  result: 'running',
  checks,
  browserErrors,
};

await mkdir(evidenceDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on('console', message => {
  if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
});
page.on('pageerror', error => browserErrors.push(`page: ${error.message}`));

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function operationResult(responseBody) {
  const pending = [responseBody];
  const seen = new Set();
  while (pending.length) {
    const value = pending.shift();
    if (!value || typeof value !== 'object' || seen.has(value)) continue;
    seen.add(value);
    const receipt = value.operation_receipt || value.receipt || value._receipt;
    if (receipt && typeof receipt === 'object' && (receipt.run_id || receipt.id)) {
      return {
        ...value,
        operation_receipt: value.operation_receipt || receipt,
        replayed: value.operation_receipt?.replayed ?? value.replayed ?? receipt.replayed ?? responseBody?.replayed ?? false,
        output: value.output || value.data || value.result || responseBody,
      };
    }
    for (const child of Object.values(value)) {
      if (child && typeof child === 'object') pending.push(child);
      else if (typeof child === 'string') {
        try {
          const parsed = JSON.parse(child);
          if (parsed && typeof parsed === 'object') pending.push(parsed);
        } catch {
          // Human-readable tool content is not a structured result.
        }
      }
    }
  }
  for (const item of responseBody?.content || []) {
    if (typeof item.text !== 'string') continue;
    try {
      const parsed = JSON.parse(item.text);
      const found = operationResult(parsed);
      if (found?.operation_receipt) return found;
    } catch {
      // MCP may include a short human-readable text block beside JSON data.
    }
  }
  return null;
}

function receiptIdentity(result) {
  const receipt = result?.operation_receipt;
  return receipt?.id || (receipt?.run_id && receipt?.step_key
    ? `${receipt.run_id}:${receipt.step_key}`
    : null);
}

function assetsFrom(responseBody) {
  if (Array.isArray(responseBody)) return responseBody;
  const pending = [responseBody];
  const seen = new Set();
  while (pending.length) {
    const value = pending.shift();
    if (!value || typeof value !== 'object' || seen.has(value)) continue;
    seen.add(value);
    if (Array.isArray(value.assets)) return value.assets;
    if (Array.isArray(value.rows)) return value.rows;
    for (const child of Object.values(value)) {
      if (child && typeof child === 'object') pending.push(child);
      else if (typeof child === 'string') {
        try {
          const parsed = JSON.parse(child);
          if (parsed && typeof parsed === 'object') pending.push(parsed);
        } catch {
          // Human-readable tool content is not a structured query result.
        }
      }
    }
  }
  return [];
}

try {
  await page.goto(`${baseURL}/signup`, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Display name').fill('C6 Asset Register Qualification');
  await page.getByLabel('Email', { exact: true }).fill(`c6-a12-${unique}@example.com`);
  await page.getByLabel('Password', { exact: true }).fill(`C6-A12-${unique}aZ7!`);
  await page.getByRole('button', { name: 'Create account', exact: true }).click();
  await page.getByRole('button', { name: 'Not now', exact: true })
    .waitFor({ timeout: 1_500 }).catch(() => {});
  const notNow = page.getByRole('button', { name: 'Not now', exact: true });
  if (await notNow.isVisible().catch(() => false)) await notNow.click();
  await page.goto(`${baseURL}/apps`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Manage apps', exact: true })
    .first().waitFor({ timeout: 20_000 });
  await page.getByRole('button', { name: 'Manage apps', exact: true }).first().click();
  const packageRow = page.getByTestId('app-manager-row-asset-register');
  await packageRow.waitFor({ timeout: 20_000 });
  await packageRow.getByRole('checkbox').click();
  const seedDataToggle = page.getByTestId('include-seed-data-toggle-checkbox');
  if (await seedDataToggle.isChecked()) await seedDataToggle.uncheck();
  const installResponsePromise = page.waitForResponse(response =>
    response.request().method() === 'POST' &&
    new URL(response.url()).pathname === '/api/apps/batch-install',
  );
  await page.getByTestId('app-manager-apply').click();
  const installResponse = await installResponsePromise;
  assert(installResponse.ok(), `Asset Register install returned HTTP ${installResponse.status()}.`);
  const installBody = await installResponse.json();
  await page.getByTestId('app-manager-result').waitFor({ timeout: 60_000 });
  const installSummary = await page.getByTestId('app-manager-result').innerText();
  assert(/1\s+installed,\s+0\s+skipped,\s+0\s+failed/.test(installSummary),
    `Asset Register install did not succeed: ${installSummary}`);
  const installedAppId = installBody?.installed?.[0]?.app_id;
  assert(installedAppId, `Install response did not identify the new App: ${JSON.stringify(installBody)}`);
  checks.push('signed Asset Register installed using the visible Manage Apps flow');
  await page.getByRole('dialog')
    .getByRole('button', { name: 'Close', exact: true })
    .last()
    .click();

  const token = await page.evaluate(() => localStorage.getItem('t75_token'));
  assert(token, 'The browser session has no access token.');
  const listedAppsResponse = await page.request.get(`${baseURL}/api/apps`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const listedAppsBody = await listedAppsResponse.json();
  const listedApp = (listedAppsBody?.apps || []).find(app => app.id === installedAppId);
  assert(listedAppsResponse.ok() && listedApp,
    `Installed App ${installedAppId} was not returned by /api/apps: ${JSON.stringify(listedAppsBody)}`);
  const appId = installedAppId;
  await page.goto(`${baseURL}/apps/${appId}`, { waitUntil: 'domcontentloaded' });
  evidence.appDetailUrl = page.url();
  await page.getByRole('heading', { name: /Asset Register/ }).waitFor({ timeout: 30_000 });
  const assetsTrackLink = page.getByRole('link', { name: /^Assets(?:\s|$)/ }).first();
  await assetsTrackLink.waitFor({ timeout: 30_000 });
  await assetsTrackLink.click();
  await page.getByRole('tab', { name: 'Asset detail', exact: true }).waitFor({ timeout: 30_000 });
  await page.getByRole('tab', { name: 'Asset detail', exact: true }).click();

  const frame = page.frameLocator('iframe[title="App extension view asset_detail"]');
  await frame.getByRole('heading', { name: 'Register asset', exact: true }).waitFor({ timeout: 30_000 });
  await frame.getByLabel('Asset title', { exact: true }).fill(title);
  await frame.getByLabel('Asset tag', { exact: true }).fill(assetTag);
  const uiOperationResponse = page.waitForResponse(response =>
    response.request().method() === 'POST' &&
    new URL(response.url()).pathname === `/api/extensions/${appId}/operations/register_asset`,
  );
  await frame.getByRole('button', { name: 'Register asset', exact: true }).click();
  const uiResponse = await uiOperationResponse;
  assert(uiResponse.ok(), `Extension view operation returned HTTP ${uiResponse.status()}.`);
  const ui = await uiResponse.json();
  const idempotencyKey = uiResponse.request().headers()['idempotency-key'];
  assert(idempotencyKey, 'Visible extension operation did not send an idempotency key.');
  await frame.getByText('Asset registered through the governed operation.', { exact: true })
    .waitFor({ timeout: 20_000 });
  checks.push('registered an Asset through the visible extension view');

  const appResponse = await page.request.get(`${baseURL}/api/apps/${appId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const appBody = await appResponse.json();
  const workspaceId = appBody?.app?.workspace_id;
  assert(appResponse.ok() && workspaceId, 'Could not resolve the installed App workspace.');
  const authHeaders = {
    Authorization: `Bearer ${token}`,
    'X-Integral-Scope': `ws:${workspaceId}`,
    'Idempotency-Key': idempotencyKey,
  };
  const operationInput = { asset_tag: assetTag, title };

  const httpResponse = await page.request.post(
    `${baseURL}/api/extensions/${appId}/operations/register_asset`,
    { headers: authHeaders, data: { input: operationInput } },
  );
  assert(httpResponse.ok(), `HTTP operation replay returned HTTP ${httpResponse.status()}.`);
  const http = await httpResponse.json();

  const residentResponse = await page.request.post(
    `${baseURL}/api/agentive/tools/integral_invoke_app_operation`,
    {
      headers: authHeaders,
      data: {
        parameters: {
          app_id: appId,
          operation_key: 'register_asset',
          input: operationInput,
          idempotency_key: idempotencyKey,
        },
        scope: { kind: 'workspace', workspace_id: workspaceId },
      },
    },
  );
  assert(residentResponse.ok(), `Resident operation replay returned HTTP ${residentResponse.status()}.`);
  const residentBody = await residentResponse.json();
  const resident = operationResult(residentBody);

  const mcpResponse = await page.request.post(`${baseURL}/api/mcp/`, {
    headers: {
      ...authHeaders,
      'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream',
    },
    data: {
      jsonrpc: '2.0',
      id: `c6-a12-operation-${unique}`,
      method: 'tools/call',
      params: {
        name: 'integral_invoke_app_operation',
        arguments: {
          app_id: appId,
          operation_key: 'register_asset',
          input: operationInput,
          idempotency_key: idempotencyKey,
        },
      },
    },
  });
  assert(mcpResponse.ok(), `MCP operation replay returned HTTP ${mcpResponse.status()}.`);
  const mcpBody = await mcpResponse.json();
  const mcp = operationResult(mcpBody);
  evidence.transportOperationResponses = { resident: residentBody, mcp: mcpBody };
  assert(resident?.operation_receipt && mcp?.operation_receipt,
    'Resident or MCP replay did not return a durable operation receipt.');

  const operationResults = { ui, http, resident, mcp };
  const receiptIds = Object.fromEntries(Object.entries(operationResults).map(([surface, result]) =>
    [surface, receiptIdentity(result)],
  ));
  const assetIds = Object.fromEntries(Object.entries(operationResults).map(([surface, result]) =>
    [surface, result?.output?.asset?.entry_id || result?.asset?.entry_id || null],
  ));
  assert(Object.values(receiptIds).every(Boolean) && new Set(Object.values(receiptIds)).size === 1,
    `Operation receipts differ across transports: ${JSON.stringify(receiptIds)}`);
  assert(Object.values(assetIds).every(Boolean) && new Set(Object.values(assetIds)).size === 1,
    `Operation effects differ across transports: ${JSON.stringify(assetIds)}`);
  assert(ui.operation_receipt?.replayed === false,
    'The first visible UI operation was not recorded as the original effect.');
  for (const [surface, result] of Object.entries(operationResults)) {
    if (surface === 'ui') continue;
    assert(result.operation_receipt?.replayed === true,
      `${surface} did not replay the original operation receipt.`);
  }
  checks.push('HTTP, resident, and MCP retries reused the UI receipt and single Asset effect');

  const viewQueryResponsePromise = page.waitForResponse(response =>
    response.request().method() === 'POST' &&
    new URL(response.url()).pathname === `/api/extensions/${appId}/queries/available_assets`,
  );
  await frame.getByRole('button', { name: 'Refresh availability', exact: true }).click();
  const viewQueryResponse = await viewQueryResponsePromise;
  assert(viewQueryResponse.ok(), `Asset Register UI query returned HTTP ${viewQueryResponse.status()}.`);
  const viewQuery = await viewQueryResponse.json();
  await frame.getByText(/Query available_assets → 1 row\(s\)\./).waitFor({ timeout: 20_000 });

  const queryParams = { limit: 5, offset: 0 };
  const httpQueryResponse = await page.request.post(
    `${baseURL}/api/extensions/${appId}/queries/available_assets`,
    { headers: authHeaders, data: { params: queryParams } },
  );
  assert(httpQueryResponse.ok(), `HTTP query returned HTTP ${httpQueryResponse.status()}.`);
  const httpQuery = await httpQueryResponse.json();

  const residentQueryResponse = await page.request.post(
    `${baseURL}/api/agentive/tools/integral_governed_query`,
    {
      headers: authHeaders,
      data: {
        parameters: {
          mode: 'declared_capability',
          capability_key: 'available_assets',
          app_id: appId,
          params: queryParams,
        },
        scope: { kind: 'workspace', workspace_id: workspaceId },
      },
    },
  );
  assert(residentQueryResponse.ok(), `Resident query returned HTTP ${residentQueryResponse.status()}.`);
  const residentQueryBody = await residentQueryResponse.json();

  const mcpQueryResponse = await page.request.post(`${baseURL}/api/mcp/`, {
    headers: {
      ...authHeaders,
      'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream',
    },
    data: {
      jsonrpc: '2.0',
      id: `c6-a12-query-${unique}`,
      method: 'tools/call',
      params: {
        name: 'integral_governed_query',
        arguments: {
          mode: 'declared_capability',
          capability_key: 'available_assets',
          app_id: appId,
          params: queryParams,
        },
      },
    },
  });
  assert(mcpQueryResponse.ok(), `MCP query returned HTTP ${mcpQueryResponse.status()}.`);
  const mcpQueryBody = await mcpQueryResponse.json();
  evidence.transportQueryResponses = {
    resident: residentQueryBody,
    mcp: mcpQueryBody,
  };
  const querySurfaces = {
    ui: assetsFrom(viewQuery),
    http: assetsFrom(httpQuery),
    resident: assetsFrom(residentQueryBody),
    mcp: assetsFrom(mcpQueryBody),
  };
  const foundIds = Object.fromEntries(Object.entries(querySurfaces).map(([surface, rows]) =>
    [surface, rows.filter(row => row.entry_id === assetIds.ui).map(row => row.entry_id)],
  ));
  assert(Object.values(foundIds).every(ids => ids.length === 1),
    `The four queries did not return the same single Asset: ${JSON.stringify(foundIds)}`);
  const totals = Object.fromEntries(Object.entries(querySurfaces).map(([surface, rows]) => [surface, rows.length]));
  assert(Object.values(totals).every(total => total === 1),
    `The four queries returned inconsistent row counts: ${JSON.stringify(totals)}`);
  checks.push('UI, HTTP, resident, and MCP queries returned the same one-row Asset Register result');

  evidence.result = 'passed';
  evidence.appId = appId;
  evidence.workspaceId = workspaceId;
  evidence.idempotencyKey = idempotencyKey;
  evidence.receiptIds = receiptIds;
  evidence.assetIds = assetIds;
  evidence.operationReplayed = Object.fromEntries(Object.entries(operationResults).map(([surface, result]) =>
    [surface, result.operation_receipt?.replayed ?? result.replayed ?? null],
  ));
  evidence.queryTotals = totals;
  evidence.queryFoundIds = foundIds;
  await page.screenshot({ path: path.join(evidenceDir, 'asset-register-a12.png'), fullPage: true });
} catch (error) {
  evidence.result = 'failed';
  evidence.failure = error instanceof Error ? error.message : String(error);
  evidence.failureUrl = page.url();
  evidence.failurePageText = await page.locator('body').innerText().catch(() => '');
  await page.screenshot({ path: path.join(evidenceDir, 'asset-register-a12-failure.png'), fullPage: true }).catch(() => {});
  throw error;
} finally {
  evidence.browserErrors = browserErrors;
  await writeFile(path.join(evidenceDir, 'asset-register-a12.json'), `${JSON.stringify(evidence, null, 2)}\n`);
  await browser.close();
}

console.log(JSON.stringify(evidence, null, 2));
