'use strict';

const TYPESAFE_SYSTEM_ONE_URL = 'https://api.typesafe.ai/v1/systemone';
const MAX_ACTION_CANDIDATES = 24;
const RESERVED_IDS = Object.freeze(['reobserve', 'abstain']);
const ROLE_CLASSES = new Set([
  'button',
  'toggle',
  'checkbox',
  'radio',
  'popup',
  'menu_item',
  'link',
  'text_input',
]);
const RISKY_LABEL = /\b(delete|send|purchase|buy|close|quit|uninstall)\b/i;
const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/;

function slug(value) {
  const text = String(value || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40);
  return text || 'control';
}

function roleClassFor(element) {
  const role = String(element?.role || element?.roleDescription || '')
    .toLowerCase()
    .replace(/^ax/, '');
  if (/(checkbox|check.?box)/.test(role)) return 'checkbox';
  if (/(radio)/.test(role)) return 'radio';
  if (/(switch|toggle)/.test(role)) return 'toggle';
  if (/(popup|combo|pop.?up|disclosure)/.test(role)) return 'popup';
  if (/(menu.?item)/.test(role)) return 'menu_item';
  if (/(link)/.test(role)) return 'link';
  if (/(text.?field|text.?area|edit|search.?field|text_input)/.test(role)) {
    return 'text_input';
  }
  if (/(button|cell)/.test(role)) return 'button';
  return null;
}

function elementLabel(element) {
  return String(element?.label || element?.title || element?.name || '')
    .trim()
    .slice(0, 200);
}

function elementState(element, roleClass) {
  if (element?.checked === true || element?.value === '1') return 'checked';
  if (element?.checked === false) return 'unchecked';
  if (roleClass === 'text_input') {
    return String(element?.value || '').trim() ? 'has_text' : 'empty';
  }
  if (element?.enabled === false) return 'enabled';
  return 'enabled';
}

function isUsableElement(element) {
  if (!element?.elementToken) return false;
  if (element.enabled === false || element.onScreen === false) return false;
  if (element.webContent === true || element.embeddedWeb === true) return false;
  return Boolean(elementLabel(element));
}

function buildNativeCandidates(elements, { text } = {}) {
  const resolve = new Map();
  const compact = [];
  const candidates = [];
  const usedIds = new Set();
  const source = Array.isArray(elements) ? elements : [];
  for (const element of source) {
    if (candidates.length >= MAX_ACTION_CANDIDATES) break;
    if (!isUsableElement(element)) continue;
    const roleClass = roleClassFor(element);
    if (!roleClass || !ROLE_CLASSES.has(roleClass)) continue;
    const label = elementLabel(element);
    if (RISKY_LABEL.test(label)) continue;
    let index = 0;
    let id = `${roleClass}.${slug(label)}`;
    while (usedIds.has(id) || !ID_PATTERN.test(id)) {
      index += 1;
      id = `${roleClass}.${slug(label)}.${index}`.slice(0, 64);
    }
    usedIds.add(id);
    const action = roleClass === 'text_input' && text ? 'type_text' : 'click';
    if (action === 'type_text' && (!text || String(text).length > 400)) continue;
    compact.push({
      role_class: roleClass,
      label,
      state: elementState(element, roleClass),
    });
    candidates.push({
      id,
      description:
        action === 'type_text'
          ? `Type into ${label}`
          : `Click the ${roleClass.replace('_', ' ')} ${label}`,
      source: 'ax',
    });
    resolve.set(id, {
      action,
      elementToken: String(element.elementToken),
      text: action === 'type_text' ? String(text) : undefined,
    });
  }
  candidates.push(
    { id: 'reobserve', description: 'Take a new snapshot before acting' },
    { id: 'abstain', description: 'Stop; do not act on this window' },
  );
  return { candidates, elements: compact, resolve };
}

function parseTypesafeChoice(body, allowedIds) {
  const answer =
    body?.choices?.candidate ||
    body?.answers?.candidate ||
    body?.choices?.driver_action;
  const selectedId = String(answer?.choice || '');
  if (!allowedIds.has(selectedId)) {
    const error = new Error('Jev selected an action that was not offered');
    error.code = 'computer_use.jev_invalid_choice';
    throw error;
  }
  const confidence = Number(answer?.confidence);
  return {
    selectedId,
    confidence:
      Number.isFinite(confidence) && confidence >= 0 && confidence <= 1
        ? confidence
        : 0,
    model: typeof body?.model === 'string' ? body.model : 'jev-latest',
  };
}

async function chooseWithTypesafe(
  {
    apiKey,
    goal,
    snapshotId,
    captureId,
    candidates,
    elements,
    history,
  },
  { fetchImpl = fetch } = {},
) {
  const key = String(apiKey || '').trim();
  if (!key) {
    const error = new Error('Jev is enabled but no TypeSafe API key is saved');
    error.code = 'computer_use.jev_key_required';
    throw error;
  }
  const boundedGoal = String(goal || '').trim().slice(0, 4000);
  if (!boundedGoal) {
    const error = new Error('Jev requires a goal for this window');
    error.code = 'computer_use.invalid_arguments';
    throw error;
  }
  const criteria = Object.fromEntries(
    candidates.map((item) => [item.id, item.description]),
  );
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20_000);
  let response;
  try {
    response = await fetchImpl(TYPESAFE_SYSTEM_ONE_URL, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${key}`,
        'Content-Type': 'application/json',
      },
      signal: controller.signal,
      body: JSON.stringify({
        model: 'jev-latest',
        state: {
          observation: {
            capture_id: String(captureId || snapshotId || 'snapshot'),
            regions: [],
            history: Array.isArray(history) ? history.slice(0, 16) : [],
            snapshot_id: snapshotId || null,
            elements: elements || [],
            candidate_sources: Object.fromEntries(
              candidates
                .filter((item) => item.source)
                .map((item) => [item.id, item.source]),
            ),
          },
        },
        questions: {
          candidate: {
            type: 'choice',
            instructions: boundedGoal,
            criteria,
          },
        },
      }),
    });
  } catch (error) {
    const failed = new Error('TypeSafe Jev did not respond');
    failed.code = 'computer_use.jev_unavailable';
    failed.cause = error;
    throw failed;
  } finally {
    clearTimeout(timer);
  }
  if (!response.ok) {
    const error = new Error(
      response.status === 401
        ? 'The TypeSafe API key was rejected'
        : 'TypeSafe Jev refused the choice request',
    );
    error.code =
      response.status === 401
        ? 'computer_use.jev_unauthorized'
        : 'computer_use.jev_unavailable';
    throw error;
  }
  const body = await response.json();
  return parseTypesafeChoice(body, new Set(Object.keys(criteria)));
}

module.exports = {
  MAX_ACTION_CANDIDATES,
  RESERVED_IDS,
  TYPESAFE_SYSTEM_ONE_URL,
  buildNativeCandidates,
  chooseWithTypesafe,
  parseTypesafeChoice,
  roleClassFor,
};
