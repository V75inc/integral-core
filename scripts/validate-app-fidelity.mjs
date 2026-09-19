#!/usr/bin/env node
/**
 * Engineer-level fidelity check against the Integral API (:4002 by default).
 * Validates tracks, content-profile fields, relation targets, views, and seed entries.
 *
 * Usage:
 *   node scripts/validate-app-fidelity.mjs [appNameOrId]
 * Env:
 *   API_BASE=http://127.0.0.1:4002
 *   EMAIL=admin@gointegral.app
 *   PASS=admin123
 *   OUT=.smoke-dup/fidelity-api-report.json
 */
import fs from 'fs';
import path from 'path';

const API = process.env.API_BASE || 'http://127.0.0.1:4002';
const EMAIL = process.env.EMAIL || 'admin@gointegral.app';
const PASS = process.env.PASS || 'admin123';
const OUT =
  process.env.OUT ||
  path.join(
    process.cwd(),
    '.smoke-dup',
    'fidelity-api-report.json',
  );
const WANT = process.argv[2] || '';

async function api(token, method, urlPath, body) {
  const r = await fetch(API + urlPath, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = { raw: text.slice(0, 500) };
  }
  return { ok: r.ok, status: r.status, json };
}

function fieldList(profile) {
  if (!profile) return [];
  const ets = profile.entry_types || profile.entryTypes || [];
  if (Array.isArray(ets) && ets.length) {
    const fields = [];
    for (const et of ets) {
      for (const f of et.fields || et.schema?.fields || []) {
        fields.push({
          key: f.key || f.name,
          name: f.name || f.key,
          type: f.type || f.field_type || f.kind,
          target:
            f.target_track_id ||
            f.targetTrackId ||
            f.related_track_id ||
            f.relation?.track_id ||
            f.config?.target_track_id ||
            null,
          raw: f,
        });
      }
    }
    return fields;
  }
  // flat shape
  for (const k of ['fields', 'content_fields', 'schema']) {
    const v = profile[k];
    if (Array.isArray(v)) {
      return v.map((f) => ({
        key: f.key || f.name,
        name: f.name || f.key,
        type: f.type || f.field_type || f.kind,
        target:
          f.target_track_id ||
          f.targetTrackId ||
          f.related_track_id ||
          null,
        raw: f,
      }));
    }
  }
  return [];
}

function viewsList(payload) {
  if (!payload) return [];
  if (Array.isArray(payload)) return payload;
  return payload.views || payload.items || payload.results || [];
}

function entriesList(payload) {
  if (!payload) return [];
  if (Array.isArray(payload)) return payload;
  return payload.entries || payload.items || payload.results || [];
}

async function main() {
  const checks = [];
  const note = (id, ok, detail = '') => {
    checks.push({ id, ok: !!ok, detail });
    console.log(`${ok ? 'PASS' : 'FAIL'} ${id}${detail ? ' — ' + detail : ''}`);
  };

  const login = await api(null, 'POST', '/api/auth/login', {
    email: EMAIL,
    password: PASS,
  });
  if (!login.ok || !login.json?.access_token) {
    console.error('login failed', login.status, login.json);
    process.exit(1);
  }
  const token = login.json.access_token;

  const appsR = await api(token, 'GET', '/api/apps?limit=50');
  const apps = appsR.json?.apps || appsR.json?.items || [];
  let app = null;
  if (WANT.startsWith('n.WorkspaceApp.') || WANT.startsWith('n.')) {
    app = apps.find((a) => a.id === WANT);
  } else if (WANT) {
    const re = new RegExp(WANT.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i');
    app = apps.find((a) => re.test(a.name || ''));
  } else {
    app =
      apps.find((a) => /car\s*rental|rental management/i.test(a.name || '')) ||
      apps[0];
  }
  note('app_found', !!app, app ? `${app.name} (${app.id})` : `want=${WANT} apps=${apps.length}`);
  if (!app) {
    fs.mkdirSync(path.dirname(OUT), { recursive: true });
    fs.writeFileSync(
      OUT,
      JSON.stringify({ ok: false, checks, apps }, null, 2),
    );
    process.exit(2);
  }

  const tracksR = await api(token, 'GET', `/api/apps/${app.id}/tracks`);
  const tracks = tracksR.json?.tracks || tracksR.json?.items || [];
  note('has_tracks', tracks.length > 0, `count=${tracks.length}`);

  const trackById = Object.fromEntries(tracks.map((t) => [t.id, t]));
  const names = tracks.map((t) => String(t.name || ''));
  const isCarRental = /car|rental/i.test(app.name || '');

  if (isCarRental) {
    note('cars_track', names.some((n) => /^cars?$/i.test(n)), JSON.stringify(names));
    note('rentals_track', names.some((n) => /rental/i.test(n)), JSON.stringify(names));
    note('renters_track', names.some((n) => /renter/i.test(n)), JSON.stringify(names));
    note('damage_track', names.some((n) => /damage/i.test(n)), JSON.stringify(names));
    note('track_count_ge_3', tracks.length >= 3, `count=${tracks.length}`);
  }

  const trackReports = [];
  for (const tr of tracks) {
    const cpR = await api(token, 'GET', `/api/tracks/${tr.id}/content-profile`);
    const viewsR = await api(token, 'GET', `/api/tracks/${tr.id}/views`);
    const entriesR = await api(
      token,
      'GET',
      `/api/tracks/${tr.id}/entries?limit=50`,
    );
    const fields = fieldList(cpR.json);
    const views = viewsList(viewsR.json);
    const entries = entriesList(entriesR.json);
    const relations = fields.filter((f) =>
      /relation|ref|link|lookup/i.test(String(f.type || '')),
    );

    note(
      `track_${tr.name}_has_fields`,
      fields.length > 0,
      `fields=${fields.length} status=${cpR.status}`,
    );
    note(
      `track_${tr.name}_has_view`,
      views.length >= 1,
      `views=${views.length} status=${viewsR.status}`,
    );
    note(
      `track_${tr.name}_has_seeds`,
      entries.length >= 1,
      `entries=${entries.length} status=${entriesR.status}`,
    );
    note(
      `track_${tr.name}_seeds_ge_2`,
      entries.length >= 2,
      `entries=${entries.length}`,
    );

    for (const rel of relations) {
      const tid = rel.target;
      const okTarget =
        !tid ||
        !!trackById[tid] ||
        names.some((n) => String(tid).includes(n));
      note(
        `track_${tr.name}_rel_${rel.key}_target`,
        okTarget || tid == null,
        `type=${rel.type} target=${tid || '(unresolved/inline)'}`,
      );
    }

    trackReports.push({
      name: tr.name,
      id: tr.id,
      fieldCount: fields.length,
      fields: fields.map(({ key, name, type, target }) => ({
        key,
        name,
        type,
        target,
      })),
      viewCount: views.length,
      views: views.map((v) => ({
        id: v.id,
        name: v.name,
        type: v.view_type || v.type,
      })),
      entryCount: entries.length,
      entryTitles: entries.slice(0, 8).map((e) => e.title || e.name || e.id),
      profileStatus: cpR.status,
      viewsStatus: viewsR.status,
      entriesStatus: entriesR.status,
    });
  }

  if (isCarRental) {
    const cars = trackReports.find((t) => /^cars?$/i.test(t.name));
    const rentals = trackReports.find((t) => /rental/i.test(t.name));
    const blob = JSON.stringify(trackReports).toLowerCase();
    if (cars) {
      note(
        'cars_registration',
        /registration/.test(blob),
        'scan fields',
      );
      note('cars_make_model', /make|model/.test(blob), 'scan fields');
      note('cars_status', /status/.test(JSON.stringify(cars).toLowerCase()));
      note(
        'cars_service_or_renewal',
        /service|renewal/.test(JSON.stringify(cars).toLowerCase()),
      );
      note(
        'cars_daily_rate',
        /daily[_ ]?rate/.test(JSON.stringify(cars).toLowerCase()),
      );
    }
    if (rentals) {
      const types = (rentals.fields || []).map((f) =>
        String(f.type || '').toLowerCase(),
      );
      const keys = (rentals.fields || []).map((f) =>
        String(f.key || f.name || '').toLowerCase(),
      );
      note(
        'rentals_has_car_relation',
        keys.some((k) => /car/.test(k)) &&
          (types.some((t) => /relation|ref|link/.test(t)) ||
            /car/.test(keys.join(','))),
        JSON.stringify(rentals.fields),
      );
      note(
        'rentals_has_renter_relation',
        keys.some((k) => /renter/.test(k)),
        JSON.stringify(rentals.fields),
      );
    }
  }

  const ok = checks.every((c) => c.ok);
  const report = {
    ok,
    generated_at: new Date().toISOString(),
    api: API,
    app: { id: app.id, name: app.name, description: app.description },
    trackCount: tracks.length,
    tracks: trackReports,
    checks,
    failCount: checks.filter((c) => !c.ok).length,
  };
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  // also mirror under /tmp smoke dir when present
  try {
    fs.mkdirSync('/tmp/integral-dup-smoke', { recursive: true });
    fs.writeFileSync(
      '/tmp/integral-dup-smoke/validate-fidelity.mjs',
      fs.readFileSync(new URL(import.meta.url)).toString?.() || '',
    );
  } catch {
    /* ignore */
  }
  console.log(`\nWrote ${OUT} ok=${ok} failCount=${report.failCount}`);
  process.exit(ok ? 0 : 2);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
