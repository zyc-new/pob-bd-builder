"""Minimal OpenAI-compatible chat client (stdlib only).

Works with DeepSeek, OpenAI, Moonshot, Ollama (/v1), LM Studio, etc.  We keep the
request/response shape deliberately narrow: messages + tools in, one assistant
message out.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class LLMError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


@dataclass
class LLMConfig:
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.3
    max_tokens: int = 4096
    timeout: float = 180.0
    native_tools: bool = True

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    @property
    def configured(self) -> bool:
        if self.api_key.strip():
            return True
        # Local servers usually accept any/no key.
        return any(host in self.base_url for host in ("127.0.0.1", "localhost", "0.0.0.0"))


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools and self.config.native_tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.api_key.strip():
            headers["Authorization"] = f"Bearer {self.config.api_key.strip()}"

        req = urllib.request.Request(self.config.endpoint, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise LLMError(f"模型接口返回 HTTP {exc.code}：{body[:800]}", status=exc.code, detail=body) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"无法连接模型接口：{exc}", detail=str(exc)) from exc

        try:
            parsed = json.loads(raw)
            message = parsed["choices"][0]["message"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"模型接口返回了无法解析的内容：{raw[:800]}", detail=raw) from exc
        return message
