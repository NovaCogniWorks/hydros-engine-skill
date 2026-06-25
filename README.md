# hydros-engine-skill

Hydros 水力仿真 Skill，用于编排线上 Hydros Engine MCP：场景查询、仿真任务创建、进度跟踪、结果导出、MCP resource 读取、图表分析和 HTML 报告生成。

当前线上已验证的 MCP 入口是统一 HTTP 端点：

```text
https://mcp.hydroos.pub/
```

`api.hydroos.pub` 是 MCP server 背后调用 hydros-data / hydros-accounts / engine API 的业务域名，不是 AI 客户端直接配置的 MCP endpoint。历史文档中的 `https://hydroos.cn/mcps/...` 已废弃，不应再用于新配置。场景配置或对象资源中仍可能出现 `https://hydroos.cn/s3/...` 这类历史静态资源 URL，当前先保持兼容，不在本 Skill 文档中要求迁移。

## 定位

```text
AI Assistant / Agent
  └── hydros-engine-skill
        ├── 调用 Hydros Engine MCP 工具
        │     ├── biz_scenario_id_lists
        │     ├── subscribe_to_simulation_events
        │     ├── create_simulation_task
        │     ├── get_task_step / get_task_status
        │     ├── get_timeseries_data / get_export_status
        │     └── resources/read
        └── 生成图表、异常分析和 HTML 报告

Hydros Engine MCP
  └── 后端执行链路：api.hydroos.pub / hydros-data / hydros-engine
```

## 核心能力

### 场景查询

调用 MCP 工具 `biz_scenario_id_lists` 获取可运行场景清单、场景名称和配置 URL。最新线上环境中，场景查询和仿真执行都通过统一 MCP endpoint 暴露，不再要求客户端分别配置 `hydros-engine-mdm` 与 `hydros-engine-executor` 两个 URL。

### 仿真执行

标准顺序：

1. `initialize`
2. `subscribe_to_simulation_events`
3. `create_simulation_task`
4. `get_task_step` 轮询进度
5. 仅在异常或需要完整状态时调用 `get_task_status`

已验证默认场景示例：

```text
场景 ID: 200000
场景名称: 京石段-全默认场景
默认输出步长: 7200s
```

不建议将 `step_resolution` 设置得过小。线上验证中，使用默认输出步长可完成任务；过细步长可能导致 engine 步进超时。

### 结果导出与读取

标准顺序：

1. `get_timeseries_data`
2. 轮询 `get_export_status`
3. 等待状态变为 `COMPLETED`
4. 优先读取返回的 `resource_uri`
5. 通过 MCP `resources/read` 读取结果内容
6. 再生成 HTML 报告

当前生产链路中，结果资源推荐使用：

```text
hydroengine://downloads/<file_name>.xlsx
```

不要默认把 `/s3/...` HTTPS URL 当作可直接下载地址；该类地址可能需要 JWT，不一定能用 MCP token 直接访问。

## MCP 配置

### Codex TOML 示例

```toml
[mcp_servers.hydros-engine-executor]
url = "https://mcp.hydroos.pub/"
bearer_token_env_var = "MCP_TOKEN"

[mcp_servers.hydros-engine-executor.headers]
Execution-Source = "codex"
Production-Code = "copaw"
Accept = "application/json, text/event-stream"
```

### OpenHands 示例

```bash
openhands mcp add hydros-engine-executor \
  --transport http \
  --header "Authorization: Bearer ${HYDROS_API_TOKEN}" \
  --header "Execution-Source: openhands" \
  --header "Production-Code: copaw" \
  --header "Accept: application/json, text/event-stream" \
  https://mcp.hydroos.pub/
```

如果本地统一使用 `MCP_TOKEN`：

```bash
openhands mcp add hydros-engine-executor \
  --transport http \
  --header "Authorization: Bearer ${MCP_TOKEN}" \
  --header "Execution-Source: openhands" \
  --header "Production-Code: copaw" \
  --header "Accept: application/json, text/event-stream" \
  https://mcp.hydroos.pub/
```

### HTTP 直连排查

```bash
curl -X POST https://mcp.hydroos.pub/ \
  -H "Authorization: Bearer <token>" \
  -H "Execution-Source: codex" \
  -H "Production-Code: copaw" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}'
```

## 报告生成

Skill 默认使用 `skills/hydros-engine-skill-executor/assets/hydros-report-template/` 中的 HTML 模板，并基于真实导出数据生成报告。报告生成前必须已经完成结果导出和 resource 读取，不能使用默认数据、旧缓存或残缺文件冒充真实结果。

推荐输出结构：

```text
output/<biz_scene_instance_id>/
  data/
  charts/
  report/
    simulation_report.html
    simulation_report.md
```

## 项目结构

```text
hydros-engine-skill/
├── README.md
├── CLAUDE.md
└── skills/
    ├── hydros-engine-skill-executor/
    │   ├── SKILL.md
    │   ├── assets/
    │   ├── references/
    │   └── scripts/
    └── hydros-engine-skill-analyst/
        ├── SKILL.md
        ├── assets/
        └── scripts/
```
