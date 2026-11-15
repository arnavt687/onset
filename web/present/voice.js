/* Onset presentation: what a spoken phrase means, and when to act on it.
 *
 * Nothing here touches the page or the microphone, so it runs under Node (tests/test_present_voice.py).
 *
 *   matchCommands(text, { scenes, open })   every command in a transcript, in spoken order
 *   matchCommand(text, { scenes })          the first one, or null
 *   makeListener({ scenes })                turns the recogniser's growing, revised results into
 *                                           commands that each fire once
 *
 * The presenter talks through the pitch while this listens, so a command has to work in the
 * middle of a sentence ("okay, let's move on to the problem") without ordinary sentences moving
 * the deck ("the next step is a clinician-led study"). Three kinds of phrase:
 *   - whole phrases ("next slide", "go back", "go to slide three") fire wherever they are said;
 *   - phrases that are also ordinary English ("next", "move on", "go on", "jump to four") look at
 *     the words around them: "next" does not fire after "the" or before "step", "go on" does not
 *     fire before "to have another stroke";
 *   - single words that are everywhere in normal talk ("back", "forward", "continue") fire only
 *     said by themselves: after a pause or a lead-in ("okay", "so", "let's"), with nothing after.
 * A " | " in the text is a pause (the recogniser closed an utterance there).
 */

export const HOLD_MS = 500;      // how long a phrase that depends on the next word waits for it
export const COOLDOWN_MS = 900;  // the same command again within this is the same command

const CARD = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12 };
const ORD = { first: 1, second: 2, third: 3, fourth: 4, forth: 4, fifth: 5, sixth: 6, seventh: 7, eighth: 8, ninth: 9, tenth: 10, eleventh: 11, twelfth: 12 };
const SOUNDS_LIKE = { for: 4, fore: 4, to: 2, too: 2 };
const NUM = `(?<n>\\d{1,2}|${Object.keys(CARD).join('|')})`;
const NTH = `(?<n>\\d{1,2}(?:st|nd|rd|th)|${Object.keys(ORD).join('|')})`;
const toNumber = s => (/^\d/.test(s) ? parseInt(s, 10) : CARD[s] ?? ORD[s] ?? SOUNDS_LIKE[s]);

export const norm = t => String(t).toLowerCase().replace(/[’`]/g, "'").replace(/\|/g, ' | ')
  .replace(/[^a-z0-9'| ]+/g, ' ').replace(/(^| )'+|'+(?= |$)/g, '$1').replace(/\s+/g, ' ').trim();

/* words that may come before or after a command said in passing */
const LEAD = /(?:^| )(?:okay|ok|so|and|now|um|uh|er|alright|right|sorry|oops|wait|actually|please|yeah|yes|yep|then|just|well|cool|great|good|perfect|anyway|anyways|let'?s|we'll|i'll|we can|we will|i will|i can|you can|can we|shall we|could we|can you|let me|let us)$/;
const CONT = /^(?:please|so|okay|ok|now|um|uh|er|alright|all right|right|sorry|thanks|thank you|cool|great|good|perfect|anyway|anyways|yeah|yes|next|slide|again)(?: |$)/;
/* "on slide three we showed", "from the first slide": talking about a slide, not asking for it */
const ABOUT = /(?:^| )(?:on|in|from|of|since|before|after|by|at|than|saw|was|is)(?: the| that| this| our)?$/;

const GO_TO = "(?:(?:go|going|jump|jumping|skip|skipping|switch|move|flip|hop|head|cut|return)(?: back| over| straight| right)? to|(?:take|bring) (?:us|me|it)(?: back)? to)";
const TO = `(?:${GO_TO}|back to|over to)`;
const OF_THE_TALK = /^of(?! the (?:talk|deck|presentation|slides|pitch|show)(?: |$))(?: |$)/; // "the end of the call" is not the last slide
const SLIDE = '(?:slide|slides|scene|page|section)';
const OPEN = "(?:open|show|bring up|pull up|put up|switch to|go to|back to|over to|jump to|flip to|cut to|take (?:us|me) to|let'?s (?:see|look at|watch|open|go to))";
const CHECK = "(?:show|check|open|go to|jump to|switch to|back to|on to|onto|over to|try|test|let'?s (?:see|do|try|look at|check|test))(?: me| us)?(?: the| a)?";

const first = () => 1, last = (m, scenes) => scenes, said = m => toNumber(m.groups.n);
const rule = (cmd, label, src, o = {}) => ({ cmd, label, re: new RegExp(`(?<![a-z0-9'])(?:${src})(?![a-z0-9'])`, 'g'), ...o });

/* Options: notBefore / notAfter: the words just before / after must not match. onlyBefore /
   onlyAfter: they must match, or there are none (a pause). weak: ordinary English that is only a
   command in the right place, so never taken from the recogniser's second guesses.
   closed: only once the utterance is complete. Earlier rules win ties. */
const RULES = [
  // ── back
  rule('back', 'Back', '(?:(?:go|jump|flip|skip) )?back to the (?:last|previous|prior) (?:slide|one|page)'),
  rule('back', 'Back', '(?:previous|previews|prior) (?:slide|slides|slid|side|one|page)'),
  rule('back', 'Back', '(?:go|jump|flip|skip) (?:back|black|backward|backwards)(?: (?:one|a)(?: slide| step| page)?)?|goback',
    { notAfter: /^(?:to|home|in|into|on|over|through|out|up|down|and forth|for(?! a (?:second|sec|moment|minute|bit)(?: |$))|with|there|again)(?: |$)/ }),
  rule('back', 'Back', 'back (?:one(?: slide| step| page)?|a (?:slide|step|page))|one back|one (?:slide|step|page) back|back please'),
  rule('back', 'Back', 'back up(?: one| a slide)?', { notBefore: /(?:^| )(?:a|the|our|as|for|my|your)$/, notAfter: /^(?:plan|option|demo|call|data|copy|the|your|our|my|their|his|her|this|that)(?: |$)/ }),
  rule('back', 'Back', 'previous', { weak: true, notBefore: /(?:^| )(?:the|a|an|our|my|your|his|her|their|its|any|no|of|from|in|with|on|than|to)$/, onlyAfter: CONT }),
  rule('back', 'Back', 'back', { weak: true, onlyBefore: LEAD, onlyAfter: CONT }),

  // ── a slide by number, the first, the last
  rule('goto', '', `(?:${TO} )?(?:the )?${SLIDE} (?:number )?${NUM}`, { n: said, notBefore: ABOUT }),
  rule('goto', '', `(?:${TO} )?(?:the )?${NTH} ${SLIDE}`, { n: said, notBefore: ABOUT }),
  rule('goto', '', `${TO} (?:the )?${NTH} one`, { n: said }),
  rule('goto', '', `${GO_TO} (?:number )?${NUM}`, { n: said, weak: true, onlyAfter: CONT }),
  rule('goto', '', '(?:go|jump|skip)(?: back| over)? to (?:slide )?(?<n>for|fore|to|too)', { n: said, weak: true, closed: true, onlyAfter: /^$/ }),
  rule('goto', '', `(?:${TO} )?(?:the )?(?:very )?(?:title|cover|opening|intro) (?:slide|page)`, { n: first, notBefore: ABOUT }),
  rule('goto', '', `${TO} the (?:very )?(?:start|beginning|top)`, { n: first, notAfter: OF_THE_TALK }),
  rule('goto', '', 'start over|start from the (?:top|beginning|start)|take it from the top|restart the (?:deck|slides|presentation|talk|pitch)',
    { n: first, notBefore: /(?:^| )(?:to|they|you|would|will|must|might)$/ }),
  rule('goto', '', `(?:${TO} )?(?:the )?(?:very )?(?:last|final|closing|end|ending) (?:slide|page)`, { n: last, notBefore: ABOUT }),
  rule('goto', '', `${TO} the (?:very )?(?:last|final) one`, { n: last }),
  rule('goto', '', `${TO} the (?:very )?end`, { n: last, notAfter: OF_THE_TALK }),

  // ── next
  rule('next', 'Next', '(?:next|nex|necks) (?:slide|slides|slid|slight|sly|flight|side|page|please)|text (?:slide|slides|slight)'),
  rule('next', 'Next', '(?:next|nex|necks) one', { notAfter: /^(?:hundred|thousand|million|of|or|in|is|was|will|would|we|to|that|who|which)(?: |$)/ }),
  rule('next', 'Next', 'go next|keep going|(?:go|skip) forwards?|skip ahead|advance (?:the )?slides?'),
  rule('next', 'Next', 'mov(?:e|ing) (?:on|along)', { notAfter: /^(?:from|with|without|after|in)(?: |$)/ }),
  rule('next', 'Next', "go on|carry on|move forward|move ahead|keep moving", { weak: true, onlyAfter: CONT,
    notBefore: /(?:^| )(?:to|will|would|who|that|they|patients|people|survivors|many|some|most|four|can|could|might|may|and)$/ }),
  rule('next', 'Next', 'continue|forward|forwards|advance|onward|onwards|proceed', { weak: true, onlyBefore: LEAD, onlyAfter: CONT }),
  rule('next', 'Next', 'next', { weak: true,
    notBefore: /(?:^| )(?:the|a|an|our|my|your|his|her|their|its|this|that|what's|whats|what|is|are|was|comes?|coming|came|goes|happens?|happened|up|very|each|every|which)$/,
    notAfter: /^(?:step|steps|to|week|weeks|month|months|year|years|day|days|time|morning|night|door|generation|phase|stage|version|level|thing|things|question|call|check|patient|person|stroke|visit|appointment|study|trial|round|milestone|goal|few|couple|several|big|best|is|was|will|would|could|should|comes|came|up|we|we'll|we're|i|i'll|i'm|you|you'll|they|it|it's|he|she|there|the|a|an|our|in|on|for|with|after|of)(?: |$)/ }),

  // ── the live demo scene
  rule('live', 'Live view', `${OPEN}(?: me| us| you)?(?: the| our)? (?:live (?:view|call|dashboard|feed|screen)|dashboard|call view)`),
  rule('try', 'Browser demo', `(?:${OPEN}|try|use|run|start|launch)(?: me| us| you)?(?: the| our)? (?:in )?(?:browser|web|backup|back up|fallback) (?:demo|version)`),
  rule('demo', 'Live demo', "(?:start|begin|launch|open|run|do|show|go to|jump to|skip to|switch to|cut to|on to|onto|over to|back to|take (?:us|me) to|time for|ready for|let'?s (?:do|see|start|run|go to|get to|try))(?: me| us| you)?(?: the| a| our)? (?:live )?demo|let'?s call it|demo time|let'?s see it (?:live|in action)"),

  // ── the idea: the three checks, acting a change
  rule('smile', 'Smile', `${CHECK} (?:smile|face)(?: check| test)?`),
  rule('arms', 'Arms', `${CHECK} (?:arms|arm|arm drift|arm hold)(?: check| test)?`),
  rule('speech', 'Speech', `${CHECK} (?:speech|sentence|speaking)(?: check| test)?`),
  rule('change', 'Act a change', '(?:show|act|make|fake|simulate|try|demo)(?: me| us| them)?(?: out)? (?:a|the|that) change|act it out|what a change looks like'),
  rule('normal', 'Back to usual', 'back to (?:normal|usual|baseline|the baseline|the usual)|(?:undo|reset) (?:it|that|the change)'),

  // ── when it changes, and the voice itself
  rule('replay', 'Replay', '(?:play|show|run|watch|do|see) (?:it|that) (?:again|once more|one more time)|replay(?: it| that)?|play it back|one more time'),
  rule('voiceoff', 'Voice off', 'stop listening|voice off|(?:turn|switch) (?:the )?(?:voice|mic|microphone|listening) off|(?:turn|switch) off (?:the )?(?:voice|mic|microphone|listening)|mute (?:the )?(?:voice|mic|microphone)|stop (?:the )?voice (?:commands?|control)'),
];

/* "move on to the next slide" is one request, not two */
const JOINS = new Set(['to', 'the', 'onto', 'and', 'then', 'with', 'a', 'on', "let's", 'lets', 'go', 'we', 'can']);
const keyOf = m => m.cmd + (m.n || '');

export function matchCommands(text, { scenes = 7, open = false } = {}) {
  const words = [], breaks = new Set();
  for (const w of norm(text).split(' ')) { if (w === '|') breaks.add(words.length); else if (w) words.push(w); }
  if (!words.length) return [];
  const flat = words.join(' '), startAt = new Map(), endAt = new Map();
  for (let i = 0, at = 0; i < words.length; at += words[i].length + 1, i++) { startAt.set(at, i); endAt.set(at + words[i].length, i + 1); }
  // up to four words either side, never across a pause
  const before = i => { let a = i; while (a > 0 && i - a < 4 && !breaks.has(a)) a--; return words.slice(a, i).join(' '); };
  const after = i => { let b = i; while (b < words.length && b - i < 4 && !breaks.has(b)) b++; return words.slice(i, b).join(' '); };

  const found = [];
  for (const r of RULES) {
    r.re.lastIndex = 0;
    for (let m; (m = r.re.exec(flat));) {
      const start = startAt.get(m.index), end = endAt.get(m.index + m[0].length);
      const n = r.n ? r.n(m, scenes) : undefined;
      if (r.cmd === 'goto' && !(n >= 1 && n <= scenes)) continue;
      const pre = before(start), post = after(end), tail = open && end === words.length;
      if (pre && ((r.notBefore && r.notBefore.test(pre)) || (r.onlyBefore && !r.onlyBefore.test(pre)))) continue;
      if (tail && r.closed) continue;
      if (!tail && ((r.notAfter && r.notAfter.test(post)) || (r.onlyAfter && post && !r.onlyAfter.test(post)))) continue;
      const label = r.label || (n === 1 ? 'First slide' : n === scenes ? 'Last slide' : `Slide ${n}`);
      // pending: the next word could still rule it out, and it has not been said yet
      found.push({ cmd: r.cmd, n, label, start, end, phrase: m[0], weak: !!r.weak, pending: tail && !!(r.notAfter || r.onlyAfter) });
    }
  }
  found.sort((a, b) => a.start - b.start || (b.end - b.start) - (a.end - a.start));
  const out = [];
  let upTo = 0;
  for (const c of found) {
    if (c.start < upTo) continue; // inside a longer phrase that started earlier
    upTo = c.end;
    const p = out[out.length - 1];
    if (p && keyOf(p) === keyOf(c)) {
      const gap = words.slice(p.end, c.start);
      let paused = false; for (let i = p.end; i <= c.start; i++) paused = paused || breaks.has(i);
      if (!paused && gap.length <= 3 && gap.every(w => JOINS.has(w)) && (gap.length || p.phrase !== c.phrase)) {
        p.end = c.end; p.pending = c.pending; p.weak = p.weak && c.weak; p.phrase = words.slice(p.start, p.end).join(' ');
        continue;
      }
    }
    out.push(c);
  }
  return out;
}

export function matchCommand(text, opts) {
  const m = matchCommands(text, { ...opts, open: false })[0];
  return m ? { cmd: m.cmd, n: m.n, label: m.label, phrase: m.phrase } : null;
}

/* The recogniser reports an utterance many times while it is being said (interim results, which
 * it revises), then once more when it is complete. feed() takes the whole list each time
 * (results: [{ final, alts: [best guess, ...] }], now in ms) and returns the commands to run now.
 *   - A phrase is acted on the first time it appears, mid-sentence, and never again: a cursor
 *     marks how many words have been dealt with, and the complete utterance repeats the words.
 *   - A phrase the next word could rule out ("next" ... "step") waits HOLD_MS for that word;
 *     poll() fires it if nothing more was said. wait() is how long until then, or -1.
 *   - The same command again within COOLDOWN_MS is dropped.
 */
export function makeListener({ scenes = 7, holdMs = HOLD_MS, cooldownMs = COOLDOWN_MS } = {}) {
  let results = [], cursor = 0, hold = null, done = null, checked = 0, text = '';

  function accept(m, now, out) {
    cursor = Math.max(cursor, m.end);
    const key = keyOf(m), again = done && done.key === key && now - done.at < cooldownMs;
    if (!again) out.push({ cmd: m.cmd, n: m.n, label: m.label, phrase: m.phrase });
    done = { key, start: m.start, at: again ? done.at : now };
  }

  function run(now) {
    const out = [], parts = [], spans = [];
    let count = 0;
    for (const r of results) {
      const t = norm(r.alts[0] || '').replace(/ ?\| ?/g, ' ').trim(), k = t ? t.split(' ').length : 0;
      spans.push([count, count + k]); count += k;
      if (t) parts.push(t);
      if (r.final) parts.push('|');
    }
    text = parts.join(' ');
    const found = matchCommands(text, { scenes, open: results.length > 0 && !results[results.length - 1].final });
    let waiting = false;
    for (const m of found) {
      if (m.start < cursor) continue;
      const key = keyOf(m);
      // the recogniser revised earlier words, and the phrase already acted on moved along with them
      if (done && done.key === key && now - done.at < 4000 && Math.abs(m.start - done.start) <= 3
        && !found.some(x => x.start === done.start && keyOf(x) === key)) { cursor = m.end; done.start = m.start; continue; }
      if (m.pending) {
        if (!hold || hold.key !== key || hold.start !== m.start) hold = { key, start: m.start, at: now };
        if (now - hold.at < holdMs) { waiting = true; break; }
      }
      accept(m, now, out);
    }
    if (!waiting) hold = null;
    // an utterance that is complete with no command in its best guess: try the other guesses
    while (checked < results.length && results[checked].final) {
      const [a, b] = spans[checked], r = results[checked++];
      if (found.some(m => m.start < b && m.end > a)) continue;
      for (const alt of r.alts.slice(1)) {
        const m = matchCommands(alt, { scenes }).find(x => !x.weak);
        if (m) { accept({ ...m, start: a, end: b }, now, out); break; }
      }
    }
    return out;
  }

  return {
    feed(list, now) { results = list || []; return run(now); },
    poll(now) { return hold ? run(now) : []; },
    wait(now) { return hold ? Math.max(0, hold.at + holdMs - now) : -1; },
    heard() { return text; },
    /* the recogniser started again: its list of results starts again too */
    reset() { results = []; cursor = 0; hold = null; checked = 0; text = ''; if (done) done.start = -99; },
  };
}
