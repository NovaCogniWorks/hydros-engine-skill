# 项目上下文

## 项目定位

本项目是一个 **Claude Code Skill**（hydros-engine-skill），不是 MCP 服务本身。

- **hydros-engine-mcp**（外部提供）：MCP 服务，提供 4 个工具
- **hydros-engine-skill**（本项目）：Skill，编排调用这些 MCP 工具，引导用户完成完整工作流

## 架构关系

```
Claude Code
  └── hydros-engine-skill（本项目，Skill 层）
        └── 调用 hydros-engine-mcp（外部 MCP 服务）
              └── 连接 hydros-engine（水力仿真引擎）
```

## 外部 MCP 服务提供的 4 个工具

| 工具 | 功能 |
|------|------|
| `list_scenarios` | 获取场景清单 |
| `create_simulation` | 创建仿真任务 |
| `subscribe_progress` | 建立 SSE 连接，推送仿真进度/完成/失败 |
| `query_timeseries` | 查询水网对象时序数据 |

## Skill 职责

1. **场景查询与展示** — 调用 list_scenarios，格式化展示，引导用户选择
2. **仿真任务创建与进度跟踪** — 调用 create_simulation + subscribe_progress，SSE 消息过滤（heartbeat 丢弃、progress 节流、completed/failed 终态），向用户推送进度
3. **结果查询、图表生成与数据分析** — 调用 query_timeseries，生成时序曲线/对比图，执行异常检测（负压、流速异常、水头损失等）

## 当前进度

- [x] README.md 已创建并推送到 GitHub
- [ ] SKILL.md 待创建（Skill 定义文件）
- [ ] 测试用例待编写
- [ ] 评估与优化

## Git 远程

- SSH: git@github.com:NovaCogniWorks/hydros-engine-skill.git
- 主分支: main

## 使用语言

与用户沟通使用中文。
