"""Import a poe.ninja character build into PoB via its undocumented API.

poe.ninja build pages are JS apps; the raw PoB code is exposed by:
    GET https://poe.ninja/poe1/api/builds/<snapshotVersion>/character
        ?account=<account>&name=<character>&overview=<league>&timeMachine=
    -> {"pathOfBuildingExport": "<base64 zlib PoB code>", ...}

The snapshot version comes from /poe1/api/data/index-state (per league).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

BASE = "https://poe.ninja"
UA = "pob-agent/0.1 (local PoB assistant)"
INDEX_URL = BASE + "/poe1/api/data/index-state"

_BUILD_URL = re.compile(
    r"poe\.ninja/poe1/builds/(?P<league>[^/]+)/character/(?P<account>[^/?#]+)/(?P<character>[^/?#]+)",
    re.IGNORECASE,
)
_POB_URL = re.compile(r"poe\.ninja/poe1/pob/(?P<id>[A-Za-z0-9_-]+)", re.IGNORECASE)


class NinjaError(RuntimeError):
    pass


@dataclass
class NinjaBuild:
    account: str
    character: str
    league: str
    snapshot: str
    code: str
    level: int | None = None
    class_name: str | None = None
    ascendancy: str | None = None

    def summary(self) -> dict:
        return {
            "account": self.account,
            "character": self.character,
            "league": self.league,
            "snapshot": self.snapshot,
            "level": self.level,
            "class": self.class_name,
            "ascendancy": self.ascendancy,
            "codeLength": len(self.code),
        }


def _get(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise NinjaError(f"poe.ninja HTTP {exc.code}: {body[:300]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NinjaError(f"无法连接 poe.ninja: {exc}") from exc


def _get_json(url: str) -> dict:
    raw = _get(url)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise NinjaError(f"poe.ninja 返回了非 JSON 内容: {raw[:200]!r}") from exc


def _snapshot_for_league(league: str) -> str:
    index = _get_json(INDEX_URL)
    candidates = [
        sv
        for sv in index.get("snapshotVersions", [])
        if league in (sv.get("url"), sv.get("snapshotName"), sv.get("name"))
    ]
    if not candidates:
        raise NinjaError(f"poe.ninja 找不到联赛快照: {league}")
    # Normal league snapshot (type "exp") is the one build pages use.
    candidates.sort(key=lambda sv: 0 if sv.get("type") == "exp" else 1)
    version = candidates[0].get("version")
    if not version:
        raise NinjaError(f"联赛 {league} 没有 snapshotVersion")
    return str(version)


def parse_url(url: str) -> tuple[str, str, str]:
    m = _BUILD_URL.search(url)
    if not m:
        raise NinjaError(
            "无法识别链接。支持：https://poe.ninja/poe1/builds/<league>/character/<account>/<character>"
        )
    return (
        urllib.parse.unquote(m.group("league")),
        urllib.parse.unquote(m.group("account")),
        urllib.parse.unquote(m.group("character")),
    )


def fetch_build(url: str) -> NinjaBuild:
    # Direct pob link: https://poe.ninja/poe1/pob/<id>
    m = _POB_URL.search(url)
    if m:
        build_id = m.group("id")
        code = _get(f"{BASE}/poe1/pob/raw/{build_id}").decode("utf-8", "replace").strip()
        if not code:
            raise NinjaError("poe.ninja pob/raw 返回空内容")
        return NinjaBuild(account="", character=build_id, league="", snapshot="pob", code=code)

    league, account, character = parse_url(url)
    snapshot = _snapshot_for_league(league)
    query = urllib.parse.urlencode(
        {"account": account, "name": character, "overview": league, "timeMachine": ""}
    )
    endpoint = f"{BASE}/poe1/api/builds/{snapshot}/character?{query}"
    data = _get_json(endpoint)
    code = data.get("pathOfBuildingExport")
    if not code:
        raise NinjaError(
            f"poe.ninja 没有返回 {character} 的 PoB 代码（可能该角色未被索引/不是当前天梯）"
        )
    return NinjaBuild(
        account=account,
        character=character,
        league=league,
        snapshot=snapshot,
        code=code,
        level=data.get("level"),
        class_name=data.get("class") or data.get("baseClass"),
        ascendancy=data.get("ascendancyClassName"),
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Fetch a poe.ninja build's PoB export code.")
    parser.add_argument("url")
    parser.add_argument("--out", help="write raw PoB code to this file")
    args = parser.parse_args()
    build = fetch_build(args.url)
    print(json.dumps(build.summary(), ensure_ascii=False, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(build.code)
        print(f"code written to {args.out}")


if __name__ == "__main__":
    main()
