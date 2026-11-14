// The edit of the demo video: beats in order. Each beat has at most one voice-over segment
// (tools/video/narration.json id) and one or more shots. A beat lasts pre + VO length + post;
// shots with `dur` get that many seconds and the last shot without one takes the rest.
//
// Shot sources
//   card: 'title' | 'end'                       a recording of web/video/index.html#title / #end (record_cards.ps1)
//   card: 'disclose'                            the disclosure card (cards.html)
//   phone: '<caption name>'                     a caption frame from tools/render_captions.py in a phone
//   deck: N, ss                                 the pitch-deck recording of slide N (record_deck.ps1)
//   dash: '<outcome>', ss, speed, crop          the dashboard recording (record_dash.ps1);
//                                               crop is [x, y, w, h] in 1920x1080 page pixels
//   video: 'path.mp4', ss, speed                any other clip (e.g. a real call recording)
// synth: true adds the "Synthetic demo run" tag (top right; 'tl' puts it top left). lower: {k, t, alert, pos} adds a lower-third
// (web/video/index.html design) for its first 4.4 s; pos 'bl' (default), 'br' or 'tl'.
// Phone cards take k (kicker), t (title) and p (text) instead of a lower-third.
//
// Morning re-cut: replace a `dash`/`phone` shot with {video: 'C:/.../call-usual.mp4', ss, speed}
// and drop `synth` on it (keep it on anything still synthetic).

const CROP_METERS = [0, 90, 1500, 844];          // agent video + Face/Arms meters
const CROP_CAREGIVER_TOP = [700, 70, 1220, 686];  // meters + caregiver iMessage header ('Dry run: not sent')
const CROP_CAREGIVER = [700, 354, 1220, 686];    // meters + the rest of the caregiver iMessage

module.exports = [
  { vo: 'n01_hook', pre: 0.9, post: 0.4, shots: [
    { card: 'title', ss: 0.5, dur: 6.0 },
    { deck: 2, ss: 0.4, lower: { k: 'The problem', t: 'The first 90 days home', pos: 'br' } },
  ] },
  { vo: 'n02_what', pre: 0.3, post: 0.5, shots: [
    { deck: 1, ss: 0.2, lower: { k: 'What it is', t: 'A check-in you FaceTime · no app', pos: 'br' } },
  ] },
  { vo: 'n03_disclose', pre: 0.4, post: 0.8, shots: [
    { card: 'disclose' },
  ] },
  { vo: 'n04_smile', pre: 0.3, post: 0.4, shots: [
    { phone: 'face_smile', dur: 2.6, synth: true, k: 'Check 1 of 3 · what the caller sees', t: 'Big smile' },
    { deck: 8, ss: 0.4 },
  ] },
  { vo: 'n05_arms', pre: 0.3, post: 0.4, shots: [
    { phone: 'arms_hold', dur: 3.0, synth: true, k: 'Check 2 of 3 · what the caller sees', t: 'Both arms up, 10 seconds' },
    { deck: 9, ss: 0.4 },
  ] },
  { vo: 'n06_speech', pre: 0.3, post: 0.4, shots: [
    { deck: 10, ss: 0.4, dur: 5.6, lower: { k: 'Check 3 of 3', t: 'Repeat a sentence', pos: 'br' } },
    { phone: 'sentence_go', synth: true, t: 'Every step is a caption', p: "Instructions, the countdown and the result are written on the agent's FaceTime video, so the check works with the sound off." },
  ] },
  { vo: 'n07_usual', pre: 0.3, post: 0.8, shots: [
    { dash: 'first_run', ss: 44.0, speed: 1, dur: 5.4, synth: true, lower: { k: 'Judge dashboard', t: 'First check-in: baseline recorded', pos: 'tl' } },
    { dash: 'no_change', ss: 21, speed: 2.5, dur: 3.3, synth: true, crop: CROP_METERS },
    { dash: 'no_change', ss: 43, speed: 1, synth: true, lower: { k: 'Usual morning', t: 'No change detected', pos: 'tl' } },
  ] },
  { vo: 'n08_change', pre: 0.6, post: 0.3, shots: [
    { dash: 'escalate', ss: 9.5, speed: 2.2, synth: true, crop: CROP_METERS, lower: { k: 'Simulated change', t: 'One-sided smile · arm drift · pause', alert: true } },
  ] },
  { vo: 'n09_red', pre: 1.4, post: 1.2, shots: [
    { dash: 'escalate', ss: 30, speed: 3.8, dur: 3.0, synth: true },
    { dash: 'escalate', ss: 42.6, speed: 1, synth: true },  // decision appears 1.8 s in, on "Change noticed"
  ] },
  { vo: 'n10_caregiver', pre: 0.6, post: 1.2, shots: [
    { dash: 'escalate', ss: 46.0, speed: 1, dur: 4.6, synth: 'tl', crop: CROP_CAREGIVER_TOP, lower: { k: 'Caregiver iMessage', t: 'Dry run · nothing was sent', alert: true } },
    { dash: 'escalate', ss: 50.6, speed: 1, synth: 'tl', crop: CROP_CAREGIVER },
  ] },
  { vo: 'n11_unclear', pre: 0.4, post: 0.8, shots: [
    { dash: 'unclear', ss: 43, speed: 1, synth: true, lower: { k: 'Unclear', t: 'Same answer: call 911 now', alert: true, pos: 'tl' } },
  ] },
  { vo: 'n12_how', pre: 0.5, post: 0.8, shots: [
    { deck: 7, ss: 0.4, lower: { k: 'How it works', t: 'No LLM in the decision path', pos: 'br' } },
  ] },
  { vo: 'n13_safety', pre: 0.4, post: 0.6, shots: [
    { deck: 12, ss: 0.4, lower: { k: 'Safety by design', t: 'Unclear means call 911' } },
  ] },
  { vo: 'n14_limits', pre: 0.4, post: 0.6, shots: [
    { deck: 13, ss: 0.4, lower: { k: 'Limits and what\u2019s next', t: 'Clinician-led validation study', pos: 'br' } },
  ] },
  { vo: null, pre: 0, post: 0, shots: [
    { card: 'end', ss: 0.2, dur: 7.5 },
  ] },
];
