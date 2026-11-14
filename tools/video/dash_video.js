// Injected into the dashboard while it is recorded for the demo video (record.cjs --init).
// Matches the dashboard's words to the current escalation behaviour (onset/script.py:
// the caregiver is added to the call and 911 is strongly advised) and drops the run badge.
// The dashboard itself is not changed.
(() => {
  const SWAP = [
    ['Change noticed → call 911', 'Change noticed → caregiver added'],
    ['The person was told to call 911 now.', 'Caregiver added to the live call · 911 strongly advised.'],
    ["Couldn't measure clearly → call 911", 'Still unclear after a retry → caregiver added'],
    ['Treated exactly like a change: the person was told to call 911.', 'Handled like a change · 911 strongly advised.'],
    ["Onset never says you're fine. If you feel unwell, call 911.", 'If you feel unwell, call 911.'],
    ["Research prototype · not a medical device · never says you're fine", 'Research prototype · not a medical device'],
  ];
  const fix = root => {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      for (const [a, b] of SWAP) if (n.nodeValue.includes(a)) n.nodeValue = n.nodeValue.replace(a, b);
    }
  };
  addEventListener('DOMContentLoaded', () => {
    const st = document.createElement('style');
    st.textContent = '#runbadge{display:none!important}';
    document.head.appendChild(st);
    fix(document.body);
    new MutationObserver(ms => ms.forEach(m => fix(m.target))).observe(document.body, { subtree: true, childList: true, characterData: true });
  });
})();
