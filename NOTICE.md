# NOTICE

本技能包只包含集成代码（MCP 服务、CLI、PoB 桥、安装/启动脚本和文档）。

不包含：

- Path of Building / Path of Building Community（其版权与许可归原作者）
- PoeCharm（其版权与许可归原作者）
- 任何游戏本体文件

使用前请自行从官方/作者渠道下载 PoB Portable 和 PoeCharm，并遵守其许可。
`install_pob_bridge.ps1` 会修改 PoB 的 `Launch.lua` 并复制 `Modules/AgentBridge.lua`；
脚本会先备份 `Launch.lua.bak-agent`。PoB 更新可能覆盖补丁，需要重新安装。
