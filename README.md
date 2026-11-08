# PoB BD Builder Skill

一个可分享的 DSH 技能：让 chat AI 直接通过 MCP 控制 **中文版 Path of Building**
（PoeCharm3 启动），支持：

- 自动检查本地 PoB / PoeCharm / `zh-rCN` 汉化 / AgentBridge / MCP 环境
- 自动启动中文 PoB，并做 MCP 握手
- 从 `poe.ninja` 角色页链接导入 BD
- 读取/修改天赋、技能、装备、配置，读取 DPS/生存并迭代
- 无需 Claude Desktop / Cherry Studio 等额外 MCP 客户端

## 包内容

```
pob-bd-builder/
  SKILL.md                 技能说明（DSH 读取）
  README.md                本文件
  scripts/
    discover-env.cmd       检查环境
    discover_env.py        环境发现逻辑
    start-cn.cmd           启动中文 PoB
    start_pob_cn.ps1       启动/点击/等待桥/MCP 握手
    pob.cmd                执行 PoB 命令（自动确保中文 PoB）
    run_pob.py             用发现到的路径运行 MCP 客户端
    pob.ps1                PowerShell 包装（需允许脚本执行）
  mcp/                     MCP 服务端/客户端（自包含）
    mcp_server.py
    pob_cli.py
    agent.py
    bridge.py
    llm.py
    ninja_import.py
    pob_wake.py
  pob/
    AgentBridge.lua        要安装到 PoB/Modules 的桥
  install/
    install_pob_bridge.ps1 安装/更新桥与 Launch.lua 补丁
    install-bridge.cmd     一键入口
```

**不包含** PoB 或 PoeCharm 本体（版权/许可归原作者），需要用户自行下载：

- Path of Building Community Portable（当前补丁按 **2.67.2** 的 `Launch.lua` 结构编写）
- PoeCharm（中文汉化启动器，当前自动点击按 3.6.0 布局）

其他 PoB 版本的 `Launch.lua` 结构可能不同；安装器会报 marker 找不到，此时需要手动
参考 `pob/AgentBridge.lua` 与 `Launch.lua` 的插入点，或使用匹配版本。

## 环境要求

- Windows
- Python 3.10+（实测 3.13；只用标准库，无需 pip 安装）
- PoB Portable 2.67.2（其他版本见下方说明）
- PoeCharm3（中文启动器）

## 安装

1. 准备 PoB Portable 和 PoeCharm。PoeCharm 要求 PoB 目录在它下面；如果不在，
   `discover-env` 会自动创建 `PoeCharm-main/PathOfBuildingCommunity-Portable` junction。
2. 把 `pob-bd-builder` 整个目录放到：
   `%USERPROFILE%\.dsh\skills\pob-bd-builder\`
3. 安装/更新 PoB 桥（每个 PoB 目录一次）：
   ```powershell
   & "$env:USERPROFILE\.dsh\skills\pob-bd-builder\install\install-bridge.cmd"
   ```
   - 自动备份 `Launch.lua` 为 `Launch.lua.bak-agent`
   - 复制 `Modules/AgentBridge.lua`
   - 幂等：已安装时不会重复插入
4. 检查环境：
   ```powershell
   & "$env:USERPROFILE\.dsh\skills\pob-bd-builder\scripts\discover-env.cmd"
   ```
   必须返回 `ok:true`，并显示 `pobDir`、`charmExe`、`translate.dir`、`pobCli`、`mcpServer`。
   结果写入 `%LOCALAPPDATA%\pob-bd-builder\runtime.json` / `runtime.env`，**不写进技能目录**
   （可用 `POB_AGENT_DATA` 覆盖）。
5. 启动中文 PoB + MCP：
   ```powershell
   & "$env:USERPROFILE\.dsh\skills\pob-bd-builder\scripts\start-cn.cmd"
   ```
6. 新开一个 DSH 会话，技能列表应出现 `pob-bd-builder`。

## 使用

```powershell
$pob = "$env:USERPROFILE\.dsh\skills\pob-bd-builder\scripts\pob.cmd"
& $pob state
& $pob import-ninja "https://poe.ninja/poe1/builds/..." --name "Imported"
& $pob stats TotalDPS Life TotalEHP
```

`pob.cmd` 每条命令都会先做环境检查、确保中文 PoB 运行，再启动 MCP 客户端。

## 更新 PoB 后

PoB 更新会覆盖 `Launch.lua`。重新运行：

```powershell
& "$env:USERPROFILE\.dsh\skills\pob-bd-builder\install\install-bridge.cmd"
```

脚本会保留原始备份 `Launch.lua.bak-agent`；如需回滚，恢复该文件即可。

## 安全与限制

- 所有服务只监听 `127.0.0.1`。
- 导入/新建会替换当前构建，执行前先 `save`。
- MCP 采用 stdio，由 `pob_cli.py` 每次调用启动，不是常驻端口。
- 自动点击 PoeCharm「启动」依赖当前 3.6.0 布局；如果布局变化，脚本会报告失败，
  可手动点一次「启动」。
- 中文 PoB 后台失焦可能暂停帧循环；工具调用会做光标唤醒，失败时点一下 PoB。
