# Hydro Engine MCP 连接指南

本文档提供详细的 MCP 连接排查和配置指南。

## 连接方式

### 正确连接方式

- **URL**: `https://hydroos.cn/mcps/hydros-engine-executor`
- **协议**: `JSON-RPC 2.0 over HTTP POST`
- **必需 Header**:
  - `Authorization: Bearer <token>`
  - `Content-Type: application/json`
  - `Execution-Source: codex`
  - `Production-Code: copaw`
  - `Accept: application/json,text/event-stream`

说明：
- `hydros-engine-executor` 是标准 MCP 服务名，不要误写成 `hydro-engine-mcp`
- 配置里的 URL 不要带尾部空格

### 标准工作流

1. `initialize` - 初始化 MCP 连接
2. `subscribe_to_simulation_events` - 订阅仿真事件
3. 业务工具调用 - 执行具体的仿真操作

## 连接排查

### HTTP 直连排查

当需要排查连接问题时，使用以下配置：

```bash
curl -X POST https://hydroos.cn/mcps/hydros-engine-executor \
  -H "Authorization: Bearer <token>" \
  -H "Execution-Source: codex" \
  -H "Production-Code: copaw" \
  -H "Accept: application/json,text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}'
```

**注意事项**：
- 只排查 MCP 入口 `https://hydroos.cn/mcps/hydros-engine-executor`
- 不要误打业务网页或猜测式 REST 路径（如 `https://hydroos.cn/api/scenario/lists`）
- 这类业务地址通常返回 HTML，不是可用的 JSON/MCP 响应

### 常见错误及解决方案

| 错误码/现象 | 原因 | 解决方案 |
|------------|------|---------|
| `406 Not Acceptable` | 缺少 `Content-Type` 或 `Accept` header | 确保请求包含 `Content-Type: application/json` 和 `Accept: application/json,text/event-stream` |
| `401 Unauthorized` | Token 或业务 Header 缺失 | 检查 `Authorization`、`Execution-Source`、`Production-Code` 是否完整且值正确 |
| `32602` | 参数缺失 | 检查是否漏传 `sse_client_id` 等必需参数 |
| 返回 HTML | URL 错误 | 使用 `/mcp` 端点，不是 `/api/xxx` |
| 连接超时 | 使用了不兼容的客户端库 | 避免使用 SSE 客户端库做初始化，使用标准 HTTP POST |

## 连接避坑指南

### 错误方式 1：使用 SSE 客户端库直连

**问题**：使用 SSE 客户端库直接连 `https://hydroos.cn/mcps/hydros-engine-executor` 做初始化探测，容易卡住或超时。

**原因**：MCP 初始化使用 JSON-RPC 2.0 over HTTP POST，不是 SSE stream。

**正确方式**：优先使用已安装的 `hydros-engine-executor` 工具，并先调用 `list_mcp_resource_templates` 做轻量握手检查。

### 错误方式 2：误用业务 API 路径

**问题**：误用 `https://hydroos.cn/api/scenario/lists` 这类路径，返回的通常是 HTML 页面。

**原因**：这些是业务网页路径，不是 MCP 端点。

**正确方式**：使用 skill 中定义的 MCP 工具，如 `biz_scenario_id_lists`、`get_scenario_events`。

### 错误方式 3：缺少必需 Header

**问题**：直连 `https://hydroos.cn/mcps/hydros-engine-executor` 时缺少 `Accept: application/json,text/event-stream`，返回 `406 Not Acceptable`。

**原因**：服务端需要明确的 Accept header 来确定响应格式。

**正确方式**：带齐 `Authorization`、`Execution-Source`、`Production-Code`、`Content-Type`、`Accept` 后再排查。

## Token 配置

### 获取 Token

1. 访问 `https://hydroos.cn/playground/`
2. 完成注册或登录
3. 在"账号管理"中获取 API token
4. 将 token 配置到 `Authorization: Bearer <token>`
5. 同时保留业务 Header：`Execution-Source: codex`、`Production-Code: copaw`

### Token 验证

如果用户尚未配置 token（`Authorization token: ""`），应：
1. 直接报告 token 缺失
2. 停止后续步骤
3. 引导用户按上述流程获取 token

## 推荐连接流程

1. **检查 MCP 安装**：确认 `hydros-engine-executor` 已安装并可连通
2. **轻量探测**：调用 `list_mcp_resource_templates(server="hydros-engine-executor")` 确认握手正常
3. **使用工具链**：优先走已安装的 `hydros-engine-executor` 工具链，避免临时直连
4. **仅在排查时直连**：只在需要排查问题时才使用 HTTP 直连，且必须带齐必需 Header
