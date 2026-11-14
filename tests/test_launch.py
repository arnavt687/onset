"""Issue #28: what tools/demo.sh needs to start a demo on a laptop.

- the worker is sized for one line, in `start` and `dev` alike (onset/worker.py)
- tools/chert.sh dev passes --agent and --env-file, so the CLI finds Onset
"""

import math
import shutil
import subprocess
from pathlib import Path

from onset import worker

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- worker sizing


def test_worker_defaults_never_refuse_a_call():
    opts = worker.server_options({})
    assert opts == {"num_idle_processes": 1, "load_threshold": math.inf, "port": 0}
    assert "never refuses" in worker.describe(opts)


def test_worker_settings_from_env():
    opts = worker.server_options({"ONSET_IDLE_PROCESSES": "2", "ONSET_LOAD_THRESHOLD": "0.95",
                                  "ONSET_HEALTH_PORT": "8091"})
    assert opts == {"num_idle_processes": 2, "load_threshold": 0.95, "port": 8091}
    assert "0.95" in worker.describe(opts)


def test_worker_settings_that_would_starve_it_are_ignored():
    for bad in ("0", "-1", "nan", "lots", "off", "inf", " "):
        assert worker.server_options({"ONSET_LOAD_THRESHOLD": bad})["load_threshold"] == math.inf, bad
    assert worker.server_options({"ONSET_IDLE_PROCESSES": "12"})["num_idle_processes"] == worker.MAX_IDLE_PROCESSES
    assert worker.server_options({"ONSET_IDLE_PROCESSES": "-3"})["num_idle_processes"] == 0
    assert worker.server_options({"ONSET_IDLE_PROCESSES": "many"})["num_idle_processes"] == 1
    assert worker.server_options({"ONSET_HEALTH_PORT": "x"})["port"] == 0


def test_options_are_accepted_by_agent_server():
    from livekit.agents import AgentServer

    AgentServer(**worker.server_options({}))  # raises on an unknown keyword


# ---------------------------------------------------------------- tools/chert.sh dev


def chert(tmp_path: Path, env_local: str | None, *args: str, env: dict | None = None) -> str:
    """Run a copy of tools/chert.sh in tmp_path (its own .env.local) with CHERT_DRY_RUN=1."""
    (tmp_path / "tools").mkdir(exist_ok=True)
    shutil.copy(ROOT / "tools" / "chert.sh", tmp_path / "tools" / "chert.sh")
    if env_local is not None:
        (tmp_path / ".env.local").write_text(env_local)
    full = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CHERT_DRY_RUN": "1", **(env or {})}
    out = subprocess.run(["bash", str(tmp_path / "tools" / "chert.sh"), *args], env=full,
                         capture_output=True, text=True, timeout=20, check=True)
    return out.stdout.strip()


def test_chert_dev_adds_the_agent_and_the_env_file(tmp_path):
    out = chert(tmp_path, "LIVEKIT_URL=wss://x\nAGENT_NAME=onset-ab\n", "dev", "--json")
    assert out.endswith("dev --json --agent onset-ab --env-file .env.local"), out


def test_chert_dev_reads_env_local_like_dotenv(tmp_path):
    for line, want in (('AGENT_NAME="onset-q"', "onset-q"), ("AGENT_NAME='onset-s'", "onset-s"),
                       ("export AGENT_NAME=onset-e", "onset-e"), ("AGENT_NAME=onset-c   # mine", "onset-c"),
                       ("AGENT_NAME = onset-sp\r", "onset-sp")):
        out = chert(tmp_path, f"AGENT_NAME=old\n{line}\n", "dev")
        assert f"--agent {want} " in out + " ", (line, out)


def test_chert_dev_environment_wins_and_onset_is_the_default(tmp_path):
    assert "--agent from-env " in chert(tmp_path, "AGENT_NAME=from-file\n", "dev", env={"AGENT_NAME": "from-env"}) + " "
    out = chert(tmp_path, "LIVEKIT_URL=wss://x\n", "dev")
    assert "--agent onset --env-file .env.local" in out


def test_chert_dev_keeps_flags_you_pass(tmp_path):
    out = chert(tmp_path, "AGENT_NAME=onset-ab\n", "dev", "--agent", "mine", "--env-file=other.env")
    assert out.endswith("dev --agent mine --env-file=other.env"), out


def test_chert_dev_without_env_local_and_other_commands(tmp_path):
    assert chert(tmp_path, None, "dev").endswith("dev --agent onset")
    assert chert(tmp_path, "AGENT_NAME=onset-ab\n", "doctor", "--json").endswith("doctor --json")
    assert "@trychert/facetime@1.2.3 " in chert(tmp_path, "CHERT_CLI_VERSION=1.2.3\n", "whoami")


def test_demo_sh_parses():
    subprocess.run(["bash", "-n", str(ROOT / "tools" / "demo.sh")], check=True)
    subprocess.run(["bash", "-n", str(ROOT / "tools" / "stop.sh")], check=True)
    help_text = subprocess.run(["bash", str(ROOT / "tools" / "demo.sh"), "--help"], capture_output=True, text=True,
                               timeout=20).stdout
    assert "--force" in help_text and "--dev" in help_text
