"""Smoke test for the MCP stdio server.

Run while PoB + AgentBridge are up:
    python test_mcp.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def send(proc: subprocess.Popen, payload: dict) -> dict:
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("MCP server closed stdout")
    return json.loads(line)


def main() -> None:
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "mcp_server.py")],
        cwd=str(ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        init = send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke-test", "version": "1.0"},
                },
            },
        )
        assert init["result"]["serverInfo"]["name"] == "pob-agent", init
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        listing = send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [tool["name"] for tool in listing["result"]["tools"]]
        assert "pob_get_build_state" in names, names
        assert "pob_allocate_passives" in names, names

        state = send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "pob_get_build_state", "arguments": {}},
            },
        )
        payload = json.loads(state["result"]["content"][0]["text"])
        assert payload["ok"], payload
        print("state:", payload["data"].get("className"), payload["data"].get("level"))

        search = send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "pob_search_passives", "arguments": {"query": "Fire", "limit": 3}},
            },
        )
        found = json.loads(search["result"]["content"][0]["text"])
        assert found["ok"], found
        print("search nodes:", [n["name"] for n in found["data"]["nodes"]])

        level = send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "pob_set_level", "arguments": {"level": 90}},
            },
        )
        level_payload = json.loads(level["result"]["content"][0]["text"])
        assert level_payload["ok"], level_payload
        assert level_payload["data"]["level"] == 90, level_payload
        print("OK: MCP stdio handshake + read/write tools work")
    finally:
        proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        err = proc.stderr.read()
        if err:
            print("stderr:", err)


if __name__ == "__main__":
    main()
