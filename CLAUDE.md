# 项目上下文

## 项目定位

本项目是一个 **Claude Code Skill**（hydros-engine-skill），不是 MCP 服务本身。

- **hydros-engine-executor**（外部提供）：仿真任务执行 MCP 服务
- **hydros-engine-mdm**（外部提供）：场景建模元数据与拓扑前置条件服务
- **hydros-engine-skill**（本项目）：Skill，编排调用这些能力并引导用户完成完整工作流

## 架构关系

```
Claude Code
  └── hydros-engine-skill（本项目，Skill 层）
        ├── 调用 hydros-engine-executor（仿真执行 MCP）
        │     └── 连接 hydros-engine（水力仿真引擎）
        └── 检查 hydros-engine-mdm（元数据前置条件）
```

## 外部服务分工

| 服务 | 功能 |
|------|------|
| `hydros-engine-executor` | 创建仿真任务、跟踪进度、导出结果、上传报告 |
| `hydros-engine-mdm` | 提供场景清单、预置事件、水网对象、拓扑和 `objects.yaml` 相关前置条件 |

## Skill 职责

1. **场景查询与展示** — 查询场景、补充建模元数据前置检查、引导用户选择
2. **仿真任务创建与进度跟踪** — 创建仿真任务，持续跟踪进度直到终态
3. **结果查询、图表生成与数据分析** — 导出结果文件（CSV / XLSX），生成时序曲线/对比图，执行异常检测（负压、流速异常、水头损失等）

## 当前进度

- [x] README.md 已创建并推送到 GitHub
- [x] SKILL.md 已创建
- [ ] 测试用例待编写
- [ ] 评估与优化

## Git 远程

- SSH: git@github.com:NovaCogniWorks/hydros-engine-skill.git
- 主分支: main

## 使用语言

与用户沟通使用中文。
