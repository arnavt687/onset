"""How the agent's LiveKit worker is sized: one Chert line, one call at a time, on a laptop.

LiveKit's AgentServer picks its defaults from the run mode (issue #28):

    |                     | `agent.py dev` | `agent.py start` |
    |---------------------|----------------|------------------|
    | num_idle_processes  | 0              | up to 12         |
    | load_threshold      | inf            | 0.7              |
    | health port         | random         | 8081             |

`start` is sized for a server farm. On an 8-core laptop it spawned 8 idle processes, each loading
MediaPipe; the CPU load they caused pushed the worker past 0.7, it marked itself "at full capacity"
and never accepted the job Chert's waiting room sent it, so `chert dev` reported agent_not_warm
and every call was declined. A worker that serves one line has nobody to hand work to, so
refusing a job is never the right answer.

These settings are the same in both modes:

    ONSET_IDLE_PROCESSES   processes kept loaded and ready for the next call (default 1)
    ONSET_LOAD_THRESHOLD   CPU load at which the worker refuses new calls (default inf: never)
    ONSET_HEALTH_PORT      the worker's health-check port (default 0: any free port)
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping

DEFAULT_IDLE_PROCESSES = 1
MAX_IDLE_PROCESSES = 4


def server_options(env: Mapping[str, str] | None = None) -> dict:
    """Keyword arguments for livekit.agents.AgentServer."""
    env = os.environ if env is None else env

    def text(name: str) -> str:
        return str(env.get(name) or "").strip()

    try:
        idle = int(text("ONSET_IDLE_PROCESSES") or DEFAULT_IDLE_PROCESSES)
    except ValueError:
        idle = DEFAULT_IDLE_PROCESSES
    idle = min(max(idle, 0), MAX_IDLE_PROCESSES)

    raw = text("ONSET_LOAD_THRESHOLD").lower()
    try:
        threshold = math.inf if raw in ("", "inf", "off", "none") else float(raw)
    except ValueError:
        threshold = math.inf
    if not threshold > 0:  # 0, negative or nan would refuse every call
        threshold = math.inf

    try:
        port = int(text("ONSET_HEALTH_PORT") or 0)
    except ValueError:
        port = 0
    return {"num_idle_processes": idle, "load_threshold": threshold, "port": max(0, port)}


def describe(opts: dict) -> str:
    t = opts["load_threshold"]
    return (f"{opts['num_idle_processes']} idle process(es), "
            f"{'never refuses a call' if math.isinf(t) else f'refuses calls above load {t:g}'}, "
            f"health port {opts['port'] or 'any free'}")
