// Is the Chert line's Mac ready for audio? Read-only; uses the CLI's saved sign-in (~/.chert) and
// talks only to app.trychert.com. Prints no credentials and masks phone numbers.
//   node tools/chert_line_health.mjs [project-id]
// Without an argument: CHERT_PROJECT_ID (environment, then .env.local), then the CLI sign-in's project.
// Look for audio_ready: true. With false, callers hear nothing and the agent hears silence (docs/chert-audio.md).
// tools/doctor.py runs the same check (line readiness).
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const chertHome = process.env.CHERT_HOME || join(homedir(), '.chert');
function fromEnvFile(name) {
  const file = join(dirname(fileURLToPath(import.meta.url)), '..', '.env.local');
  if (!existsSync(file)) return undefined;
  for (const line of readFileSync(file, 'utf8').split(/\r?\n/)) {
    const m = line.match(new RegExp(`^\\s*${name}\\s*=\\s*["']?([^"'#\\s]*)`));
    if (m && m[1]) return m[1];
  }
}
function fromSignIn() {
  try { return JSON.parse(readFileSync(join(chertHome, 'credentials.json'), 'utf8')).project_id; } catch { return undefined; }
}
const project = process.argv[2] || process.env.CHERT_PROJECT_ID || fromEnvFile('CHERT_PROJECT_ID') || fromSignIn();
if (!project) { console.log('usage: node tools/chert_line_health.mjs [project-id]  (or set CHERT_PROJECT_ID)'); process.exit(2); }
function findToken(dir, depth = 0) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name), s = statSync(path);
    if (s.isDirectory()) { if (depth < 2 && !['dev', 'rooms', 'onboarding'].includes(name)) { const t = findToken(path, depth + 1); if (t) return t; } continue; }
    if (!name.endsWith('.json') || s.size > 65536) continue;
    try { const j = JSON.parse(readFileSync(path, 'utf8')); const t = j.access_token || j.token; if (typeof t === 'string' && t.length > 20) return t; } catch { /* not it */ }
  }
}
const token = existsSync(chertHome) ? findToken(chertHome) : undefined;
if (!token) { console.log('no saved Chert sign-in found'); process.exit(1); }
const mask = (s) => String(s ?? '').replace(/\+\d[\d ()-]{5,}\d/g, (m) => { const d = m.replace(/\D/g, ''); return `+${d[0]}…${d.slice(-2)}`; });
const r = await fetch(new URL('/api/v1/lines', 'https://app.trychert.com'), { redirect: 'error',
  headers: { authorization: `Bearer ${token}`, 'x-chert-project-id': project } });
if (!r.ok) { console.log('HTTP', r.status, (await r.text()).slice(0, 200)); process.exit(1); }
for (const line of (await r.json()).data.items) {
  const h = line.session_health ?? {}, rd = h.readiness ?? {};
  console.log(`${mask(line.display_name)}: status ${line.status}, worker ${h.status ?? '?'}, last heartbeat ${h.last_heartbeat_at ?? '?'}`);
  console.log(`  audio_ready ${rd.audio_ready}   video_ready ${rd.video_ready}   facetime_signed_in ${rd.facetime_signed_in}`);
  for (const c of rd.automatic?.checks ?? []) console.log(`  check ${c.id.padEnd(22)} ${c.outcome}${c.code ? ` (${c.code})` : ''}`);
  if (rd.audio_ready !== true) console.log('  !! audio is not ready on this Mac: calls will be silent both ways');
}
