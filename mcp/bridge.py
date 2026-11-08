"""HTTP client for the in-PoB AgentBridge (Modules/AgentBridge.lua).

The bridge is intentionally dumb: it only exposes data operations.  This module
keeps retries/timeouts out of the agent loop and gives it a small typed surface.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


class BridgeError(RuntimeError):
    """Raised when the PoB bridge is unreachable or returns ok=false."""

    def __init__(self, message: str, *, code: str = "bridge_error", detail: Any = None):
        super().__init__(message)
        self.code = code
        self.detail = detail


class Bridge:
    def __init__(self, base_url: str = "http://127.0.0.1:49090", timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            raise BridgeError(f"PoB bridge HTTP {exc.code}: {raw[:300]}", code="bridge_http", detail=raw) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BridgeError(
                f"无法连接 PoB 桥接服务（{self.base_url}）。请确认 PoB 已启动并加载 AgentBridge。",
                code="bridge_unreachable",
                detail=str(exc),
            ) from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BridgeError(f"PoB bridge returned invalid JSON: {raw[:300]}", code="bridge_json") from exc

    def ping(self) -> dict:
        return self._request("GET", "/ping")

    def state(self) -> dict:
        result = self._request("GET", "/state")
        if not result.get("ok"):
            raise BridgeError(result.get("error", "PoB bridge state failed"), code="no_build")
        return result["data"]

    def rpc(self, cmd: str, **params: Any) -> Any:
        result = self._request("POST", "/rpc", {"cmd": cmd, **params})
        if not result.get("ok"):
            raise BridgeError(result.get("error", f"{cmd} failed"), code="command_failed", detail=result)
        return result.get("data")

    def wait_ready(self, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        last: Exception | None = None
        while time.time() < deadline:
            try:
                self.ping()
                return True
            except BridgeError as exc:
                last = exc
                time.sleep(0.5)
        if last:
            raise last
        return False
