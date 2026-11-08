"""Agent loop, tool schemas and PoE build strategy prompts."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from bridge import Bridge, BridgeError
from llm import LLMClient, LLMError
from ninja_import import NinjaError, fetch_build

MAX_TOOL_RESULT_CHARS = 14000

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_build_state",
            "description": "读取当前 PoB 构建摘要：职业、升华、等级、天赋点、DPS、生命、护盾、EHP、抗性。每次开始工作或修改后都应调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "new_build",
            "description": "在 PoB 中新建一个空构建。会关闭当前构建（未保存内容会丢失），只能在用户明确要求时调用。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "构建名称"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "import_ninja",
            "description": "从 poe.ninja 角色页链接导入 BD 到 PoB。支持 https://poe.ninja/poe1/builds/<league>/character/<account>/<character> 以及 /poe1/pob/<id>。导入会替换当前构建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "poe.ninja 角色/BD 链接"},
                    "name": {"type": "string", "description": "可选，导入后的构建名"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "import_pob_code",
            "description": "用 PoB 导出码（pobb.in / poe.ninja 的 pathOfBuildingExport 字符串）导入构建，会替换当前构建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_class",
            "description": "选择职业与升华。职业用英文名（Witch/Marauder/Ranger/Duelist/Templar/Shadow/Scion），升华用英文名（如 Elementalist）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "class": {"type": "string", "description": "职业英文名"},
                    "ascendancy": {"type": "string", "description": "升华英文名，可省略"},
                },
                "required": ["class"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_level",
            "description": "设置角色等级（1-100）。默认关闭自动等级，避免分配天赋时等级自动变化。",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "minimum": 1, "maximum": 100},
                    "auto": {"type": "boolean", "description": "是否恢复自动等级"},
                },
                "required": ["level"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_passives",
            "description": "按英文名称搜索天赋节点，返回 id、名称、类型、升华、是否已分配、词缀。分配前必须先搜索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "英文名称子串，如 Fire、Life、Resistance"},
                    "type": {
                        "type": "string",
                        "enum": ["Normal", "Notable", "Keystone", "Mastery", "Socket", "ClassStart", "AscendClassStart"],
                    },
                    "ascendancy": {"type": "string", "description": "升华名，如 Elementalist"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 60},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "allocate_passives",
            "description": "按 id 分配天赋节点，自动补齐路径。ids 必须来自 search_passives 的结果；一次最多 20 个。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "integer"}, "description": "天赋节点 id 列表"},
                },
                "required": ["ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_allocated_passives",
            "description": "列出当前已分配的天赋节点（id、名称、类型、升华、词缀），用于分析现有 BD。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deallocate_passive",
            "description": "取消一个天赋节点及其依赖节点。",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_gems",
            "description": "按英文名称/标签搜索技能宝石。support=true 只搜辅助宝石，false 只搜主动宝石。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "support": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 60},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_skill_group",
            "description": "新建一个技能组并解析宝石。gems 是数组，每个元素 {name 或 id, level, quality}；第一个通常是主技能，其余为辅助。",
            "parameters": {
                "type": "object",
                "properties": {
                    "gems": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "id": {"type": "string"},
                                "level": {"type": "integer"},
                                "quality": {"type": "integer"},
                            },
                        },
                    },
                    "main_active_skill": {"type": "integer", "description": "主技能在 gems 中的下标，从 1 开始"},
                    "include_in_full_dps": {"type": "boolean"},
                },
                "required": ["gems"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "列出当前构建的所有技能组与宝石。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_uniques",
            "description": "按英文名称搜索传奇物品，返回数据库中的完整名称（通常带基底，如 'Tabula Rasa, Simple Robe'）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 60},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_unique",
            "description": "把指定传奇物品加入构建并自动装备（会替换原槽位）。name 用 search_uniques 返回的完整名称。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "slot": {"type": "string", "description": "可选，指定槽位如 'Ring 2'、'Body Armour'"},
                    "equip": {"type": "boolean", "description": "默认 true；false 只加入物品列表"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_item_raw",
            "description": "用 PoB 英文物品文本创建并装备一件物品（稀有/魔法/传奇均可），会替换原槽位。中文物品文本不支持。",
            "parameters": {
                "type": "object",
                "properties": {
                    "raw": {"type": "string", "description": "完整英文物品文本，含 Rarity/名称/基底/词缀"},
                    "slot": {"type": "string", "description": "可选，指定槽位如 'Ring 2'"},
                    "equip": {"type": "boolean", "description": "默认 true；false 只加入物品列表"},
                },
                "required": ["raw"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_items",
            "description": "列出当前构建的物品及装备槽位。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_config",
            "description": "设置 Configuration 页的一个输入，如 enemyLevel=83、bandit=None。键名用 PoB 内部 var 名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"description": "数字/字符串/布尔值"},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stats",
            "description": "读取计算输出。可指定 keys（如 TotalDPS、Life、TotalEHP、FireResist），不传返回常用集合。",
            "parameters": {
                "type": "object",
                "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_build",
            "description": "导出当前构建 XML 到 pob-agent/exports/，返回路径和字节数；不会发给模型全文。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_build",
            "description": "把当前构建保存到 PoB 的 Builds 目录，返回文件路径。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "文件名（不含 .xml）"}},
                "required": ["name"],
            },
        },
    },
]


def system_prompt(state: dict | None, state_error: str | None = None) -> str:
    state_text = json.dumps(state, ensure_ascii=False, indent=2) if state else f"（读取失败：{state_error}）"
    return f"""你是「PoB AI 副驾驶」，一个 Path of Exile 1（3.29 天赋树）的 BD 构建助手，通过工具直接操作正在运行的 Path of Building。

## 当前构建状态
{state_text}

## 工作方式
1. 先理解用户目标：职业/升华、主技能、玩法、预算、目标内容（清图/Boss/攻坚）、是否需要特定传奇。
2. 信息不足时先问 1-2 个关键问题；不要凭空假设预算或版本。
3. 变更前先 `get_build_state`。需要新建构建时，先提醒会丢失当前未保存内容，得到明确同意再 `new_build`。
4. 所有名称用英文 canonical name；先用 `search_passives` / `search_gems` / `search_uniques` 查，禁止编造 id 或名称。
5. 天赋：先定主技能和伤害类型，再找对应 notable/keystone；分配 `allocate_passives` 时 id 必须来自搜索结果。注意保留足够生命/抗性点。
6. 技能：主技能 + 4~5 个辅助；辅助必须真的能辅助该技能（看 tags/描述），不确定就换一个。
7. 物品：优先能买到的传奇/稀有；没有价格数据时说明这是「理论配置」，不要声称是最优或最便宜。
8. 每次修改后用 `get_stats` 复核 DPS、Life、EHP、抗性；如果变差，说明原因并回退。
9. `export_build` / `save_build` 只在用户要求或阶段完成时调用。

## BD 质量底线（非硬性）
- 等级 90 左右，约 110-115 点天赋。
- 生命净值尽量 ≥ 1500（视 BD 而定），元素抗性目标 75%（PoB 里 -60% 是未配置时的默认）。
- 有明确的伤害来源与防御层（生命/护盾/闪避/护甲/格挡/回复）。
- 不要为了堆 DPS 把天赋点全丢到伤害上；说明生存风险。

## 输出风格
- 用中文，简洁，先结论后细节。
- 每次工具调用后，用一两句话说明「做了什么、结果如何」。
- 最后给出：当前 DPS/生命/EHP/抗性、下一步建议、需要用户确认的选项。
- 不要输出大段 XML；需要导出时用工具。

## 工具预算
- 单轮最多 20 次工具调用；接近上限时停下，汇报进度并让用户决定下一步。
- 工具报错时先读错误信息，修正参数；不要重复同样的失败调用。
"""


def _json_result(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + f"...(truncated, {len(text)} chars total)"
    return text


class Agent:
    def __init__(self, bridge: Bridge, llm: LLMClient, max_steps: int = 20, exports_dir: Path | None = None):
        self.bridge = bridge
        self.llm = llm
        self.max_steps = max_steps
        self.exports_dir = exports_dir or Path(__file__).resolve().parent / "exports"
        self.history: list[dict] = []

    def reset(self) -> None:
        self.history.clear()

    def run_tool(self, name: str, args: dict) -> dict:
        """Public entry point for MCP / other frontends."""
        return self._execute(name, args)

    def _execute(self, name: str, args: dict) -> dict:
        try:
            if name == "get_build_state":
                return {"ok": True, "data": self.bridge.state()}
            if name == "new_build":
                self.bridge.rpc("new_build", name=args.get("name", "AI Build"))
                time.sleep(0.9)
                return {"ok": True, "data": self.bridge.state()}
            if name == "import_pob_code":
                code = str(args.get("code") or "").strip()
                if not code:
                    return {"ok": False, "error": "code is required"}
                imported = self.bridge.rpc(
                    "import_code",
                    code=code,
                    name=args.get("name") or "Imported Build",
                )
                time.sleep(1.6)
                return {"ok": True, "data": {"import": imported, "state": self.bridge.state()}}
            if name == "import_ninja":
                url = str(args.get("url") or "").strip()
                if not url:
                    return {"ok": False, "error": "url is required"}
                build = fetch_build(url)
                name = args.get("name") or f"{build.character} ({build.league})"
                imported = self.bridge.rpc("import_code", code=build.code, name=name)
                time.sleep(1.8)
                return {
                    "ok": True,
                    "data": {
                        "source": build.summary(),
                        "import": imported,
                        "state": self.bridge.state(),
                    },
                }
            if name == "select_class":
                data = self.bridge.rpc("select_class", **{k: v for k, v in args.items() if v not in (None, "")})
                time.sleep(0.35)
                return {"ok": True, "data": data}
            if name == "set_level":
                data = self.bridge.rpc("set_level", level=int(args["level"]), auto=bool(args.get("auto", False)))
                time.sleep(0.25)
                return {"ok": True, "data": data}
            if name == "search_passives":
                data = self.bridge.rpc(
                    "search_nodes",
                    q=args.get("query", ""),
                    type=args.get("type"),
                    ascendancy=args.get("ascendancy"),
                    limit=min(int(args.get("limit", 30)), 60),
                )
                return {"ok": True, "data": data}
            if name == "allocate_passives":
                ids = [int(i) for i in args.get("ids", [])][:20]
                allocated, failed = [], []
                for node_id in ids:
                    try:
                        allocated.append(self.bridge.rpc("alloc_node", id=node_id))
                    except BridgeError as exc:
                        failed.append({"id": node_id, "error": str(exc)})
                time.sleep(0.25)
                return {"ok": True, "data": {"allocated": allocated, "failed": failed, "state": self.bridge.state()}}
            if name == "list_allocated_passives":
                return {"ok": True, "data": self.bridge.rpc("list_allocated")}
            if name == "deallocate_passive":
                data = self.bridge.rpc("dealloc_node", id=int(args["id"]))
                time.sleep(0.2)
                return {"ok": True, "data": data}
            if name == "search_gems":
                data = self.bridge.rpc(
                    "search_gems",
                    q=args.get("query", ""),
                    support=args.get("support"),
                    limit=min(int(args.get("limit", 30)), 60),
                )
                return {"ok": True, "data": data}
            if name == "add_skill_group":
                gems = []
                for gem in args.get("gems", []):
                    item = {k: v for k, v in gem.items() if v is not None}
                    gems.append(item)
                data = self.bridge.rpc(
                    "add_skill_group",
                    gems=gems,
                    mainActiveSkill=int(args.get("main_active_skill", 1)),
                    includeInFullDPS=bool(args.get("include_in_full_dps", False)),
                )
                time.sleep(0.35)
                return {"ok": True, "data": data}
            if name == "list_skills":
                return {"ok": True, "data": self.bridge.rpc("list_skills")}
            if name == "search_uniques":
                data = self.bridge.rpc(
                    "search_uniques", q=args.get("query", ""), limit=min(int(args.get("limit", 30)), 60)
                )
                return {"ok": True, "data": data}
            if name == "add_unique":
                data = self.bridge.rpc(
                    "add_unique",
                    name=args["name"],
                    slot=args.get("slot"),
                    equip=args.get("equip", True),
                )
                time.sleep(0.25)
                return {"ok": True, "data": data}
            if name == "add_item_raw":
                data = self.bridge.rpc(
                    "add_item_raw",
                    raw=args["raw"],
                    slot=args.get("slot"),
                    equip=args.get("equip", True),
                )
                time.sleep(0.25)
                return {"ok": True, "data": data}
            if name == "list_items":
                return {"ok": True, "data": self.bridge.rpc("list_items")}
            if name == "set_config":
                data = self.bridge.rpc("set_config", key=args["key"], value=args.get("value"))
                time.sleep(0.2)
                return {"ok": True, "data": data}
            if name == "get_stats":
                data = self.bridge.rpc("get_stats", keys=args.get("keys"))
                return {"ok": True, "data": data}
            if name == "export_build":
                data = self.bridge.rpc("export_build")
                self.exports_dir.mkdir(parents=True, exist_ok=True)
                path = self.exports_dir / f"build-{time.strftime('%Y%m%d-%H%M%S')}.xml"
                path.write_text(data["xml"], encoding="utf-8")
                return {"ok": True, "data": {"path": str(path), "bytes": data["bytes"]}}
            if name == "save_build":
                return {"ok": True, "data": self.bridge.rpc("save_build", name=args.get("name", "ai-build"))}
            return {"ok": False, "error": f"Unknown tool: {name}"}
        except BridgeError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001 - tool errors must go back to the model
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def run(self, user_message: str) -> dict:
        self.history.append({"role": "user", "content": user_message})
        self.history = self.history[-16:]
        try:
            state = self.bridge.state()
            state_error = None
        except BridgeError as exc:
            state, state_error = None, str(exc)

        messages = [{"role": "system", "content": system_prompt(state, state_error)}] + self.history
        steps = []
        for _ in range(self.max_steps):
            message = self.llm.chat(messages, TOOLS)
            tool_calls = message.get("tool_calls") or []
            content = message.get("content") or ""
            if tool_calls:
                messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
                for call in tool_calls:
                    fn = call.get("function", {})
                    name = fn.get("name", "")
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except json.JSONDecodeError:
                        args = {}
                    result = self._execute(name, args)
                    steps.append({"tool": name, "args": args, "result": result})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id", ""),
                            "content": _json_result(result),
                        }
                    )
                continue

            if not content.strip():
                content = "模型没有返回内容，请重试或换一个说法。"
            self.history.append({"role": "assistant", "content": content})
            try:
                final_state = self.bridge.state()
            except BridgeError:
                final_state = None
            return {"reply": content, "steps": steps, "state": final_state, "demo": False}

        fallback = "本轮工具调用次数已达上限。我先停在当前状态，请确认下一步方向。"
        self.history.append({"role": "assistant", "content": fallback})
        try:
            final_state = self.bridge.state()
        except BridgeError:
            final_state = None
        return {"reply": fallback, "steps": steps, "state": final_state, "demo": False}


class DemoAgent:
    """Deterministic demo used when no API key is configured.

    It exercises the full bridge/tool path so the UI and PoB integration can be
    validated without a model.  It is intentionally not a general planner.
    """

    def __init__(self, bridge: Bridge):
        self.bridge = bridge
        self.done = False
        self.steps: list[dict] = []

    def _call(self, tool_name: str, fn, args: dict | None = None):
        args = args or {}
        try:
            result = fn()
            self.steps.append({"tool": tool_name, "args": args, "result": {"ok": True, "data": result}})
            return result
        except BridgeError as exc:
            self.steps.append({"tool": tool_name, "args": args, "result": {"ok": False, "error": str(exc)}})
            return None

    def run(self, user_message: str) -> dict:
        self.steps = []
        try:
            current = self.bridge.state()
            if self.done and not current.get("pointsUsed") and not current.get("ascendancyPointsUsed"):
                # PoB was restarted / a new empty build was created; allow the demo to run again.
                self.done = False
        except BridgeError:
            pass
        if not self.done:
            self._call(
                "new_build",
                lambda: self.bridge.rpc("new_build", name="AI Demo - Fireball Witch"),
                {"name": "AI Demo - Fireball Witch"},
            )
            time.sleep(1.0)
            self._call(
                "select_class",
                lambda: self.bridge.rpc("select_class", **{"class": "Witch", "ascendancy": "Elementalist"}),
                {"class": "Witch", "ascendancy": "Elementalist"},
            )
            time.sleep(0.35)
            self._call("set_level", lambda: self.bridge.rpc("set_level", level=90), {"level": 90})
            time.sleep(0.25)
            self._call(
                "add_skill_group",
                lambda: self.bridge.rpc(
                    "add_skill_group",
                    gems=[
                        {"name": "Fireball", "level": 20, "quality": 20},
                        {"name": "Controlled Destruction", "level": 20},
                        {"name": "Fire Penetration", "level": 20},
                        {"name": "Faster Casting", "level": 20},
                    ],
                    mainActiveSkill=1,
                ),
                {"gems": ["Fireball", "Controlled Destruction", "Fire Penetration", "Faster Casting"]},
            )
            time.sleep(0.35)
            for notable in ("Heart of Flame", "Constitution"):
                found = self._call(
                    "search_passives",
                    lambda n=notable: self.bridge.rpc("search_nodes", q=n, type="Notable", limit=3),
                    {"query": notable, "type": "Notable"},
                )
                if found and found.get("nodes"):
                    node_id = found["nodes"][0]["id"]
                    self._call("alloc_node", lambda i=node_id: self.bridge.rpc("alloc_node", id=i), {"id": node_id})
                    time.sleep(0.2)
            self._call("add_unique", lambda: self.bridge.rpc("add_unique", name="Tabula Rasa"), {"name": "Tabula Rasa"})
            time.sleep(0.25)
            self._call("set_config", lambda: self.bridge.rpc("set_config", key="enemyLevel", value=83), {"key": "enemyLevel", "value": 83})
            time.sleep(0.2)
            self.done = True

        state = self.bridge.state()
        stats = state.get("stats", {})
        dps = stats.get("TotalDPS") or 0
        life = stats.get("Life") or 0
        ehp = stats.get("TotalEHP") or 0
        reply = (
            "【演示模式】未配置模型 API Key，我按内置脚本跑了一遍完整链路，用来验证 PoB 桥接与工具调用。\n\n"
            f"当前构建：{state.get('className')} / {state.get('ascendancy')}，等级 {state.get('level')}，"
            f"天赋点 {state.get('pointsUsed')}。\n"
            f"核心输出：DPS {dps:,.0f}，生命 {life:,.0f}，EHP {ehp:,.0f}。\n\n"
            "已执行：新建构建 → 选职业/升华 → 设等级 90 → 火球+3 辅助 → 点火系/生命 notable → 装备 Tabula Rasa → 设置敌人等级 83。\n\n"
            "这是演示骨架，不是一套完整可玩 BD。配置 `pob-agent/config.json` 里的 base_url / api_key / model 后，"
            "我会用真实模型按你的目标继续配装、点天赋并迭代。"
        )
        return {"reply": reply, "steps": self.steps, "state": state, "demo": True}
