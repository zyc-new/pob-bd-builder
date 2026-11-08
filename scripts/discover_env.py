#!/usr/bin/env python3
"""Discover the local PoB / PoeCharm / zh-rCN / MCP runtime.

Run before starting PoB:
    python discover_env.py --write

Outputs JSON and writes runtime.json + runtime.env to a user data directory.
The skill then launches PoeCharm using the discovered paths instead of assuming
a fixed install location.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(
    os.environ.get("POB_AGENT_DATA")
    or (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "pob-bd-builder")
)
RUNTIME_JSON = DATA_ROOT / "runtime.json"
RUNTIME_ENV = DATA_ROOT / "runtime.env"

SKIP_DIRS = {
    "$recycle.bin", "windows", "winsxs", "system32", "syswow64", "program files",
    "program files (x86)", "programdata", "appdata", "node_modules", ".git",
    ".cache", "temp", "tmp", "packages", "resources",
}


def _roots() -> list[Path]:
    roots: list[Path] = []
    for key in ("POB_ROOT", "POB_AGENT_HOME"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value))
    roots.append(Path.cwd())
    roots.extend(list(Path.cwd().parents)[:3])
    roots.append(SKILL_ROOT)
    roots.extend(list(SKILL_ROOT.parents)[:3])
    home = Path.home()
    roots.extend([home, home / "Desktop", home / "Downloads", home / "Documents"])
    for drive in ("D:/", "C:/", "E:/"):
        p = Path(drive)
        if p.exists():
            roots.append(p)

    seen: set[str] = set()
    result: list[Path] = []
    for root in roots:
        try:
            key = str(root.resolve()).lower()
        except OSError:
            continue
        if key not in seen and root.exists():
            seen.add(key)
            result.append(root)
    return result


def _walk_dirs(root: Path, max_depth: int = 4):
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        try:
            depth = len(Path(dirpath).resolve().relative_to(root).parts)
        except ValueError:
            depth = max_depth
        if depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")]
        yield Path(dirpath), set(filenames)


def _find_exe(name: str, max_depth: int = 4) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for root in _roots():
        try:
            for dirpath, filenames in _walk_dirs(root, max_depth=max_depth):
                if name in filenames:
                    key = str(dirpath).lower()
                    if key not in seen:
                        seen.add(key)
                        found.append(dirpath)
        except OSError:
            continue
    return found


def _score_pob(directory: Path) -> int:
    score = 0
    if (directory / "Path of Building.exe").exists():
        score += 10
    if (directory / "Launch.lua").exists():
        score += 20
    if (directory / "Modules" / "AgentBridge.lua").exists():
        score += 10
    if (directory / "Modules" / "Main.lua").exists():
        score += 5
    if (directory / "manifest.xml").exists():
        score += 3
    if "pathofbuilding" in directory.name.lower():
        score += 5
    return score


def _score_charm(directory: Path) -> int:
    score = 0
    if (directory / "PoeCharm3.exe").exists():
        score += 20
    zh = directory / "Data" / "Translate" / "zh-rCN"
    if zh.is_dir():
        score += 15
        for name in ("Gems_data.csv", "passiveTree.csv", "statDescriptions.csv", "Items_Gems.txt.csv"):
            if (zh / name).exists():
                score += 3
    if (directory / "Data" / "Translate.json").exists():
        score += 5
    return score


def _translate_info(charm_dir: Path | None) -> dict:
    if not charm_dir:
        return {"ok": False, "files": []}
    zh = charm_dir / "Data" / "Translate" / "zh-rCN"
    files = []
    if zh.is_dir():
        for name in ("Gems_data.csv", "passiveTree.csv", "statDescriptions.csv", "Items_Gems.txt.csv", "GUI.csv"):
            if (zh / name).exists():
                files.append(str(zh / name))
    translate_json = charm_dir / "Data" / "Translate.json"
    json_ok = False
    if translate_json.exists():
        try:
            text = translate_json.read_text(encoding="utf-8", errors="ignore")
            json_ok = "zh-rCN" in text
        except OSError:
            pass
    settings = charm_dir / "Data" / "Settings.conf"
    language = None
    if settings.exists():
        try:
            for line in settings.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.lower().startswith("translatetl="):
                    language = line.split("=", 1)[1].strip()
        except OSError:
            pass
    return {
        "ok": zh.is_dir() and len(files) >= 3 and json_ok,
        "dir": str(zh) if zh.is_dir() else None,
        "files": files,
        "translateJson": str(translate_json) if translate_json.exists() else None,
        "translateJsonHasZhCN": json_ok,
        "settingsLanguage": language,
    }


def _find_agent_dir() -> Path | None:
    bundled = SKILL_ROOT / "mcp"
    if all((bundled / name).exists() for name in ("pob_cli.py", "mcp_server.py", "agent.py")):
        return bundled
    candidates = _find_exe("pob_cli.py", max_depth=5)
    for directory in candidates:
        if (directory / "mcp_server.py").exists() and (directory / "agent.py").exists():
            return directory
    return None


def _ensure_junction(link: Path, target: Path, fix: bool) -> dict:
    if link.exists():
        try:
            resolved = link.resolve()
        except OSError:
            resolved = link
        return {
            "ok": resolved == target.resolve(),
            "path": str(link),
            "target": str(resolved),
            "created": False,
        }
    if not fix:
        return {"ok": False, "path": str(link), "error": "missing link"}
    link.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "ok": proc.returncode == 0,
        "path": str(link),
        "target": str(target),
        "created": proc.returncode == 0,
        "error": None if proc.returncode == 0 else (proc.stdout + proc.stderr)[-400:],
    }


def discover(fix: bool = False) -> dict:
    pob_dirs = _find_exe("Path of Building.exe", max_depth=4)
    charm_dirs = _find_exe("PoeCharm3.exe", max_depth=4)
    agent_dir = _find_agent_dir()

    pob_ranked = sorted(((p, _score_pob(p)) for p in pob_dirs), key=lambda item: item[1], reverse=True)
    charm_ranked = sorted(((p, _score_charm(p)) for p in charm_dirs), key=lambda item: item[1], reverse=True)
    charm = charm_ranked[0][0] if charm_ranked else None
    pob = None
    link = charm / "PathOfBuildingCommunity-Portable" if charm else None
    if charm and link and link.exists():
        try:
            candidate = link.resolve()
            if (candidate / "Path of Building.exe").exists():
                pob = candidate
        except OSError:
            pass
    if pob is None and pob_ranked:
        pob = pob_ranked[0][0]

    link_info = None
    if charm and pob:
        link_info = _ensure_junction(charm / "PathOfBuildingCommunity-Portable", pob, fix)

    translate = _translate_info(charm)
    bridge = (pob / "Modules" / "AgentBridge.lua") if pob else None
    mcp_server = (agent_dir / "mcp_server.py") if agent_dir else None
    pob_cli = (agent_dir / "pob_cli.py") if agent_dir else None

    missing = []
    if not pob:
        missing.append("Path of Building.exe")
    if not charm:
        missing.append("PoeCharm3.exe")
    if charm and not translate["ok"]:
        missing.append("zh-rCN translation files")
    if pob and not (bridge and bridge.exists()):
        missing.append("Modules/AgentBridge.lua")
    if not mcp_server:
        missing.append("pob-agent/mcp_server.py")
    if not pob_cli:
        missing.append("pob-agent/pob_cli.py")

    result = {
        "ok": not missing,
        "missing": missing,
        "root": str(SKILL_ROOT),
        "pobDir": str(pob) if pob else None,
        "pobExe": str(pob / "Path of Building.exe") if pob else None,
        "launchLua": str(pob / "Launch.lua") if pob else None,
        "bridgeLua": str(bridge) if bridge else None,
        "bridgeInstalled": bool(bridge and bridge.exists()),
        "charmDir": str(charm) if charm else None,
        "charmExe": str(charm / "PoeCharm3.exe") if charm else None,
        "charmLink": link_info,
        "translate": translate,
        "agentDir": str(agent_dir) if agent_dir else None,
        "pobCli": str(pob_cli) if pob_cli else None,
        "mcpServer": str(mcp_server) if mcp_server else None,
        "installer": str(SKILL_ROOT / "install" / "install_pob_bridge.ps1"),
        "readme": str(SKILL_ROOT / "README.md"),
        "python": sys.executable,
        "candidates": {
            "pob": [str(p) for p, _ in pob_ranked[:10]],
            "charm": [str(p) for p, _ in charm_ranked[:10]],
        },
    }
    return result


def load_cached() -> dict | None:
    if not RUNTIME_JSON.exists():
        return None
    try:
        data = json.loads(RUNTIME_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not data.get("ok"):
        return None
    if not data.get("charmLink", {}).get("ok"):
        return None
    if not data.get("translate", {}).get("ok"):
        return None
    required = ("pobExe", "charmExe", "bridgeLua", "pobCli", "mcpServer")
    for key in required:
        value = data.get(key)
        if not value or not Path(value).exists():
            return None
    return {**data, "cached": True}


def write_runtime(result: dict) -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    RUNTIME_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["@echo off"]
    for key, value in (
        ("POB_AGENT_DIR", result.get("agentDir")),
        ("POB_AGENT_HOME", str(Path(result["agentDir"]).parent) if result.get("agentDir") else None),
        ("POB_POB_DIR", result.get("pobDir")),
        ("POB_CHARM_EXE", result.get("charmExe")),
        ("POB_PYTHON", result.get("python")),
        ("POB_BRIDGE_LUA", result.get("bridgeLua")),
    ):
        if value:
            lines.append(f'set "{key}={value}"')
    RUNTIME_ENV.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover local PoB / PoeCharm / zh-rCN / MCP runtime.")
    parser.add_argument("--write", action="store_true", help="write runtime.json/runtime.env")
    parser.add_argument("--fix", action="store_true", help="create the PoeCharm→PoB junction if missing")
    parser.add_argument("--quick", action="store_true", help="reuse a valid runtime.json instead of rescanning")
    args = parser.parse_args()
    result = load_cached() if args.quick else None
    if result is None:
        try:
            result = discover(fix=args.fix)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "missing": ["discovery failed"]}
        if args.write:
            write_runtime(result)
    elif args.write:
        write_runtime(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.get("ok") else 2)


if __name__ == "__main__":
    main()
