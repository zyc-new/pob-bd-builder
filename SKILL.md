---
name: pob-bd-builder
description: 每次先启动中文版 PoB（PoeCharm3），再通过 MCP 直接控制 Path of Building（PoE1 3.29）构建 BD：从 poe.ninja 链接导入 BD、读取职业/等级/天赋/DPS/生存，搜索并分配天赋，添加技能组与装备，设置配置，迭代到目标。适用于用户要求导入 BD、配 BD、改天赋、加技能/装备、分析或优化当前 PoB 构建。
---

# PoB BD Builder（MCP Skill）

> **硬性规则：所有任务必须使用中文版 PoB（由 PoeCharm3 启动）。**
> 禁止直接启动/操作 `Path of Building.exe`（英文版）。
> 每次任务开始（含续接、导入、修改）先做两步：
> 1. `discover-env.cmd`：检查本地 PoB、PoeCharm、zh-rCN 汉化文件、AgentBridge、MCP 文件位置；
> 2. `start-cn.cmd`：用检查到的路径启动中文 PoB，并做一次 MCP 握手验证。
> 返回 `ok:true` 后再执行 `state`/`import-ninja`/修改。`pob.cmd` 内部也会自动做这两步。
> 如果检测到英文版正在运行：先让用户保存/关闭，再启动中文版，不要在英文版上继续。

本技能让当前 chat AI **直接调用 MCP 服务**控制 PoB，不需要 Claude Desktop、Cherry Studio
或其他 MCP 客户端。原理：技能脚本 `pob_cli.py` 是 MCP stdio 客户端，每次调用启动
`pob-agent/mcp_server.py`，再通过 `127.0.0.1:49090` 调用 PoB 内的 `AgentBridge`。

## 首次安装（分享给其他人）

1. 准备 PoB Portable + PoeCharm（技能不包含这两个本体）。
2. 把 `pob-bd-builder` 放到 `%USERPROFILE%\.dsh\skills\`。
3. 每个 PoB 目录运行一次桥安装：
   ```powershell
   & "$env:USERPROFILE\.dsh\skills\pob-bd-builder\install\install-bridge.cmd"
   ```
   会备份 `Launch.lua`、复制 `Modules/AgentBridge.lua`、幂等打补丁。
4. 再执行下面的环境检查/启动流程。

## 环境检查（每次第一步）

```powershell
& "$env:USERPROFILE\.dsh\skills\pob-bd-builder\scripts\discover-env.cmd"
```

它会从 `POB_ROOT` / `POB_AGENT_HOME` / 当前目录 / 上级目录 / 用户目录 / 常见盘符搜索：

- `Path of Building.exe`（PoB 根目录），并检查 `Launch.lua`、`Modules/AgentBridge.lua`；
- `PoeCharm3.exe`（汉化启动器）；
- `PoeCharm-main/Data/Translate/zh-rCN`（简体中文 CSV）、`Translate.json`；
- `pob-agent/mcp_server.py`、`pob_cli.py`；
- PoeCharm 目录下是否已有指向 PoB 的 `PathOfBuildingCommunity-Portable` junction（缺失可自动创建）。

输出 `{"ok":true,...}` 才继续；结果写入 `%LOCALAPPDATA%\pob-bd-builder\runtime.json`
/ `runtime.env`（不写进技能目录，可用 `POB_AGENT_DATA` 覆盖）。
关键字段：`pobDir`、`charmExe`、`translate.dir`、`bridgeInstalled`、`pobCli`、`mcpServer`。

## 前置检查

1. **先启动/确认中文版 PoB**：

```powershell
& "$env:USERPROFILE\.dsh\skills\pob-bd-builder\scripts\start-cn.cmd"
```

返回 `{"ok":true,...}` 才继续；`alreadyRunning` 或 `launched` 都可以。
如果报英文版正在运行，先保存并关闭 `Path of Building.exe`，再重跑。

2. 定义脚本路径并做一次状态检查：

```powershell
$pob = "$env:USERPROFILE\.dsh\skills\pob-bd-builder\scripts\pob.cmd"
& $pob state
```

返回 `{"ok":true,"data":{...}}` 才继续。若返回 `bridge_unreachable`，再跑一次
`& $pob state`；CLI 会在调用前唤醒 PoB 窗口。仍失败就把 PoB 窗口点一下/切到前台重试。
PoB 在后台被系统暂停时，工具会超时，这不是数据损坏。

## 命令速查

技能自带 `mcp/`（mcp_server、pob_cli 等）；`pob.cmd` 通过
`%LOCALAPPDATA%\pob-bd-builder\runtime.json` 定位它，不再依赖外部项目目录。
下表用 `pob.cmd` 代指上面的 `& $pob`。所有输出是 JSON。

> 含 `#`、`&` 的 poe.ninja 链接必须用双引号包住；如果 `.cmd` 仍然把 `&` 当命令分隔符，
> 用 `$env:POB_NINJA_URL="<url>"; & $pob import-ninja --url-env`。

> DSH 的每次 shell 调用可能是新进程：**每条命令前都重新定义 `$pob`**，或直接写完整路径。
> 不要把 `$pob` 当成跨命令保留的全局变量。

| 目的 | 命令 |
| --- | --- |
| 检查本地 PoB/PoeCharm/汉化/MCP | `discover-env.cmd` |
| 启动/确认中文 PoB（含 MCP 握手） | `start-cn.cmd` |
| 当前状态 | `pob.cmd state` |
| 新建（危险） | `pob.cmd new "AI Build"` |
| 导入 poe.ninja BD（危险，替换当前构建） | `pob.cmd import-ninja "https://poe.ninja/poe1/builds/allflame/character/账号/角色"` |
| 导入（含 & 的链接更稳） | `$env:POB_NINJA_URL="..."; pob.cmd import-ninja --url-env` |
| 导入 PoB 导出码（危险） | `pob.cmd import-code --file code.txt --name "Imported"` |
| 职业/升华 | `pob.cmd class Witch Elementalist` |
| 等级 | `pob.cmd level 90` |
| 搜天赋 | `pob.cmd search-passives Fire --type Notable --limit 20` |
| 分配天赋 | `pob.cmd alloc 14040,60501` |
| 取消天赋 | `pob.cmd dealloc 14040` |
| 已分配列表 | `pob.cmd allocated` |
| 搜宝石 | `pob.cmd search-gems Fireball --no-support` |
| 加技能组 | `pob.cmd skill Fireball "Controlled Destruction" "Fire Penetration"` |
| 技能组列表 | `pob.cmd skills` |
| 搜传奇 | `pob.cmd search-unique Tabula` |
| 加传奇 | `pob.cmd unique "Tabula Rasa, Simple Robe"` |
| 加物品文本 | `pob.cmd item --file item.txt` |
| 物品列表 | `pob.cmd items` |
| 配置 | `pob.cmd config enemyLevel 83` |
| 统计 | `pob.cmd stats TotalDPS Life TotalEHP FireResist` |
| 导出 XML | `pob.cmd export` |
| 保存 | `pob.cmd save my-build` |
| 原始工具调用 | `$env:POB_ARGS='{"keys":["TotalDPS","Life"]}'; & $pob call get_stats --args-env` |
| 工具列表 | `pob.cmd tools` |

复杂 JSON 参数优先用环境变量 `POB_ARGS`（`--args-env`）或 JSON 文件（`--args-file`），
避免 `.cmd` 吞引号；`& $pob tools` 会返回 19 个 `pob_*` 工具及 schema。

## 工作流（副驾驶）

-1. **检查环境**：`discover-env.cmd`，确认 `ok:true` 且汉化目录是 `zh-rCN`。
0. **启动中文 PoB + MCP**：`start-cn.cmd`；返回 `ok:true`（首次启动会带 `mcpOk`）。
   `pob.cmd` 也会自动执行这两步，但显式执行一次更稳。
0.1 **用户给 poe.ninja 链接**：先 `save` 当前构建作备份，再 `import-ninja <url>`；导入会替换当前
   构建（poe.ninja 角色页会自动解析 league/account/character 并取 `pathOfBuildingExport`）。
   导入后 `state` + `stats`，再按用户要求修改。
1. **读状态**：`state`。确认当前 build 名称、职业、等级、点数、DPS/生命/EHP/抗性。
2. **问清目标**：职业/升华、主技能、玩法（清图/Boss）、预算、防御层。信息不足先问 1–2 个问题。
3. **先搜后用**：天赋用 `search-passives`，宝石用 `search-gems`，传奇用 `unique` 搜索/或直接给完整名。
   所有名称用英文 canonical name；禁止编造 id。
4. **执行小步**：一次改 1–3 个动作（选职业、加技能组、分配一组节点、加一件装备），每步后
   `stats` 复核。
5. **复核**：目标 DPS/生命/EHP/抗性；变差就说明并回退（`dealloc` / 换装）。
6. **交付**：汇总改动、当前数值、下一步；用户要求时 `export` 或 `save`。

**危险操作**：`new` 会关闭当前构建且未保存内容会丢失。必须先向用户确认，不能擅自执行。
其他写操作（分配/加物品）也要先说明将要做什么。

## 构建规则

- 等级 90 左右，约 110–115 点天赋；先定主技能和伤害类型，再点对应 notable。
- 生存底线：生命/护盾、元素抗性目标 75%、至少一层防御（护甲/闪避/格挡/回复）。
- 技能组：主技能 + 4–5 个辅助；辅助必须能作用于该技能（看 `search-gems` 的 tags）。
- 物品：没有价格数据时说明这是“理论配置”；不要声称最便宜/最优。
- 中文只用于对话和 Notes；写回 PoB 的搜索词、物品文本必须是英文。
- 不要为了 DPS 把天赋全点伤害；说明生存风险。

## 示例

用户：“帮我把这个火球女巫补到 90 级，抗性满，DPS 尽量高，先给方案再改。”

```powershell
$pob = "$env:USERPROFILE\.dsh\skills\pob-bd-builder\scripts\pob.cmd"
& $pob state
& $pob class Witch Elementalist
& $pob level 90
& $pob skill Fireball "Controlled Destruction" "Fire Penetration" "Faster Casting"
& $pob search-passives Fire --type Notable --limit 30
& $pob alloc 60501,14040
& $pob search-passives Life --type Notable --limit 20
& $pob alloc 55485
& $pob config enemyLevel 83
& $pob stats TotalDPS Life TotalEHP FireResist ColdResist LightningResist
```

先给用户看方案与预期，再执行；每步把 `stats` 的关键变化用一两句话说明。

### 导入 poe.ninja BD

```powershell
$pob = "$env:USERPROFILE\.dsh\skills\pob-bd-builder\scripts\pob.cmd"
& $pob save before-import
& $pob import-ninja "https://poe.ninja/poe1/builds/<league>/character/<account>/<character>"
& $pob state
& $pob stats TotalDPS CombinedDPS Life TotalEHP FireResist ColdResist LightningResist
```

支持 `https://poe.ninja/poe1/pob/<id>` 直接 POB 链接；也支持 `import-code --file` 导入
任意 PoB 导出码。链接里的 `#i=5&search=...` 片段会被忽略。

## 故障排查

| 返回 | 处理 |
| --- | --- |
| `missing` 非空 | `discover-env.cmd` 查看缺哪个：PoB/PoeCharm/zh-rCN/AgentBridge/MCP |
| `英文版正在运行` | 保存并关闭 `Path of Building.exe`，再运行 `start-cn.cmd`；不要在英文版上操作 |
| `Chinese PoB is not ready` | 运行 `start-cn.cmd` 看详细错误；确认 PoeCharm3 已启动且点过「启动」 |
| `bridge_unreachable` | 中文 PoB 未启动/后台暂停；运行 `start-cn.cmd` 或点一下 PoB 后重试；看 `agent_bridge.log` |
| `no_build` | PoB 不在 BUILD 模式；先在 PoB 新建/打开构建 |
| `Unknown passive node id` | id 不是来自 `search-passives`；重新搜索 |
| `Unknown unique` | 用 `unique` 搜索完整名（通常带基底） |
| CLI 超时 | PoB 卡住或桥 49090 被占用；确认单实例、看日志 |
| 中文乱码 | 用 PowerShell 7 / 设置 `PYTHONUTF8=1` |

## 边界

- 只支持 stdio MCP（CLI 内部处理），不需要外部客户端。
- 不做交易/价格；不保证 BD 一定适合真实赛季经济。
- PoB 更新会覆盖 `Launch.lua` 补丁；更新后需重放（见 docs/02）。
- 多开 PoB 会争抢 49090 端口；当前只支持单实例。
