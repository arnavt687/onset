// Full record of calls from Chert (ids and keys trimmed), for side-by-side comparison.
//   node tools/chert_call_raw.mjs <project-id> <call-id> [...]
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const [project, ...calls] = process.argv.slice(2);
const home = process.env.CHERT_HOME || join(homedir(), '.chert');
function findToken(dir, depth = 0) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name), s = statSync(path);
    if (s.isDirectory()) { if (depth < 2 && !['dev', 'rooms', 'onboarding'].includes(name)) { const t = findToken(path, depth + 1); if (t) return t; } continue; }
    if (!name.endsWith('.json') || s.size > 65536) continue;
    try { const j = JSON.parse(readFileSync(path, 'utf8')); const t = j.access_token || j.token; if (typeof t === 'string' && t.length > 20) return t; } catch { /* no */ }
  }
}
const token = findToken(home);
const skip = new Set(['id', 'project_id', 'worker_id', 'line_id', 'idempotency_key', 'masked_identity', 'assistant_version_id', 'integration_revision_id']);
for (const id of calls) {
  const r = await fetch(new URL(`/api/v1/calls/${id}`, 'https://app.trychert.com'), { redirect: 'error',
    headers: { authorization: `Bearer ${token}`, 'x-chert-project-id': project } });
  const c = (await r.json()).data;
  console.log(`\n=== ${id}`);
  for (const [k, v] of Object.entries(c)) {
    if (skip.has(k)) continue;
    if (k === 'facetime_call_events') {
      for (const e of v) {
        const { id: _i, call_id: _c, project_id: _p, event_type, source, created_at, occurred_at, ...rest } = e;
        console.log('  ev', String(occurred_at || created_at).slice(11, 23), String(event_type).padEnd(26), String(source).padEnd(13), JSON.stringify(rest).slice(0, 400));
      }
    } else console.log(' ', k.padEnd(34), JSON.stringify(v)?.slice(0, 300));
  }
}
