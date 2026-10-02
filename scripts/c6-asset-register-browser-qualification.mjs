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
  if (responseBody?.operation_receipt) return responseBody;
  if (responseBody?.data?.operation_receipt) return responseBody.data;
  if (responseBody?.result?.operation_receipt) return responseBody.result;
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

function assetsFrom(responseBody) {
  if (Array.isArray(responseBody)) return responseBody;
  if (Array.isArray(responseBody?.assets)) return responseBody.assets;
  if (Array.isArray(responseBody?.output?.assets)) return responseBody.output.assets;
  if (Array.isArray(responseBody?.data?.assets)) return responseBody.data.assets;
  if (Array.isArray(responseBody?.result?.assets)) return responseBody.result.assets;
  if (Array.isArray(responseBody?.rows)) return responseBody.rows;
  if (Array.isArray(responseBody?.result?.rows)) return responseBody.result.rows;
  for (const item of responseBody?.content || []) {
    if (typeof item.text !== 'string') continue;
    try {
      const rows = assetsFrom(JSON.parse(item.text));
      if (rows.length) return rows;
    } catch {
      // Ignore non-JSON MCP text content.
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
  await page.getByTestId('app-manager-apply').click();
  await page.getByTestId('app-manager-result').waitFor({ timeout: 60_000 });
  const installSummary = await page.getByTestId('app-manager-result').innerText();
  assert(/1\s+installed,\s+0\s+skipped,\s+0\s+failed/.test(installSummary),
    `Asset Register install did not succeed: ${installSummary}`);
  checks.push('signed Asset Register installed using the visible Manage Apps flow');
  await page.getByRole('button', { name: 'Close', exact: true }).click();

  const appLink = page.getByRole('link', { name: /Asset Register/ }).first();
  await appLink.waitFor({ timeout: 20_000 });
  const appHref = await appLink.getAttribute('href');
  assert(appHref, 'Installed Asset Register did not expose its App link.');
  const appId = appHref.split('/').filter(Boolean).at(-1);
  await appLink.click();
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

  const token = await page.evaluate(() => localStorage.getItem('t75_token'));
  assert(token, 'The browser session has no access token.');
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
  const resident = operationResult(await residentResponse.json());

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
  const mcp = operationResult(await mcpResponse.json());
  assert(resident?.operation_receipt && mcp?.operation_receipt,
    'Resident or MCP replay did not return a durable operation receipt.');

  const operationResults = { ui, http, resident, mcp };
  const receiptIds = Object.fromEntries(Object.entries(operationResults).map(([surface, result]) =>
    [surface, result?.operation_receipt?.id || result?.receipt?.id || null],
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
    `${baseURL}/api/agentive/tools/list_available_assets`,
    {
      headers: authHeaders,
      data: {
        parameters: queryParams,
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
    [surface, result.operation_receipt?.replayed ?? null],
  ));
  evidence.queryTotals = totals;
  evidence.queryFoundIds = foundIds;
  await page.screenshot({ path: path.join(evidenceDir, 'asset-register-a12.png'), fullPage: true });
} catch (error) {
  evidence.result = 'failed';
  evidence.failure = error instanceof Error ? error.message : String(error);
  await page.screenshot({ path: path.join(evidenceDir, 'asset-register-a12-failure.png'), fullPage: true }).catch(() => {});
  throw error;
} finally {
  evidence.browserErrors = browserErrors;
  await writeFile(path.join(evidenceDir, 'asset-register-a12.json'), `${JSON.stringify(evidence, null, 2)}\n`);
  await browser.close();
}

console.log(JSON.stringify(evidence, null, 2));
