#!/usr/bin/env python3
"""MCP stdio server exposing the running Path of Building as tools.

External MCP clients (Claude Desktop, Cherry Studio, DSH, etc.) launch this
process and speak JSON-RPC over stdin/stdout.  The server itself is model
agnostic: the client's model decides what to call, this process only executes
PoB operations through Modules/AgentBridge.lua.

Usage:
    python mcp_server.py

Client config (example):
    {
      "mcpServers": {
        "pob": {
          "command": "python",
          "args": ["<skills>/pob-bd-builder/mcp/mcp_server.py"]
        }
      }
    }

Keep stdout clean: it is the MCP transport.  All diagnostics go to stderr.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from agent import Agent, TOOLS
from bridge import Bridge, BridgeError
from pob_wake import start_keepalive, wake_pob

SERVER_NAME = "pob-agent"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL = "2024-11-05"
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}

INSTRUCTIONS = """You are controlling a live Path of Building (PoB) instance for Path of Exile 1.
Call pob_get_build_state first. Use English canonical names for passives/gems/uniques,
and always search (pob_search_passives / pob_search_gems / pob_search_uniques) before
adding or allocating. IDs must come from search results; never invent them.

Co-pilot rules: pob_new_build closes the current build (unsaved changes are lost), so
confirm with the user before calling it. Explain what you changed and verify with
pob_get_stats. All numbers come from PoB; do not invent DPS/EHP values.

Tools return JSON in the text content. If a tool returns ok=false, read the error and
adjust rather than repeating the same call."""


def public_name(name: str) -> str:
    return name if name.startswith("pob_") else f"pob_{name}"


def internal_name(name: str) -> str:
    return name[4:] if name.startswith("pob_") else name


def mcp_tools() -> list[dict]:
    result = []
    for tool in TOOLS:
        fn = tool["function"]
        result.append(
            {
                "name": public_name(fn["name"]),
                "description": fn["description"],
                "inputSchema": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return result


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def error_response(request_id, code: int, message: str, data=None) -> dict:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def handle(request: dict, agent: Agent) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        protocol = requested if requested in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": mcp_tools()}}

    if method == "tools/call":
        name = internal_name(str(params.get("name") or ""))
        arguments = params.get("arguments") or {}
        if not name:
            return error_response(request_id, -32602, "Missing tool name")
        try:
            # no-op on non-Windows; the cursor nudge wakes a paused GLFW wait loop
            wake_pob(force=True)
            time.sleep(0.08)
            result = agent.run_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001 - never kill the MCP loop
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        ok = bool(result.get("ok"))
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": json_text(result)}],
                "isError": not ok,
            },
        }

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"resources": []}}

    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"prompts": []}}

    if method == "logging/setLevel":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if request_id is None:
        # Unknown notification: ignore per JSON-RPC.
        return None
    return error_response(request_id, -32601, f"Method not found: {method}")


def write_message(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def main() -> None:
    # MCP stdio is UTF-8 regardless of the Windows console code page.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    bridge_url = os.environ.get("POB_BRIDGE_URL", "http://127.0.0.1:49090")
    bridge = Bridge(bridge_url)
    exports = os.environ.get("POB_AGENT_EXPORTS")
    agent = Agent(bridge, llm=None, exports_dir=Path(exports) if exports else None)
    if os.environ.get("POB_KEEPALIVE", "1") != "0":
        start_keepalive(1.0)
        print("[pob-mcp] window keepalive enabled", file=sys.stderr, flush=True)
    print(f"[pob-mcp] bridge={bridge_url}", file=sys.stderr, flush=True)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            write_message(error_response(None, -32700, f"Parse error: {exc}"))
            continue
        if isinstance(request, list):
            # JSON-RPC batch is not required by MCP clients we target; reject clearly.
            write_message(error_response(None, -32600, "Batch requests are not supported"))
            continue
        try:
            response = handle(request, agent)
        except Exception as exc:  # noqa: BLE001
            response = error_response(request.get("id"), -32603, f"Internal error: {exc}")
        if response is not None:
            write_message(response)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except BridgeError as exc:
        print(f"[pob-mcp] bridge error: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
