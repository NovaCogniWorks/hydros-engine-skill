# Hydro Engine MCP 连接指南

本文档记录当前线上已验证的 Hydros Engine MCP 连接方式、排查步骤和常见问题。

## 当前生产入口

最新生产 MCP endpoint：

```text
https://mcp.hydroos.pub/
```

历史入口 `https://hydroos.cn/mcps/hydros-engine-executor` 和 `https://hydroos.cn/mcps/hydros-engine-mdm` 已不再作为新文档推荐配置。当前线上环境通过统一 MCP endpoint 暴露场景查询、仿真执行、进度跟踪、结果导出和 resource 读取能力。

说明：

- AI 客户端直接配置 `https://mcp.hydroos.pub/`。
- `api.hydroos.pub` 是 MCP server 背后访问 hydros-data / hydros-accounts / hydros-engine 的业务域名。
- 场景配置中仍可能返回 `hydroos.cn/s3/...` 历史静态资源地址，当前保持兼容，不等同于 MCP endpoint。

## 必需 Header

```text
Authorization: Bearer <token>
Content-Type: application/json
Execution-Source: <client>
Production-Code: copaw
Accept: application/json, text/event-stream
```

`Execution-Source` 建议按客户端设置：

| 客户端 | 推荐值 |
| --- | --- |
| Codex | `codex` |
| OpenHands | `openhands` |
| Claude Code | `claude` |
| Copaw | `copaw` |

## 配置示例

### Codex

```toml
[mcp_servers.hydros-engine-executor]
url = "https://mcp.hydroos.pub/"
bearer_token_env_var = "MCP_TOKEN"

[mcp_servers.hydros-engine-executor.headers]
Execution-Source = "codex"
Production-Code = "copaw"
Accept = "application/json, text/event-stream"
```

如果当前 Codex 版本使用不同 TOML schema，保持同样的 URL、Bearer token 和 Header 即可。

### OpenHands

```bash
openhands mcp add hydros-engine-executor \
  --transport http \
  --header "Authorization: Bearer ${HYDROS_API_TOKEN}" \
  --header "Execution-Source: openhands" \
  --header "Production-Code: copaw" \
  --header "Accept: application/json, text/event-stream" \
  https://mcp.hydroos.pub/
```

如果本机统一使用 `MCP_TOKEN`：

```bash
openhands mcp add hydros-engine-executor \
  --transport http \
  --header "Authorization: Bearer ${MCP_TOKEN}" \
  --header "Execution-Source: openhands" \
  --header "Production-Code: copaw" \
  --header "Accept: application/json, text/event-stream" \
  https://mcp.hydroos.pub/
```

### JSON 形态示例

```json
{
  "mcpServers": {
    "hydros-engine-executor": {
      "type": "http",
      "url": "https://mcp.hydroos.pub/",
      "headers": {
        "Authorization": "Bearer <token>",
        "Execution-Source": "codex",
        "Production-Code": "copaw",
        "Accept": "application/json, text/event-stream"
      }
    }
  }
}
```

## HTTP 直连排查

只在排查问题时直连。正常使用应优先走已注册的 MCP 工具。

```bash
curl -X POST https://mcp.hydroos.pub/ \
  -H "Authorization: Bearer <token>" \
  -H "Execution-Source: codex" \
  -H "Production-Code: copaw" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}'
```

连接成功后，再用 `tools/list` 检查工具是否可见：

```bash
curl -X POST https://mcp.hydroos.pub/ \
  -H "Authorization: Bearer <token>" \
  -H "Execution-Source: codex" \
  -H "Production-Code: copaw" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}'
```

## 标准工作流

1. `initialize` 初始化 MCP 连接。
2. `tools/list` 确认可用工具。
3. `biz_scenario_id_lists` 获取场景清单。
4. `subscribe_to_simulation_events` 建立仿真事件订阅。
5. `create_simulation_task` 创建仿真任务。
6. `get_task_step` 轮询当前步数、状态和轻量事件摘要。
7. 仅在失败或需要完整状态时调用 `get_task_status`。
8. `get_timeseries_data` 启动结果导出。
9. `get_export_status` 轮询导出状态，等待 `COMPLETED`。
10. 使用返回的 `resource_uri` 通过 MCP `resources/read` 读取结果。
11. 基于真实结果生成图表和 HTML 报告。

## 结果读取规则

当前推荐结果 URI 形式：

```text
hydroengine://downloads/<file_name>.xlsx
```

处理规则：

- `get_export_status` 未返回 `COMPLETED` 前，不要下载或生成报告。
- 如果返回 `resource_uri`，优先通过 MCP `resources/read` 读取。
- 不要默认把 HTTPS `/s3/...` 地址当作可直接下载文件；这类地址可能要求 JWT，MCP token 未必能访问。
- 如果 `resources/read` 返回 workbook 文本或 Markdown 表格，可先落盘或解析后再生成报告。

## 常见错误

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `404 Not Found` | 仍在使用旧 `/mcps/...` 路径 | 改用 `https://mcp.hydroos.pub/` |
| `406 Not Acceptable` | 缺少 `Accept` 或 `Content-Type` | 添加 `Accept: application/json, text/event-stream` 和 `Content-Type: application/json` |
| `401 Unauthorized` | token 缺失、过期或 Header 不完整 | 检查 `Authorization`、`Execution-Source`、`Production-Code` |
| `32602` | JSON-RPC 参数缺失或字段名错误 | 检查工具 schema，尤其是 `biz_scene_instance_id`、`sse_client_id` |
| 结果 HTTPS 下载提示未认证 | `/s3/...` 需要 JWT | 改用 `resource_uri` + `resources/read` |
| 仿真步进超时 | 步长设置过细或场景计算压力过大 | 优先使用场景默认参数，避免过小 `step_resolution` |
| `get_export_status` 返回 `FAILED` | 导出链路失败 | 停止后续下载和报告生成，报告失败原因 |

## Token 检查

如果未配置 token，应直接报告 token 缺失并停止调用 MCP。不要把空 token、示例 token 或历史 token 写入文档或日志。

推荐环境变量：

```bash
export MCP_TOKEN="<your-token>"
```

OpenHands 文档也可以使用：

```bash
export HYDROS_API_TOKEN="<your-token>"
```

## 推荐排查顺序

1. 确认配置 URL 是 `https://mcp.hydroos.pub/`。
2. 确认 token 环境变量存在且客户端实际加载。
3. 确认 Header 包含 `Execution-Source`、`Production-Code`、`Accept`。
4. 调 `initialize`。
5. 调 `tools/list`。
6. 调 `biz_scenario_id_lists`。
7. 再进入仿真任务链路。
