// Ask Chert for its own record of calls: status, end reason and the event timeline.
// Uses the CLI's saved sign-in (~/.chert) and talks only to app.trychert.com. Prints no credentials.
//   node tools/chert_calls.mjs <project-id> <call-id> [<call-id> ...]
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const [project, ...calls] = process.argv.slice(2);
const home = process.env.CHERT_HOME || join(homedir(), '.chert');

function findToken(dir, depth = 0) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const s = statSync(path);
    if (s.isDirectory()) { if (depth < 2 && !['dev', 'rooms', 'onboarding'].includes(name)) { const t = findToken(path, depth + 1); if (t) return t; } continue; }
    if (!name.endsWith('.json') || s.size > 65536) continue;
    try {
      const j = JSON.parse(readFileSync(path, 'utf8'));
      const t = j.access_token || j.token || j.credential?.access_token;
      if (typeof t === 'string' && t.length > 20) return t;
    } catch { /* not it */ }
  }
}
const token = findToken(home);
if (!token) { console.log('no saved Chert sign-in found under', home, '->', readdirSync(home)); process.exit(1); }

const get = async path => {
  const r = await fetch(new URL(path, 'https://app.trychert.com'), { redirect: 'error',
    headers: { authorization: `Bearer ${token}`, 'x-chert-project-id': project } });
  const text = await r.text();
  try { return { status: r.status, data: JSON.parse(text).data ?? JSON.parse(text) }; } catch { return { status: r.status, data: text.slice(0, 200) }; }
};

for (const id of calls) {
  const call = await get(`/api/v1/calls/${id}`);
  console.log(`\n=== call ${id} (HTTP ${call.status})`);
  if (call.status !== 200) { console.log(call.data); continue; }
  const c = call.data;
  const keep = ['status', 'direction', 'mode', 'media', 'terminal_reason', 'terminal_detail', 'duration_seconds', 'created_at', 'answered_at', 'ended_at'];
  console.log(Object.fromEntries(keep.filter(k => k in c).map(k => [k, c[k]])));
  console.log('other fields:', Object.keys(c).filter(k => !keep.includes(k) && k !== 'facetime_call_events').join(', '));
  const inline = c.facetime_call_events;
  const ev = await get(`/api/v1/calls/${id}/events`);
  const events = ev.status === 200 ? (ev.data.events ?? ev.data) : inline;
  if (!Array.isArray(events)) { console.log('events:', ev.status, JSON.stringify(ev.data).slice(0, 300)); continue; }
  for (const e of events) {
    const { type, event_type, source, created_at, at, occurred_at, ...rest } = e;
    const extra = JSON.stringify(rest);
    console.log(' ', (created_at || occurred_at || at || '').slice(11, 23), (type || event_type || '?').padEnd(24), (source || '').padEnd(8), extra.length > 2 ? extra.slice(0, 260) : '');
  }
}
