/* Onset caregiver view.

   Opened from Onset's iMessage after a check-in found a change (or couldn't measure clearly).
   - Main room, watch token (subscribe only): the agent's captioned video ("onset-video"), the
     caller's own camera (participant "chert-bridge-..."), their audio, and the agent's summary
     (participant attributes "onset.*", or a data packet on topic "onset").
   - Side room "<room>-cg", talk token (publish only): the caregiver's mic and camera, which the
     agent relays into the FaceTime call. Only one audio publisher may exist in the main room,
     so the caregiver never publishes there.
   While the agent relays ("onset.relay" == "on") its audio track carries the caregiver's own
   voice, so that track is muted here to avoid an echo.

   The link's keys are read once from the #fragment and wiped from the address bar. They go to
   the LiveKit URL in the link and nowhere else (no fetch, beacon or logging here). */
(function () {
  "use strict";

  var STORE_KEY = "onset.caregiver.link";
  var AGENT_LATE_MS = 20000;
  var $ = function (id) { return document.getElementById(id); };

  // ---------------------------------------------------------------- 1. read and hide the link
  var rawHash = location.hash || "";
  if (rawHash) {
    try { history.replaceState(null, "", location.pathname + location.search); } catch (e) { /* old browser */ }
  }
  // iOS can reload a backgrounded tab, which would lose the wiped fragment: keep it for this tab only.
  if (!rawHash) {
    try { rawHash = sessionStorage.getItem(STORE_KEY) || ""; } catch (e) { rawHash = ""; }
  }
  var link = window.OnsetLink ? window.OnsetLink.parse(rawHash) : { ok: false, error: "bad" };
  try {
    if (link.ok) sessionStorage.setItem(STORE_KEY, rawHash);
    else sessionStorage.removeItem(STORE_KEY);
  } catch (e) { /* storage off: reloading just needs the link again */ }
  rawHash = "";
  window.addEventListener("hashchange", function () { if (location.hash.length > 1) location.reload(); });

  // ---------------------------------------------------------------- 2. state
  var S = {
    phase: link.ok ? "idle" : link.error,   // idle|connecting|live|reconnecting|ended|error|missing|bad|expired|nolib
    talk: "off",                             // off|connecting|on
    muted: false,
    agent: null, agentSeen: false, agentLate: false,
    caller: null, callerSeen: false, callerLeft: false,
    outcome: "", summary: "", patient: "", relay: "off",
    canPlayAudio: true, canRejoin: true,
    endedText: "", errorText: "", hint: "",
  };
  var room = null, talkRoom = null, localTracks = [], agentTimer = null;
  var tracks = { agentVideo: null, callerVideo: null };
  var shown = { main: null, pip: null };

  function LK() { return window.LivekitClient || null; }
  function name() { return link.name || S.patient || ""; }
  function possessive(n) { return /s$/i.test(n) ? n + "'" : n + "'s"; }

  // ---------------------------------------------------------------- 3. rendering
  function setText(id, text) { var el = $(id); if (el.textContent !== text) el.textContent = text; }
  function show(id, on) { $(id).hidden = !on; }

  function statusLine() {
    var n = name();
    switch (S.phase) {
      case "missing": return ["bad", "This page needs the private link from Onset's message. Open that link again."];
      case "bad": return ["bad", "This link is incomplete. Open it again from Onset's message."];
      case "expired": return ["bad", "This link has expired." + (n ? " If you can't reach " + n + ", call 911." : " If you can't reach them, call 911.")];
      case "nolib": return ["bad", "Couldn't load the video player. Check your connection, then tap Try again."];
      case "idle": return ["idle", "Ready when you are."];
      case "connecting": return ["busy", "Connecting…"];
      case "reconnecting": return ["busy", "Signal lost. Reconnecting…"];
      case "error": return ["bad", S.errorText || "Couldn't connect."];
      case "ended": return ["bad", S.endedText || "The call has ended."];
    }
    // live
    if (S.talk === "on") return ["talk", S.muted ? "Talking, but your microphone is muted." : "Talking. " + (n || "They") + " can hear you."];
    if (S.talk === "connecting") return ["busy", "Turning on your microphone and camera…"];
    if (S.callerLeft) return ["warn", (n || "They") + " left the call. If you can't reach " + (n || "them") + ", call 911."];
    if (!S.agent) {
      if (S.agentSeen) return ["warn", "Onset has left the call."];
      return S.agentLate ? ["warn", "Connected, but Onset isn't in the call right now."] : ["busy", "Connected. Waiting for Onset…"];
    }
    return ["live", n ? "Watching " + possessive(n) + " call." : "Watching the call."];
  }

  function stageMessage() {
    var n = name();
    switch (S.phase) {
      case "missing": case "bad": return "No call to show.";
      case "expired": return "This link has expired.";
      case "nolib": case "error": return "Not connected.";
      case "idle": return "Tap to start, below, to watch " + (n ? possessive(n) + " " : "the ") + "FaceTime check-in.";
      case "connecting": case "reconnecting": return "Connecting…";
      case "ended": return "The call has ended.";
    }
    if (!S.agent && !tracks.callerVideo) return S.agentSeen ? "Onset has left the call." : "Waiting for Onset to join the call…";
    return "Waiting for video…";
  }

  function render() {
    var n = name();
    var verb = S.outcome === "unclear" ? "couldn't be completed clearly" : "found a change";
    setText("alertText", "Onset: " + (n ? possessive(n) + " check-in " : "a check-in ") + verb + " — call 911 if you can't reach " + (n || "them") + ".");

    var st = statusLine();
    $("status").className = "status " + st[0];
    setText("statusText", st[1]);

    var main = shown.main;
    $("stage").classList.toggle("short", !main && !(link.ok && (S.phase === "live" || S.phase === "connecting" || S.phase === "reconnecting")));
    show("mainVideo", !!main);
    show("stageMsg", !main);
    if (!main) setText("stageMsg", stageMessage());

    var sum = $("summary");
    if (S.summary) { setText("summary", S.summary); sum.className = ""; }
    else {
      sum.className = "pending";
      setText("summary", S.phase === "live" ? "Waiting for Onset's summary…"
        : link.ok ? "What Onset noticed appears here once you're connected."
        : "Not available without a working link. If you can't reach " + (n || "them") + ", call 911.");
    }

    var canRetry = link.ok && (S.phase === "error" || S.phase === "nolib" || (S.phase === "ended" && S.canRejoin));
    show("startBtn", S.phase === "idle" || canRetry);
    setText("startBtn", S.phase === "idle" ? "Tap to start" : "Try again");
    show("soundBtn", S.phase === "live" && !S.canPlayAudio);

    var live = S.phase === "live";
    show("talkBtn", live && !!link.talk && S.talk !== "on" && !!S.agent && !S.callerLeft);
    $("talkBtn").disabled = S.talk === "connecting";
    setText("talkBtn", S.talk === "connecting" ? "Starting…" : "Talk to " + (n || "them"));
    show("talkRow", live && S.talk === "on");
    $("muteBtn").setAttribute("aria-pressed", S.muted ? "true" : "false");
    setText("muteBtn", S.muted ? "Unmute" : "Mute");

    var hint = S.hint;
    if (!hint && live && !link.talk) hint = "This link can watch the call, but not talk.";
    setText("hint", hint);
    show("hint", !!hint);
  }

  // ---------------------------------------------------------------- 4. who is who
  function isCaller(p) { return !!p && /^chert-bridge-/.test(p.identity || ""); }
  function isAgent(p) {
    if (!p || isCaller(p)) return false;
    var lk = LK();
    if (p.isAgent === true) return true;
    if (lk && lk.ParticipantKind && p.kind === lk.ParticipantKind.AGENT) return true;
    return /^agent-/.test(p.identity || "");
  }

  function readAgent(p) {
    var a = (p && p.attributes) || {};
    if (a["onset.summary"]) S.summary = String(a["onset.summary"]).slice(0, 1200);
    if (a["onset.outcome"]) S.outcome = String(a["onset.outcome"]);
    if (a["onset.patient"] && window.OnsetLink) S.patient = window.OnsetLink.cleanName(String(a["onset.patient"]));
    S.relay = a["onset.relay"] === "on" ? "on" : "off";
    applyAudioMute();
  }

  function scanParticipants() {
    if (!room) return;
    var agent = null, caller = null;
    room.remoteParticipants.forEach(function (p) {
      if (!agent && isAgent(p)) agent = p;
      if (!caller && isCaller(p)) caller = p;
    });
    S.agent = agent;
    if (agent) { S.agentSeen = true; readAgent(agent); }
    S.caller = caller;
    if (caller) { S.callerSeen = true; S.callerLeft = false; }
    else if (S.callerSeen) S.callerLeft = true;
  }

  // ---------------------------------------------------------------- 5. media
  function videoEl(slot) { return $(slot === "main" ? "mainVideo" : "pipVideo"); }

  function layoutVideo() {
    var want = { main: tracks.agentVideo || tracks.callerVideo, pip: tracks.agentVideo ? tracks.callerVideo : null };
    ["main", "pip"].forEach(function (slot) {
      if (shown[slot] && shown[slot] !== want[slot]) { try { shown[slot].detach(videoEl(slot)); } catch (e) {} shown[slot] = null; }
    });
    ["main", "pip"].forEach(function (slot) {
      var el = videoEl(slot);
      if (want[slot] && shown[slot] !== want[slot]) { want[slot].attach(el); shown[slot] = want[slot]; }
      if (slot === "pip") el.hidden = !shown.pip;
    });
  }

  function applyAudioMute() {
    var els = $("audio").querySelectorAll("audio[data-from='agent']");
    for (var i = 0; i < els.length; i++) els[i].muted = S.relay === "on";
  }

  function onTrackSubscribed(track, pub, p) {
    var agent = isAgent(p), caller = isCaller(p);
    if (track.kind === "video") {
      if (agent && (!tracks.agentVideo || pub.trackName === "onset-video")) tracks.agentVideo = track;
      else if (caller && (!tracks.callerVideo || pub.source === "camera")) tracks.callerVideo = track;
      layoutVideo();
    } else if (track.kind === "audio" && (agent || caller)) {
      var el = track.attach();
      el.setAttribute("data-from", agent ? "agent" : "caller");
      $("audio").appendChild(el);
      applyAudioMute();
    }
    scanParticipants();
    render();
  }

  function onTrackUnsubscribed(track) {
    if (tracks.agentVideo === track) tracks.agentVideo = null;
    if (tracks.callerVideo === track) tracks.callerVideo = null;
    layoutVideo();
    if (track.kind === "audio") {
      try { track.detach().forEach(function (el) { el.remove(); }); } catch (e) {}
    }
    render();
  }

  function clearMedia() {
    tracks.agentVideo = tracks.callerVideo = null;
    layoutVideo();
    $("audio").textContent = "";
  }

  // ---------------------------------------------------------------- 6. the watch room
  function friendlyConnectError(err) {
    var msg = String((err && err.message) || "");
    var status = err && err.status;
    if (Date.now() / 1000 >= link.expires) return ["expired", ""];
    if (status === 401 || status === 403 || /token|expired|unauthori[sz]ed|not allowed|permission/i.test(msg))
      return ["error", "This link is no longer valid. It may have expired. If you can't reach " + (name() || "them") + ", call 911."];
    if (navigator.onLine === false) return ["error", "You're offline. Reconnect, then tap Try again."];
    return ["error", "Couldn't connect to the call. Check your signal, then tap Try again."];
  }

  function onDisconnected(reason) {
    if (S.phase === "ended") return;
    var R = (LK() && LK().DisconnectReason) || {};
    var text = "The call has ended.";
    if (reason === R.DUPLICATE_IDENTITY) text = "This link was opened on another screen, so this one stopped.";
    else if (reason === R.PARTICIPANT_REMOVED) text = "Onset closed the caregiver view.";
    else if (reason === R.JOIN_FAILURE || reason === R.SIGNAL_CLOSE || reason === R.STATE_MISMATCH) text = "The connection dropped. Tap Try again.";
    if (Date.now() / 1000 >= link.expires && reason !== R.ROOM_DELETED) {
      S.phase = "expired";
      try { sessionStorage.removeItem(STORE_KEY); } catch (e) {}
    }
    else { S.phase = "ended"; S.endedText = text; }
    S.canRejoin = reason !== R.ROOM_DELETED && reason !== R.PARTICIPANT_REMOVED;
    stopTalk(false);
    clearMedia();
    S.agent = S.caller = null;
    room = null;
    clearTimeout(agentTimer);
    render();
  }

  function start() {
    var lk = LK();
    if (!link.ok) return;
    if (Date.now() / 1000 >= link.expires) { S.phase = "expired"; render(); return; }
    if (!lk) { S.phase = "nolib"; render(); return; }
    try { lk.setLogLevel("warn"); } catch (e) {}
    S.phase = "connecting"; S.hint = ""; S.endedText = ""; S.errorText = "";
    S.agentLate = false; S.callerSeen = false; S.callerLeft = false;
    render();

    var r = new lk.Room({ adaptiveStream: true, dynacast: false, disconnectOnPageLeave: true });
    var E = lk.RoomEvent;
    room = r;
    r.on(E.TrackSubscribed, onTrackSubscribed)
     .on(E.TrackUnsubscribed, onTrackUnsubscribed)
     .on(E.ParticipantConnected, function () { scanParticipants(); render(); })
     .on(E.ParticipantDisconnected, function (p) {
       if (isCaller(p)) { tracks.callerVideo = null; layoutVideo(); }
       scanParticipants(); render();
     })
     .on(E.ParticipantAttributesChanged, function (changed, p) { if (isAgent(p)) { readAgent(p); render(); } })
     .on(E.DataReceived, function (payload, p, kind, topic) {
       if (topic !== "onset") return;
       try {
         var msg = JSON.parse(new TextDecoder().decode(payload));
         if (msg && msg.type === "summary") {
           if (typeof msg.text === "string" && msg.text && !(S.agent && S.agent.attributes && S.agent.attributes["onset.summary"])) S.summary = msg.text.slice(0, 1200);
           if (typeof msg.outcome === "string" && !S.outcome) S.outcome = msg.outcome;
           render();
         }
       } catch (e) { /* not ours */ }
     })
     .on(E.AudioPlaybackStatusChanged, function () { S.canPlayAudio = r.canPlaybackAudio; render(); })
     .on(E.Reconnecting, function () { if (S.phase === "live") { S.phase = "reconnecting"; render(); } })
     .on(E.Reconnected, function () { if (S.phase === "reconnecting") { S.phase = "live"; scanParticipants(); render(); } })
     .on(E.Disconnected, function (reason) { if (room === r) onDisconnected(reason); });

    // Inside the tap: lets iOS Safari play the call's audio once it arrives.
    try { r.startAudio().catch(function () {}); } catch (e) {}

    r.connect(link.url, link.watch, { autoSubscribe: true }).then(function () {
      if (room !== r) return;
      S.phase = "live";
      S.canPlayAudio = r.canPlaybackAudio;
      scanParticipants();
      clearTimeout(agentTimer);
      agentTimer = setTimeout(function () { S.agentLate = true; render(); }, AGENT_LATE_MS);
      render();
    }, function (err) {
      if (room !== r) return;
      room = null;
      var f = friendlyConnectError(err);
      S.phase = f[0]; S.errorText = f[1];
      try { r.disconnect(); } catch (e) {}
      render();
    });
  }

  // ---------------------------------------------------------------- 7. talking (side room)
  async function startTalk() {
    var lk = LK();
    if (!lk || !link.talk || S.talk !== "off" || S.phase !== "live") return;
    S.talk = "connecting"; S.hint = ""; render();
    var audioOpts = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
    var got = [], noCamera = false, r = null;
    try {
      // Ask for the mic and camera first, inside the tap, so iOS shows its prompt.
      try {
        got = await lk.createLocalTracks({ audio: audioOpts, video: { facingMode: "user", resolution: lk.VideoPresets.h540.resolution } });
      } catch (e) {
        if (e && e.name === "NotAllowedError") throw e;
        got = await lk.createLocalTracks({ audio: audioOpts, video: false });
        noCamera = true;
      }
      localTracks = got;
      r = new lk.Room({ adaptiveStream: false, dynacast: false, disconnectOnPageLeave: true });
      talkRoom = r;
      r.on(lk.RoomEvent.Disconnected, function () {
        if (talkRoom !== r) return;
        stopTalk(true);
        if (S.phase === "live") S.hint = "Talking stopped. Tap Talk to try again.";
        render();
      });
      await r.connect(link.url, link.talk, { autoSubscribe: false });
      for (var i = 0; i < got.length; i++) {
        var t = got[i];
        await r.localParticipant.publishTrack(t, t.kind === "audio"
          ? { source: lk.Track.Source.Microphone, dtx: true, red: true }
          : { source: lk.Track.Source.Camera, simulcast: false });
        if (t.kind === "video") { t.attach($("selfVideo")); $("selfVideo").hidden = false; }
      }
      if (talkRoom !== r) return;
      S.talk = "on"; S.muted = false;
      if (noCamera) S.hint = "Your camera isn't available, so they'll hear you without video.";
    } catch (err) {
      stopTalk(true);
      var blocked = err && (err.name === "NotAllowedError" || err.name === "SecurityError");
      S.hint = blocked ? "Your microphone is blocked. Allow it for this page in your browser settings, then tap Talk again."
                       : "Couldn't start talking. Tap Talk to try again.";
    }
    render();
  }

  function stopTalk(quiet) {
    var r = talkRoom;
    talkRoom = null;
    localTracks.forEach(function (t) { try { t.detach(); t.stop(); } catch (e) {} });
    localTracks = [];
    $("selfVideo").hidden = true;
    if (r) { try { r.disconnect(); } catch (e) {} }
    S.talk = "off"; S.muted = false;
    if (!quiet) S.hint = "";
  }

  async function toggleMute() {
    var mic = localTracks.filter(function (t) { return t.kind === "audio"; })[0];
    if (!mic) return;
    try {
      if (S.muted) await mic.unmute(); else await mic.mute();
      S.muted = !S.muted;
    } catch (e) {}
    render();
  }

  // ---------------------------------------------------------------- 8. wire up
  $("startBtn").addEventListener("click", start);
  $("soundBtn").addEventListener("click", function () {
    if (room) room.startAudio().then(function () { S.canPlayAudio = room ? room.canPlaybackAudio : true; render(); }, function () {});
  });
  $("talkBtn").addEventListener("click", startTalk);
  $("muteBtn").addEventListener("click", toggleMute);
  $("leaveBtn").addEventListener("click", function () { stopTalk(false); render(); });

  // The link can run out while the page sits unopened.
  setInterval(function () {
    if (link.ok && S.phase === "idle" && Date.now() / 1000 >= link.expires) {
      S.phase = "expired";
      try { sessionStorage.removeItem(STORE_KEY); } catch (e) {}
      render();
    }
  }, 5000);

  render();
})();
