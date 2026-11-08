#!/usr/bin/env python3
"""Run pob_cli.py using the discovered runtime paths.

This keeps the skill portable: pob.cmd calls this script, which reads
runtime.json (written by discover_env.py) instead of assuming a fixed install path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(
    os.environ.get("POB_AGENT_DATA")
    or (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "pob-bd-builder")
)
RUNTIME = DATA_ROOT / "runtime.json"
DISCOVER = ROOT / "discover_env.py"


def load_runtime() -> dict | None:
    if RUNTIME.exists():
        try:
            data = json.loads(RUNTIME.read_text(encoding="utf-8"))
            if data.get("ok") and data.get("pobCli") and Path(data["pobCli"]).exists():
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return None


def discover() -> dict:
    proc = subprocess.run(
        [sys.executable, str(DISCOVER), "--write", "--fix"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": proc.stderr[-800:] or proc.stdout[-800:]}


def main() -> None:
    runtime = load_runtime()
    if runtime is None:
        runtime = discover()
    if not runtime.get("ok"):
        print(json.dumps(runtime, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    python = runtime.get("python") or sys.executable
    agent_dir = Path(runtime["agentDir"])
    cli = Path(runtime["pobCli"])
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["POB_BRIDGE_URL"] = env.get("POB_BRIDGE_URL", "http://127.0.0.1:49090")
    proc = subprocess.run(
        [python, str(cli), *sys.argv[1:]],
        cwd=str(agent_dir),
        env=env,
    )
    raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()
