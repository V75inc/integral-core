'use strict';

const crypto = require('node:crypto');
const fsp = require('node:fs/promises');
const path = require('node:path');
const { buildNativeCandidates, chooseWithTypesafe } = require('./jev-choose');

const OBSERVATION_CAPABILITIES = Object.freeze([
  'driver__list_apps',
  'driver__list_windows',
  'driver__snapshot_window',
  'driver__grant_state',
]);
const ACTION_CAPABILITIES = Object.freeze(['driver__act', 'driver__choose']);
const CURATED_OBSERVATION_TOOLS = Object.freeze([
  'list_apps',
  'list_windows',
  'get_window_state',
]);
const CURATED_ACTION_TOOLS = Object.freeze([
  'click',
  'type_text',
  'press_key',
  'hotkey',
]);
const ALLOWED_ACTIONS = Object.freeze(['click', 'type_text', 'press_key', 'hotkey']);
const MAX_ACTIONS_PER_GRANT = 60;
const MAX_STEPS_PER_BURST = 12;
const MAX_TYPE_TEXT_CHARS = 400;
const MAX_HOTKEY_KEYS = 6;
const ALLOWED_KEYS = new Set([
  'cmd', 'command', 'meta', 'win', 'super',
  'ctrl', 'control',
  'alt', 'option',
  'shift',
  'return', 'enter', 'tab', 'escape', 'esc', 'space',
  'backspace', 'delete', 'forwarddelete',
  'up', 'down', 'left', 'right',
  'home', 'end', 'pageup', 'pagedown',
  ...'abcdefghijklmnopqrstuvwxyz'.split(''),
  ...'0123456789'.split(''),
  ...'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12'.split(','),
]);

function secondsBetween(start, end) {
  return Math.max(1, Math.floor((end.getTime() - start.getTime()) / 1000));
}

function windowKey(pid, windowId) {
  return `${pid}:${windowId}`;
}

function manifestApp(app) {
  if (app.bundleId) {
    return {
      bundle_id: String(app.bundleId),
      windows: 'all',
      launch: false,
      terminate: 'deny',
    };
  }
  if (app.executable) {
    return {
      executable: path.resolve(String(app.executable)),
      windows: 'all',
      launch: false,
      terminate: 'deny',
    };
  }
  throw new Error('Every computer-use grant app needs a bundleId or executable');
}

function jsonSafe(value) {
  if (typeof value === 'bigint') return value.toString();
  if (Array.isArray(value)) return value.map(jsonSafe);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, jsonSafe(item)]),
    );
  }
  return value;
}

function invalidArguments(message) {
  const error = new Error(message);
  error.code = 'computer_use.invalid_arguments';
  return error;
}

function normalizeActSteps(args) {
  if (Array.isArray(args.steps)) {
    if (args.steps.length < 1 || args.steps.length > MAX_STEPS_PER_BURST) {
      throw invalidArguments(
        `steps must contain 1 to ${MAX_STEPS_PER_BURST} background actions`,
      );
    }
    return args.steps.map((step, index) => {
      if (!step || typeof step !== 'object' || Array.isArray(step)) {
        throw invalidArguments(`steps[${index}] must be an action object`);
      }
      return {
        action: String(step.action || ''),
        elementToken: String(step.element_token || args.element_token || ''),
        text: step.text,
        key: step.key,
        keys: step.keys,
      };
    });
  }
  return [
    {
      action: String(args.action || ''),
      elementToken: String(args.element_token || ''),
      text: args.text,
      key: args.key,
      keys: args.keys,
    },
  ];
}

function safeNativeErrorMessage(error) {
  const raw = String(error?.message || error || 'Computer-use snapshot failed')
    .replace(/\/[^\s]+/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  return raw.slice(0, 240) || 'Computer-use snapshot failed';
}

function isAxWindowUnresolved(errorOrReason) {
  const text = [
    errorOrReason?.code,
    errorOrReason?.message,
    errorOrReason,
  ]
    .filter(Boolean)
    .join(' ');
  return /ax_window_unresolved/i.test(text);
}

function hasAccessibilityElements(output) {
  return Array.isArray(output?.elements) && output.elements.length > 0;
}

class ComputerUseBroker {
  constructor({
    binaryPath,
    hostBundleId,
    stateDir,
    loadSdk = () => import('@trycua/cua-driver'),
    now = () => new Date(),
    maxArtifactBytes = 8 * 1024 * 1024,
    onRevoked = () => {},
    jev = null,
  }) {
    this.binaryPath = binaryPath;
    this.hostBundleId = hostBundleId;
    this.stateDir = stateDir;
    this.loadSdk = loadSdk;
    this.now = now;
    this.maxArtifactBytes = maxArtifactBytes;
    this.onRevoked = onRevoked;
    this.jev = jev || {
      getSettings: () => ({ enabled: false, apiKey: '' }),
      choose: chooseWithTypesafe,
    };
    this.driver = null;
    this.sdk = null;
    this.grant = null;
    this.runtimeGeneration = 0;
    this.manifestPath = null;
    this.artifacts = new Map();
    this.snapshots = new Map();
    this.actionOutcomes = new Map();
    this.unknownWindows = new Set();
    this.actionCount = 0;
    this.expirationTimer = null;
  }

  get active() {
    if (!this.driver || !this.grant) return false;
    try {
      return this.driver.isAvailable?.() !== false;
    } catch {
      return false;
    }
  }

  get generation() {
    return this.active ? this.runtimeGeneration : null;
  }

  advertisedCapabilities() {
    if (!this.active) return [];
    const capabilities = [...OBSERVATION_CAPABILITIES];
    if (this.grant.actionsAllowed === true) {
      capabilities.push('driver__act');
      const jev = this.jev.getSettings();
      if (jev.enabled === true && jev.apiKey) {
        capabilities.push('driver__choose');
      }
    }
    return capabilities;
  }

  async prepareState() {
    await fsp.mkdir(this.stateDir, { recursive: true, mode: 0o700 });
    await fsp.rm(path.join(this.stateDir, 'artifacts'), {
      recursive: true,
      force: true,
    });
    const entries = await fsp.readdir(this.stateDir, { withFileTypes: true });
    await Promise.all(
      entries
        .filter((entry) =>
          entry.isFile() &&
          /^(capabilities|discovery)-[A-Za-z0-9.-]+\.json$/.test(entry.name))
        .map((entry) => fsp.rm(path.join(this.stateDir, entry.name), { force: true })),
    );
  }

  curatedTools(grant) {
    const tools = [...CURATED_OBSERVATION_TOOLS];
    if (grant.actionsAllowed === true) tools.push(...CURATED_ACTION_TOOLS);
    return tools;
  }

  async activate(grant) {
    if (grant?.approvedLocally !== true) {
      const error = new Error('Computer use requires approval on this device');
      error.code = 'computer_use.local_consent_required';
      throw error;
    }
    const expiresAt = new Date(grant.expiresAt);
    const now = this.now();
    if (!Number.isFinite(expiresAt.getTime()) || expiresAt <= now) {
      const error = new Error('The local computer-use grant has expired');
      error.code = 'computer_use.grant_expired';
      throw error;
    }
    if (!Array.isArray(grant.apps) || grant.apps.length === 0) {
      const error = new Error('Computer use must be scoped to at least one application');
      error.code = 'computer_use.app_scope_required';
      throw error;
    }

    await this.stop();
    await this.prepareState();
    this.runtimeGeneration += 1;
    const generation = this.runtimeGeneration;
    this.manifestPath = path.join(
      this.stateDir,
      `capabilities-${generation}-${crypto.randomUUID()}.json`,
    );
    const manifest = {
      version: 3,
      expires_after: `${secondsBetween(now, expiresAt)}s`,
      idle_timeout: `${Math.max(1, Number(grant.idleTimeoutSeconds) || 300)}s`,
      allow: { tools: this.curatedTools(grant) },
      resources: {
        apps: grant.apps.map(manifestApp),
        desktop: { display: false },
      },
    };
    await fsp.writeFile(this.manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, {
      encoding: 'utf8',
      mode: 0o600,
    });

    const sdk = await this.loadSdk();
    const bounded = sdk.SessionPermissionMode.Bounded;
    const configuredDriver = {
      claudeCodeCompatibility: false,
      authorization: {
        allowedModes: [bounded],
        compatibilityMode: bounded,
        compatibilityCapabilityManifestPath: this.manifestPath,
        unrestrictedAcknowledged: false,
        maxSessionTtlSeconds: BigInt(secondsBetween(now, expiresAt)),
        maxIdleTtlSeconds: BigInt(Math.max(1, Number(grant.idleTimeoutSeconds) || 300)),
      },
    };
    try {
      this.driver = sdk.CuaDriver.createPrivateWorker({
        binaryPath: this.binaryPath,
        hostBundleId: this.hostBundleId,
        configuredDriver,
        environment: [],
        inheritStderr: false,
      });
      this.sdk = sdk;
      this.grant = { ...grant, actionsAllowed: grant.actionsAllowed === true };
      this.actionCount = 0;
      const expiresInMs = Math.max(1, expiresAt.getTime() - this.now().getTime());
      this.expirationTimer = setTimeout(() => {
        void this.stop()
          .catch(() => {})
          .finally(() => this.onRevoked({ reason: 'expired', generation }));
      }, Math.min(expiresInMs, 2_147_483_647));
      this.expirationTimer.unref?.();
      return { runtimeGeneration: generation };
    } catch (error) {
      await fsp.rm(this.manifestPath, { force: true });
      this.manifestPath = null;
      throw error;
    }
  }

  async discoverApps() {
    if (this.driver) {
      const error = new Error('Revoke the active computer-use grant before changing scope');
      error.code = 'computer_use.already_active';
      throw error;
    }
    await this.prepareState();
    const manifestPath = path.join(
      this.stateDir,
      `discovery-${crypto.randomUUID()}.json`,
    );
    await fsp.writeFile(manifestPath, `${JSON.stringify({
      version: 3,
      expires_after: '5m',
      idle_timeout: '1m',
      allow: { tools: ['list_apps'] },
      // Cua protects process discovery behind desktop observation scope. The
      // runtime still exposes only `list_apps`, lives for at most five minutes,
      // and is destroyed before any remote computer-use grant starts.
      resources: { desktop: { display: true } },
    }, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
    let driver;
    try {
      const sdk = await this.loadSdk();
      const bounded = sdk.SessionPermissionMode.Bounded;
      driver = sdk.CuaDriver.createPrivateWorker({
        binaryPath: this.binaryPath,
        hostBundleId: this.hostBundleId,
        configuredDriver: {
          claudeCodeCompatibility: false,
          authorization: {
            allowedModes: [bounded],
            compatibilityMode: bounded,
            compatibilityCapabilityManifestPath: manifestPath,
            unrestrictedAcknowledged: false,
            maxSessionTtlSeconds: 300n,
            maxIdleTtlSeconds: 60n,
          },
        },
        environment: [],
        inheritStderr: false,
      });
      return jsonSafe(await driver.listApps({}));
    } finally {
      if (driver) {
        try {
          await driver.shutdown();
        } finally {
          driver.uniffiDestroy?.();
        }
      }
      await fsp.rm(manifestPath, { force: true });
    }
  }

  assertCall(call) {
    if (!this.driver || !this.grant) {
      const error = new Error('Computer use is not active');
      error.code = 'computer_use.not_active';
      throw error;
    }
    if (call.bindingGeneration !== this.grant.bindingGeneration) {
      const error = new Error('The desktop binding changed; reconnect and retry the read');
      error.code = 'computer_use.binding_stale';
      throw error;
    }
    const now = this.now();
    if (new Date(this.grant.expiresAt) <= now) {
      const error = new Error('The local computer-use grant has expired');
      error.code = 'computer_use.grant_expired';
      throw error;
    }
    const deadline = new Date(call.deadline);
    if (!Number.isFinite(deadline.getTime()) || deadline <= now) {
      const error = new Error('The computer-use call deadline has elapsed');
      error.code = 'computer_use.deadline_elapsed';
      throw error;
    }
    return {
      driver: this.driver,
      grant: this.grant,
      runtimeGeneration: this.runtimeGeneration,
    };
  }

  async invoke(call) {
    const admitted = this.assertCall(call);
    const args = call.arguments || {};
    let data;
    const artifacts = [];
    if (call.operation === 'driver__list_apps') {
      data = {
        apps: this.grant.apps.map((app) => ({
          ...(Number.isSafeInteger(app.pid) ? { pid: app.pid } : {}),
          name: String(app.name || app.bundleId || app.executable || 'Approved application'),
          running: true,
          ...(app.bundleId ? { bundleId: app.bundleId } : {}),
          ...(app.executable ? { launchPath: app.executable } : {}),
        })),
      };
    } else if (call.operation === 'driver__list_windows') {
      const pid = Number(args.pid);
      if (!Number.isSafeInteger(pid) || pid <= 0) {
        throw invalidArguments('pid must be a positive integer');
      }
      data = await this.driver.listWindows({ pid, onScreenOnly: true });
    } else if (call.operation === 'driver__snapshot_window') {
      data = await this.snapshotWindow(args);
      if (data.artifact) {
        artifacts.push(data.artifact);
        delete data.artifact;
      }
    } else if (call.operation === 'driver__grant_state') {
      data = this.grantState();
    } else if (call.operation === 'driver__choose') {
      data = await this.choose(args);
    } else if (call.operation === 'driver__act') {
      data = await this.act(call.callId, args);
      if (data.artifact) {
        artifacts.push(data.artifact);
        delete data.artifact;
      }
    } else {
      const error = new Error('The computer-use operation is not available');
      error.code = 'computer_use.operation_denied';
      throw error;
    }
    if (
      this.driver !== admitted.driver ||
      this.grant !== admitted.grant ||
      this.runtimeGeneration !== admitted.runtimeGeneration ||
      this.now() >= new Date(admitted.grant.expiresAt) ||
      call.bindingGeneration !== admitted.grant.bindingGeneration
    ) {
      await Promise.all(artifacts.map((artifact) => this.releaseArtifact(artifact.id)));
      const error = new Error(
        call.operation === 'driver__act'
          ? 'Computer-use authority changed while the action was running'
          : 'Computer-use authority changed while the read was running',
      );
      error.code = 'computer_use.revoked_during_call';
      if (call.operation === 'driver__act') {
        const outcome = this.actionOutcomes.get(String(call.callId || ''));
        if (outcome === 'started') this.markUnknown(call.callId, args);
      }
      throw error;
    }
    return {
      runtimeGeneration: admitted.runtimeGeneration,
      data: jsonSafe(data),
      artifacts,
    };
  }

  grantState() {
    return {
      grant_id: this.grant.id,
      expires_at: this.grant.expiresAt,
      actions_allowed: this.grant.actionsAllowed === true,
      screenshots_allowed: this.grant.screenshotsAllowed === true,
      foreground_disabled: true,
      launch_disabled: true,
      terminate_disabled: true,
      runtime_generation: this.runtimeGeneration,
      action_count: this.actionCount,
      action_limit: MAX_ACTIONS_PER_GRANT,
      jev_enabled: this.jevSettings().enabled === true,
      jev_configured: Boolean(this.jevSettings().apiKey),
      apps: this.grant.apps.map((app) => ({
        name: String(app.name || app.bundleId || app.executable),
        ...(app.bundleId ? { bundleId: app.bundleId } : {}),
      })),
    };
  }

  rememberSnapshot(pid, windowId, output) {
    const tokens = new Set();
    for (const element of output.elements || []) {
      if (element?.elementToken) tokens.add(String(element.elementToken));
    }
    const snapshotId = String(output.snapshotId || '');
    this.snapshots.set(windowKey(pid, windowId), {
      snapshotId,
      tokens,
      elements: Array.isArray(output.elements) ? output.elements : [],
      captureId: String(output.captureId || output.snapshotId || snapshotId),
      runtimeGeneration: this.runtimeGeneration,
    });
    this.unknownWindows.delete(windowKey(pid, windowId));
    return snapshotId;
  }

  requireFreshElement(args) {
    const pid = Number(args.pid);
    const windowId = String(args.window_id || '');
    const snapshotId = String(args.snapshot_id || '');
    const elementToken = String(args.element_token || '');
    if (
      !Number.isSafeInteger(pid) ||
      pid <= 0 ||
      !/^\d+$/.test(windowId) ||
      !snapshotId ||
      !elementToken
    ) {
      throw invalidArguments(
        'Actions require pid, window_id, a fresh snapshot_id, and an element_token',
      );
    }
    const recorded = this.snapshots.get(windowKey(pid, windowId));
    if (
      !recorded ||
      recorded.runtimeGeneration !== this.runtimeGeneration ||
      recorded.snapshotId !== snapshotId
    ) {
      const error = new Error('Snapshot is stale; take a new snapshot of this window before acting');
      error.code = 'computer_use.snapshot_stale';
      throw error;
    }
    if (!recorded.tokens.has(elementToken)) {
      const error = new Error('element_token is not from the current snapshot of this window');
      error.code = 'computer_use.element_token_stale';
      throw error;
    }
    if (this.unknownWindows.has(windowKey(pid, windowId))) {
      const error = new Error(
        'The previous action on this window has an unknown outcome; snapshot again and do not retry that action',
      );
      error.code = 'computer_use.unknown_outcome';
      throw error;
    }
    return { pid, windowId, snapshotId, elementToken };
  }

  jevSettings() {
    try {
      return this.jev.getSettings() || { enabled: false, apiKey: '' };
    } catch {
      return { enabled: false, apiKey: '' };
    }
  }

  async choose(args) {
    const settings = this.jevSettings();
    if (settings.enabled !== true || !settings.apiKey) {
      const error = new Error(
        'Enable Jev in Settings → Computer use and save a TypeSafe API key',
      );
      error.code = 'computer_use.jev_not_configured';
      throw error;
    }
    if (this.grant.actionsAllowed !== true) {
      const error = new Error('The local grant does not permit background actions');
      error.code = 'computer_use.actions_not_consented';
      throw error;
    }
    const pid = Number(args.pid);
    const windowId = String(args.window_id || '');
    const snapshotId = String(args.snapshot_id || '');
    if (
      !Number.isSafeInteger(pid) ||
      pid <= 0 ||
      !/^\d+$/.test(windowId) ||
      !snapshotId
    ) {
      throw invalidArguments(
        'Jev requires pid, window_id, and a fresh snapshot_id',
      );
    }
    const recorded = this.snapshots.get(windowKey(pid, windowId));
    if (
      !recorded ||
      recorded.runtimeGeneration !== this.runtimeGeneration ||
      recorded.snapshotId !== snapshotId
    ) {
      const error = new Error('Snapshot is stale; take a new snapshot of this window before choosing');
      error.code = 'computer_use.snapshot_stale';
      throw error;
    }
    const built = buildNativeCandidates(recorded.elements, { text: args.text });
    const choice = await this.jev.choose({
      apiKey: settings.apiKey,
      goal: args.goal,
      snapshotId: recorded.snapshotId,
      captureId: recorded.captureId,
      candidates: built.candidates,
      elements: built.elements,
      history: args.history,
    });
    if (choice.selectedId === 'reobserve' || choice.selectedId === 'abstain') {
      return {
        selected_id: choice.selectedId,
        outcome: choice.selectedId,
        confidence: choice.confidence,
        model: choice.model,
      };
    }
    const resolved = built.resolve.get(choice.selectedId);
    if (!resolved) {
      const error = new Error('Jev selected an action that was not offered');
      error.code = 'computer_use.jev_invalid_choice';
      throw error;
    }
    return {
      selected_id: choice.selectedId,
      outcome: 'act',
      action: resolved.action,
      element_token: resolved.elementToken,
      ...(resolved.text ? { text: resolved.text } : {}),
      pid,
      window_id: windowId,
      snapshot_id: snapshotId,
      confidence: choice.confidence,
      model: choice.model,
    };
  }

  validateActStep(step) {
    if (!ALLOWED_ACTIONS.includes(step.action)) {
      throw invalidArguments('action must be click, type_text, press_key, or hotkey');
    }
    if (step.action === 'type_text') {
      const text = String(step.text ?? '');
      if (!text || text.length > MAX_TYPE_TEXT_CHARS) {
        throw invalidArguments(`text must be 1 to ${MAX_TYPE_TEXT_CHARS} characters`);
      }
    } else if (step.action === 'press_key') {
      const keyName = String(step.key || '').trim().toLowerCase();
      if (!ALLOWED_KEYS.has(keyName)) {
        throw invalidArguments('key is not in the allowlisted background key set');
      }
    } else if (step.action === 'hotkey') {
      const keys = Array.isArray(step.keys)
        ? step.keys.map((item) => String(item).trim().toLowerCase())
        : [];
      if (
        keys.length < 1 ||
        keys.length > MAX_HOTKEY_KEYS ||
        keys.some((item) => !ALLOWED_KEYS.has(item))
      ) {
        throw invalidArguments('keys must be a short allowlisted chord');
      }
    }
  }

  async performBackgroundStep(step, pid, windowId, { focusFirst }) {
    const nativeWindowId = BigInt(windowId);
    let result;
    if (step.action === 'click' || focusFirst) {
      result = await this.driver.click(
        this.clickInput(pid, nativeWindowId, step.elementToken),
      );
      if (step.action === 'click') return result;
    }
    if (step.action === 'type_text') {
      return this.driver.typeText({
        text: String(step.text),
        target: this.windowTarget(pid, nativeWindowId),
      });
    }
    if (step.action === 'press_key') {
      return this.driver.pressKey({
        key: String(step.key).trim().toLowerCase(),
        target: this.windowTarget(pid, nativeWindowId),
      });
    }
    return this.driver.hotkey({
      keys: step.keys.map((item) => String(item).trim().toLowerCase()),
      target: this.windowTarget(pid, nativeWindowId),
    });
  }

  markUnknown(callId, args) {
    if (callId) this.actionOutcomes.set(String(callId), 'unknown');
    const pid = Number(args?.pid);
    const windowId = String(args?.window_id || '');
    if (Number.isSafeInteger(pid) && /^\d+$/.test(windowId)) {
      this.unknownWindows.add(windowKey(pid, windowId));
    }
  }

  async act(callId, args) {
    if (this.grant.actionsAllowed !== true) {
      const error = new Error('The local grant does not permit background actions');
      error.code = 'computer_use.actions_not_consented';
      throw error;
    }
    const steps = normalizeActSteps(args);
    for (const step of steps) this.validateActStep(step);
    const key = callId ? String(callId) : '';
    const prior = key ? this.actionOutcomes.get(key) : null;
    if (prior === 'unknown' || prior === 'started') {
      const error = new Error(
        'This action already has an unknown outcome and must not be retried',
      );
      error.code = 'computer_use.unknown_outcome';
      throw error;
    }
    if (this.actionCount + steps.length > MAX_ACTIONS_PER_GRANT) {
      const error = new Error('The local action lease has reached its action limit');
      error.code = 'computer_use.action_limit';
      throw error;
    }
    const targets = steps.map((step) =>
      this.requireFreshElement({
        pid: args.pid,
        window_id: args.window_id,
        snapshot_id: args.snapshot_id,
        element_token: step.elementToken,
      }),
    );
    const target = targets[0];

    if (key) this.actionOutcomes.set(key, 'started');
    this.actionCount += steps.length;
    try {
      const results = [];
      for (let index = 0; index < steps.length; index += 1) {
        const step = steps[index];
        const focusFirst = step.action !== 'click' && index === 0;
        results.push(
          await this.performBackgroundStep(step, target.pid, target.windowId, {
            focusFirst,
          }),
        );
      }
      if (key) this.actionOutcomes.set(key, 'completed');
      const verification = await this.captureVerification(
        target.pid,
        target.windowId,
        args,
      );
      const primary = steps[0];
      return {
        outcome: 'completed',
        action: primary.action,
        steps: steps.map((step) => step.action),
        pid: target.pid,
        window_id: target.windowId,
        snapshot_id: target.snapshotId,
        delivery_mode: 'background',
        foreground_used: false,
        click: jsonSafe(results[0]),
        result: jsonSafe(results[results.length - 1]),
        results: jsonSafe(results),
        verification: verification.structured,
        ...(verification.artifact ? { artifact: verification.artifact } : {}),
      };
    } catch (error) {
      this.markUnknown(callId, args);
      if (error.code) throw error;
      const unknown = new Error(
        'The background action has an unknown outcome; snapshot again and do not retry it',
      );
      unknown.code = 'computer_use.unknown_outcome';
      unknown.cause = error;
      throw unknown;
    }
  }

  windowTarget(pid, windowId) {
    if (this.sdk?.ActionTarget?.Window?.new) {
      return this.sdk.ActionTarget.Window.new({ pid, windowId });
    }
    return { pid, windowId };
  }

  clickInput(pid, windowId, elementToken) {
    const sdk = this.sdk || {};
    const target = this.windowTarget(pid, windowId);
    const position = sdk.ClickPosition?.Element?.new
      ? sdk.ClickPosition.Element.new({ elementToken })
      : { elementToken };
    const deliveryMode = sdk.InputDeliveryMode?.Background ?? 0;
    const input = { target, position, deliveryMode };
    return sdk.ClickInput?.new ? sdk.ClickInput.new(input) : input;
  }

  isGenerationActive(generation) {
    return this.active && this.runtimeGeneration === Number(generation);
  }

  async captureVerification(pid, windowId, args) {
    const forceScreenshot = args.verify_screenshot === true;
    const screenshotsAllowed = this.grant.screenshotsAllowed === true;
    const axFirst = await this.captureWindow(pid, windowId, {
      includeScreenshot: forceScreenshot && screenshotsAllowed,
    });
    if (
      forceScreenshot ||
      !screenshotsAllowed ||
      hasAccessibilityElements(axFirst.structured)
    ) {
      return axFirst;
    }
    return this.captureWindow(pid, windowId, { includeScreenshot: true });
  }

  async snapshotWindow(args) {
    const wantScreenshot = this.grant.screenshotsAllowed === true;
    const captured = await this.captureWindow(args.pid, args.window_id, {
      includeScreenshot: wantScreenshot,
      maxDimension: args.max_dimension,
    });
    return {
      ...captured.structured,
      ...(captured.artifact ? { artifact: captured.artifact } : {}),
    };
  }

  windowStateInput(
    pid,
    windowId,
    { includeScreenshot, includeAccessibilityTree, screenshotPath, maxImageDimension },
  ) {
    return {
      pid,
      windowId: BigInt(windowId),
      includeAccessibilityTree: includeAccessibilityTree !== false,
      includeScreenshot: includeScreenshot === true,
      timeoutMs: 5000,
      ...(includeScreenshot === true
        ? { screenshotOutFile: screenshotPath, maxImageDimension }
        : {}),
    };
  }

  classifySnapshotError(error) {
    if (String(error?.code || '').startsWith('computer_use.')) return error;
    const classified = new Error(safeNativeErrorMessage(error));
    classified.code = 'computer_use.snapshot_failed';
    classified.cause = error;
    return classified;
  }

  async readScreenshotArtifact(screenshotPath, output) {
    let stat;
    try {
      stat = await fsp.stat(screenshotPath);
    } catch (error) {
      if (error && error.code === 'ENOENT') return null;
      throw error;
    }
    if (!stat.isFile() || stat.size <= 0) return null;
    if (stat.size > this.maxArtifactBytes) {
      const error = new Error(
        `Screenshot exceeds the ${this.maxArtifactBytes}-byte local egress limit`,
      );
      error.code = 'computer_use.artifact_too_large';
      throw error;
    }
    const bytes = await fsp.readFile(screenshotPath);
    const artifact = {
      id: path.basename(screenshotPath, '.png'),
      path: screenshotPath,
      contentType: output.screenshotMimeType || 'image/png',
      bytes: stat.size,
      sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
    };
    this.artifacts.set(artifact.id, screenshotPath);
    return artifact;
  }

  async captureWindow(
    pidValue,
    windowIdValue,
    {
      includeScreenshot,
      includeAccessibilityTree = true,
      maxDimension,
      retried = false,
    } = {},
  ) {
    const pid = Number(pidValue);
    if (
      !Number.isSafeInteger(pid) ||
      pid <= 0 ||
      !/^\d+$/.test(String(windowIdValue || ''))
    ) {
      throw invalidArguments('pid and window_id identify the exact target window');
    }
    const maxImageDimension = Math.min(
      2048,
      Math.max(320, Number(maxDimension) || 1280),
    );
    const artifactDir = path.join(this.stateDir, 'artifacts');
    await fsp.mkdir(artifactDir, { recursive: true, mode: 0o700 });
    const artifactId = crypto.randomUUID();
    const screenshotPath = path.join(artifactDir, `${artifactId}.png`);
    try {
      const output = await this.driver.getWindowState(
        this.windowStateInput(pid, windowIdValue, {
          includeScreenshot,
          includeAccessibilityTree,
          screenshotPath,
          maxImageDimension,
        }),
      );
      this.rememberSnapshot(pid, String(windowIdValue), output);
      const {
        screenshotFilePath: _screenshotFilePath,
        images: _images,
        ...structured
      } = output;
      const payload = {
        ...structured,
        images: [],
      };
      if (includeScreenshot !== true) {
        await fsp.rm(screenshotPath, { force: true });
        return { structured: payload };
      }
      const artifact = await this.readScreenshotArtifact(screenshotPath, output);
      if (artifact) return { structured: payload, artifact };
      await fsp.rm(screenshotPath, { force: true });
      const omittedReason =
        structured.screenshot_omitted_reason ||
        'The window screenshot was not written';
      if (
        !retried &&
        includeAccessibilityTree !== false &&
        isAxWindowUnresolved(omittedReason)
      ) {
        return this.captureWindow(pid, windowIdValue, {
          includeScreenshot: true,
          includeAccessibilityTree: false,
          maxDimension,
          retried: true,
        });
      }
      return {
        structured: {
          ...payload,
          screenshot_omitted: true,
          screenshot_omitted_reason: omittedReason,
        },
      };
    } catch (error) {
      await fsp.rm(screenshotPath, { force: true });
      if (includeScreenshot === true && !retried && isAxWindowUnresolved(error)) {
        return this.captureWindow(pid, windowIdValue, {
          includeScreenshot: true,
          includeAccessibilityTree: false,
          maxDimension,
          retried: true,
        });
      }
      if (includeScreenshot === true && !retried) {
        const fallback = await this.captureWindow(pid, windowIdValue, {
          includeScreenshot: false,
          includeAccessibilityTree: true,
          maxDimension,
          retried: true,
        });
        return {
          structured: {
            ...fallback.structured,
            screenshot_omitted: true,
            screenshot_omitted_reason: safeNativeErrorMessage(error),
          },
        };
      }
      throw this.classifySnapshotError(error);
    }
  }

  async releaseArtifact(artifactId) {
    const key = String(artifactId);
    const artifactPath = this.artifacts.get(key);
    if (!artifactPath) return false;
    this.artifacts.delete(key);
    await fsp.rm(artifactPath, { force: true });
    return true;
  }

  async stop() {
    const driver = this.driver;
    const manifestPath = this.manifestPath;
    this.driver = null;
    this.sdk = null;
    this.grant = null;
    this.manifestPath = null;
    this.snapshots.clear();
    this.actionOutcomes.clear();
    this.unknownWindows.clear();
    this.actionCount = 0;
    if (this.expirationTimer) clearTimeout(this.expirationTimer);
    this.expirationTimer = null;
    if (driver) {
      try {
        await driver.shutdown();
      } finally {
        driver.uniffiDestroy?.();
      }
    }
    const artifactPaths = [...this.artifacts.values()];
    this.artifacts.clear();
    await Promise.all(
      artifactPaths.map((artifactPath) => fsp.rm(artifactPath, { force: true })),
    );
    if (manifestPath) await fsp.rm(manifestPath, { force: true });
  }
}

module.exports = {
  ACTION_CAPABILITIES,
  CURATED_ACTION_TOOLS,
  CURATED_OBSERVATION_TOOLS,
  CURATED_TOOLS: CURATED_OBSERVATION_TOOLS,
  ComputerUseBroker,
  OBSERVATION_CAPABILITIES,
  jsonSafe,
};
