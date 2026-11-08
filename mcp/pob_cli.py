#!/usr/bin/env python3
"""CLI MCP client for the PoB agent.

Lets a chat AI that can run commands (DSH, Codex, Claude Code, etc.) use the PoB
MCP server without installing an MCP-capable GUI client.  Each invocation starts
`mcp_server.py`, performs the MCP handshake, calls one tool, prints JSON, exits.

Examples:
    python pob_cli.py state
    python pob_cli.py search-passives Fire --limit 5
    python pob_cli.py alloc 14040,60501
    python pob_cli.py skill Fireball "Controlled Destruction" "Fire Penetration"
    python pob_cli.py stats TotalDPS Life TotalEHP
    python pob_cli.py call add_unique --args '{"name":"Tabula Rasa"}'
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_SERVER = Path(__file__).resolve().with_name("mcp_server.py")
DEFAULT_TIMEOUT = 90.0


class MCPError(RuntimeError):
    pass


class MCPClient:
    def __init__(self, server_path: Path, bridge_url: str = "http://127.0.0.1:49090", timeout: float = DEFAULT_TIMEOUT):
        self.timeout = timeout
        env = os.environ.copy()
        env["POB_BRIDGE_URL"] = bridge_url
        env["PYTHONUTF8"] = "1"
        # Because this process is short-lived there is no need for the keepalive
        # thread; wake_pob() is still called before every tools/call.
        env.setdefault("POB_KEEPALIVE", "0")
        self.proc = subprocess.Popen(
            [sys.executable, str(server_path)],
            cwd=str(server_path.parent),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stderr: list[str] = []
        self._next_id = 0
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._queue.put(line)
        self._queue.put(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._stderr.append(line.rstrip())

    def _send(self, message: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _receive(self, timeout: float) -> dict:
        try:
            line = self._queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise MCPError(f"MCP server timed out after {timeout:.0f}s") from exc
        if line is None:
            detail = "\n".join(self._stderr[-10:])
            raise MCPError(f"MCP server exited unexpectedly.\n{detail}")
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise MCPError(f"MCP server wrote invalid JSON: {line[:300]}") from exc

    def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> Any:
        self._next_id += 1
        request_id = self._next_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        deadline = timeout or self.timeout
        while True:
            response = self._receive(deadline)
            if response.get("id") != request_id:
                continue  # server notification / log line
            if "error" in response:
                error = response["error"]
                raise MCPError(f"{error.get('message', 'MCP error')} ({error.get('code')})")
            return response.get("result")

    def notify(self, method: str, params: dict | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def initialize(self) -> dict:
        result = self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pob-cli", "version": "0.1.0"},
            },
        )
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict]:
        result = self.request("tools/list", {})
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        result = self.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        content = result.get("content") or []
        text = content[0].get("text", "{}") if content else "{}"
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"ok": not result.get("isError", False), "raw": text}
        if result.get("isError") and "ok" not in payload:
            payload["ok"] = False
        return payload

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def parse_value(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def flatten_ids(values: list[str]) -> list[int]:
    ids: list[int] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                ids.append(int(part))
    return ids


def print_payload(payload: dict, compact: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=None if compact else 2))
    if not payload.get("ok", False):
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Call the PoB MCP server from a shell/chat AI.")
    parser.add_argument("--server", default=str(DEFAULT_SERVER), help="path to mcp_server.py")
    parser.add_argument("--bridge", default=os.environ.get("POB_BRIDGE_URL", "http://127.0.0.1:49090"))
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--compact", action="store_true", help="single-line JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("state", help="pob_get_build_state")
    p = sub.add_parser("new", help="pob_new_build (destroys current unsaved build)")
    p.add_argument("name")
    p = sub.add_parser("import-ninja", help="pob_import_ninja (replaces current build)")
    p.add_argument("url", nargs="?")
    p.add_argument("--url-env", action="store_true", help="read URL from POB_NINJA_URL")
    p.add_argument("--url-file", help="read URL from a UTF-8 file")
    p.add_argument("--name")
    p = sub.add_parser("import-code", help="pob_import_pob_code (replaces current build)")
    p.add_argument("--code")
    p.add_argument("--file")
    p.add_argument("--name")
    p = sub.add_parser("class", help="pob_select_class")
    p.add_argument("class_name")
    p.add_argument("ascendancy", nargs="?")
    p = sub.add_parser("level", help="pob_set_level")
    p.add_argument("level", type=int)
    p = sub.add_parser("search-passives", help="pob_search_passives")
    p.add_argument("query")
    p.add_argument("--type", choices=["Normal", "Notable", "Keystone", "Mastery", "Socket"])
    p.add_argument("--ascendancy")
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("alloc", help="pob_allocate_passives")
    p.add_argument("ids", nargs="+", help="node ids, comma separated")
    p = sub.add_parser("dealloc", help="pob_deallocate_passive")
    p.add_argument("id", type=int)
    sub.add_parser("allocated", help="pob_list_allocated_passives")
    p = sub.add_parser("search-gems", help="pob_search_gems")
    p.add_argument("query")
    p.add_argument("--support", action="store_true")
    p.add_argument("--no-support", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("skill", help="pob_add_skill_group")
    p.add_argument("gems", nargs="+", help="gem names; first is the main skill")
    p.add_argument("--main", type=int, default=1)
    p.add_argument("--full-dps", action="store_true")
    p.add_argument("--level", type=int, default=20)
    p.add_argument("--quality", type=int, default=0)
    p = sub.add_parser("search-unique", help="pob_search_uniques")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("unique", help="pob_add_unique")
    p.add_argument("name")
    p.add_argument("--slot")
    p.add_argument("--no-equip", action="store_true")
    p = sub.add_parser("item", help="pob_add_item_raw (English item text)")
    p.add_argument("--raw")
    p.add_argument("--file")
    p.add_argument("--slot")
    p.add_argument("--no-equip", action="store_true")
    sub.add_parser("items", help="pob_list_items")
    sub.add_parser("skills", help="pob_list_skills")
    p = sub.add_parser("config", help="pob_set_config")
    p.add_argument("key")
    p.add_argument("value")
    p = sub.add_parser("stats", help="pob_get_stats")
    p.add_argument("keys", nargs="*")
    sub.add_parser("export", help="pob_export_build")
    p = sub.add_parser("save", help="pob_save_build")
    p.add_argument("name")
    sub.add_parser("tools", help="list MCP tools")
    p = sub.add_parser("call", help="raw tools/call")
    p.add_argument("tool")
    p.add_argument("--args", default="{}", help="JSON object")
    p.add_argument("--args-file", help="read JSON object from a UTF-8 file (best for arrays)")
    p.add_argument("--args-env", action="store_true", help="read JSON object from POB_ARGS")
    p.add_argument("--arg", action="append", default=[])
    return parser


def dispatch(args: argparse.Namespace, client: MCPClient) -> dict:
    command = args.command
    if command == "tools":
        return {"ok": True, "tools": client.list_tools()}
    if command == "state":
        return client.call_tool("pob_get_build_state")
    if command == "new":
        return client.call_tool("pob_new_build", {"name": args.name})
    if command == "import-ninja":
        url = args.url
        if args.url_env:
            url = os.environ.get("POB_NINJA_URL", "")
        elif args.url_file:
            url = Path(args.url_file).read_text(encoding="utf-8-sig").strip()
        if not url:
            raise MCPError("import-ninja requires a URL (or --url-env / --url-file)")
        payload = {"url": url}
        if args.name:
            payload["name"] = args.name
        return client.call_tool("pob_import_ninja", payload)
    if command == "import-code":
        code = args.code
        if args.file:
            code = Path(args.file).read_text(encoding="utf-8-sig").strip()
        if not code:
            raise MCPError("import-code requires --code or --file")
        payload = {"code": code}
        if args.name:
            payload["name"] = args.name
        return client.call_tool("pob_import_pob_code", payload)
    if command == "class":
        payload = {"class": args.class_name}
        if args.ascendancy:
            payload["ascendancy"] = args.ascendancy
        return client.call_tool("pob_select_class", payload)
    if command == "level":
        return client.call_tool("pob_set_level", {"level": args.level})
    if command == "search-passives":
        payload = {"query": args.query, "limit": args.limit}
        if args.type:
            payload["type"] = args.type
        if args.ascendancy:
            payload["ascendancy"] = args.ascendancy
        return client.call_tool("pob_search_passives", payload)
    if command == "alloc":
        return client.call_tool("pob_allocate_passives", {"ids": flatten_ids(args.ids)})
    if command == "dealloc":
        return client.call_tool("pob_deallocate_passive", {"id": args.id})
    if command == "allocated":
        return client.call_tool("pob_list_allocated_passives")
    if command == "search-gems":
        support = None
        if args.support:
            support = True
        elif args.no_support:
            support = False
        payload = {"query": args.query, "limit": args.limit}
        if support is not None:
            payload["support"] = support
        return client.call_tool("pob_search_gems", payload)
    if command == "skill":
        gems = [{"name": name, "level": args.level, "quality": args.quality} for name in args.gems]
        return client.call_tool(
            "pob_add_skill_group",
            {"gems": gems, "main_active_skill": args.main, "include_in_full_dps": args.full_dps},
        )
    if command == "search-unique":
        return client.call_tool("pob_search_uniques", {"query": args.query, "limit": args.limit})
    if command == "unique":
        payload = {"name": args.name, "equip": not args.no_equip}
        if args.slot:
            payload["slot"] = args.slot
        return client.call_tool("pob_add_unique", payload)
    if command == "item":
        raw = args.raw
        if args.file:
            raw = Path(args.file).read_text(encoding="utf-8")
        if not raw:
            raise MCPError("item requires --raw or --file")
        payload = {"raw": raw, "equip": not args.no_equip}
        if args.slot:
            payload["slot"] = args.slot
        return client.call_tool("pob_add_item_raw", payload)
    if command == "items":
        return client.call_tool("pob_list_items")
    if command == "skills":
        return client.call_tool("pob_list_skills")
    if command == "config":
        return client.call_tool("pob_set_config", {"key": args.key, "value": parse_value(args.value)})
    if command == "stats":
        keys: list[str] = []
        for value in args.keys:
            keys.extend(part.strip() for part in value.split(",") if part.strip())
        payload = {"keys": keys} if keys else {}
        return client.call_tool("pob_get_stats", payload)
    if command == "export":
        return client.call_tool("pob_export_build")
    if command == "save":
        return client.call_tool("pob_save_build", {"name": args.name})
    if command == "call":
        if args.args_file:
            raw = Path(args.args_file).read_text(encoding="utf-8-sig")
        elif args.args_env:
            raw = os.environ.get("POB_ARGS", "")
            if not raw:
                raise MCPError("POB_ARGS is not set")
        else:
            raw = args.args
        try:
            payload: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MCPError(f"arguments are not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise MCPError("arguments JSON must be an object")
        for item in args.arg:
            if "=" not in item:
                raise MCPError(f"--arg must be key=value, got: {item}")
            key, value = item.split("=", 1)
            payload[key] = parse_value(value)
        return client.call_tool(args.tool, payload)
    raise MCPError(f"Unknown command: {command}")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    parser = build_parser()
    args = parser.parse_args()
    client = MCPClient(Path(args.server), bridge_url=args.bridge, timeout=args.timeout)
    try:
        client.initialize()
        payload = dispatch(args, client)
        if not payload.get("ok") and payload.get("code") == "bridge_unreachable":
            # PoB may have been paused in the background; the MCP layer nudged
            # the cursor, so one short retry is usually enough.
            time.sleep(0.5)
            payload = dispatch(args, client)
        print_payload(payload, args.compact)
    except MCPError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=None if args.compact else 2))
        raise SystemExit(2)
    finally:
        client.close()


if __name__ == "__main__":
    main()
