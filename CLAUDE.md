# 项目上下文

## 项目定位

本项目是一个 **Hydros Engine Skill**，不是 MCP 服务本身。

- **Hydros Engine MCP**：线上统一 MCP endpoint，当前生产地址为 `https://mcp.hydroos.pub/`
- **hydros-engine-skill**：Skill 层，编排 MCP 工具调用并生成分析报告
- **后端真实执行链路**：MCP server 背后调用 `api.hydroos.pub` 上的 hydros-data / hydros-accounts / hydros-engine 能力

历史文档中的 `hydros-engine-executor` / `hydros-engine-mdm` 双 MCP URL 已不作为新配置推荐。为了兼容已有客户端配置，MCP server 名称仍可使用 `hydros-engine-executor`，但 URL 应统一为 `https://mcp.hydroos.pub/`。

## 架构关系

```text
AI Assistant
  └── hydros-engine-skill
        ├── 调用 Hydros Engine MCP 工具
        │     ├── 场景查询
        │     ├── 仿真任务创建
        │     ├── 进度轮询
        │     ├── 结果导出
        │     └── resources/read 读取结果
        └── 生成图表、异常分析和 HTML 报告

Hydros Engine MCP
  └── api.hydroos.pub / hydros-data / hydros-engine
```

## Skill 职责

1. **场景查询与展示**：查询场景、展示配置 URL 和核心能力。
2. **仿真任务创建与进度跟踪**：订阅事件、创建任务、持续轮询到终态。
3. **结果查询与报告生成**：导出结果，使用 `resource_uri` + `resources/read` 读取数据，生成图表、异常分析和 HTML 报告。

## 当前配置口径

```text
MCP URL: https://mcp.hydroos.pub/
Authorization: Bearer <token>
Execution-Source: codex / openhands / claude / copaw
Production-Code: copaw
Accept: application/json, text/event-stream
```

`api.hydroos.pub` 是后端 API 域名，不是 AI 客户端 MCP URL。`hydroos.cn/s3/...` 历史资源地址可能仍在场景配置中出现，当前保持兼容。

## 当前进度

- [x] MCP 线上调用链路已验证
- [x] 结果导出与 `resources/read` 链路已验证
- [x] HTML 报告生成链路已验证
- [ ] 后续继续沉淀回归测试和文档示例

## Git 远程

- SSH: git@github.com:NovaCogniWorks/hydros-engine-skill.git
- 主分支: main

## 使用语言

与用户沟通使用中文。
