"""Summarize a LiveKit agent JSON log: everything except routine Onset INFO lines and per-second fps lines."""

import json
import sys

for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line.startswith("{"):
        if line:
            print("RAW", line[:300])
        continue
    try:
        r = json.loads(line)
    except ValueError:
        continue
    msg, lvl = r.get("message", ""), r.get("level", "")
    if msg.startswith("[onset] caller video") or msg == "plugin registered":
        continue
    if "[onset]" in msg and lvl == "INFO" and "--all" not in sys.argv:
        continue
    ts = r.get("timestamp", "")[11:23]
    extra = {k: v for k, v in r.items() if k in ("exc_info", "error", "reason", "status_code", "body")}
    print(ts, lvl[:4], r.get("job_id", "")[-6:], r.get("name", "")[:30], "|",
          msg[:1500].replace("\n", " / "), json.dumps(extra)[:1500] if extra else "")
