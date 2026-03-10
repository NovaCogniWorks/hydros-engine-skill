# hydros-engine-skill

集成 hydros-engine 的 MCP Skill，为 Claude Code 提供水力仿真引擎的交互能力。

## 功能概述

本 Skill 通过 MCP（Model Context Protocol）协议与 hydros-engine 仿真引擎对接，提供以下四大核心能力：

---

### 1. 获取场景清单

从 hydros-engine 获取可用的仿真场景列表。

**工具定义：** `list_scenarios`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| project_id | string | 否 | 项目 ID，不传则返回所有场景 |
| status | string | 否 | 过滤条件：`ready` / `running` / `completed` |

**返回数据结构：**

```json
{
  "scenarios": [
    {
      "id": "scenario_001",
      "name": "城市供水管网稳态分析",
      "description": "某市主干管网在高峰时段的水力工况",
      "status": "ready",
      "created_at": "2026-03-10T08:00:00Z",
      "network_summary": {
        "node_count": 120,
        "pipe_count": 150,
        "pump_count": 5,
        "valve_count": 12
      }
    }
  ],
  "total": 1
}
```

**典型交互流程：**
1. 用户请求查看可用场景
2. Skill 调用 hydros-engine API 获取场景清单
3. 返回格式化的场景列表供用户选择

---

### 2. 创建仿真任务

基于选定场景创建并提交仿真计算任务。

**工具定义：** `create_simulation`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| scenario_id | string | 是 | 场景 ID |
| simulation_type | string | 是 | 仿真类型：`steady_state`（稳态）/ `extended_period`（延时） |
| duration | number | 否 | 仿真时长（小时），延时仿真必填 |
| time_step | number | 否 | 时间步长（秒），默认 3600 |
| parameters | object | 否 | 额外仿真参数覆盖 |

**返回数据结构：**

```json
{
  "task_id": "task_abc123",
  "scenario_id": "scenario_001",
  "simulation_type": "extended_period",
  "status": "submitted",
  "created_at": "2026-03-10T09:00:00Z",
  "estimated_duration_seconds": 30
}
```

**典型交互流程：**
1. 用户选定场景并指定仿真参数
2. Skill 向 hydros-engine 提交仿真任务
3. 返回 `task_id`，随后通过 SSE 跟踪进度

---

### 3. SSE 连接与实时进度推送

建立 SSE（Server-Sent Events）连接，从 hydros-engine 实时接收仿真状态，经过滤处理后向调用端推送。

**架构：**

```
调用端(Claude) <-- SSE推送 -- [Skill SSE Client] <-- SSE连接 -- hydros-engine
```

**工具定义：** `subscribe_task_progress`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| task_id | string | 是 | 仿真任务 ID |

**SSE 消息类型与过滤规则：**

| Engine 原始事件 | 过滤动作 | 推送给调用端的事件 |
|----------------|---------|------------------|
| `heartbeat` | 丢弃 | — |
| `progress` | 提取百分比 + 当前步骤 | `progress` |
| `warning` | 记录日志，不中断 | `warning`（可选推送） |
| `completed` | 提取结果摘要 | `completed` |
| `error` / `failed` | 提取错误信息 | `failed` |

**推送消息格式：**

```json
// progress
{
  "event": "progress",
  "task_id": "task_abc123",
  "percentage": 45,
  "current_step": "Computing hydraulic balance at time 12:00",
  "elapsed_seconds": 12
}

// completed
{
  "event": "completed",
  "task_id": "task_abc123",
  "result_summary": {
    "total_time_steps": 24,
    "convergence": true,
    "max_iterations": 8,
    "warnings_count": 2
  }
}

// failed
{
  "event": "failed",
  "task_id": "task_abc123",
  "error_code": "DIVERGENCE",
  "error_message": "Hydraulic solver failed to converge at time step 15"
}
```

**关键设计要点：**
- SSE Client 需实现自动重连（指数退避策略）
- heartbeat 事件仅用于保活，不推送给调用端
- progress 事件做节流处理（如每 5% 推送一次），避免消息风暴
- completed/failed 为终态事件，收到后关闭 SSE 连接

---

### 4. 查询水网对象时序数据

仿真完成后，查询指定水网对象（节点/管道/水泵/阀门）的时序结果数据，并调用相关工具生成图表、分析数据准确性。

**工具定义：** `query_timeseries`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| task_id | string | 是 | 仿真任务 ID |
| object_id | string | 是 | 水网对象 ID（如 `node_J1`, `pipe_P12`） |
| object_type | string | 是 | 对象类型：`node` / `pipe` / `pump` / `valve` |
| parameters | string[] | 是 | 查询参数列表（见下表） |
| time_range | object | 否 | 时间范围过滤 `{ start: number, end: number }`（小时） |

**可查询参数：**

| 对象类型 | 可查询参数 |
|---------|-----------|
| node | `pressure`（压力）, `head`（水头）, `demand`（需水量）, `quality`（水质） |
| pipe | `flow`（流量）, `velocity`（流速）, `headloss`（水头损失）, `status`（状态） |
| pump | `flow`（流量）, `head_gain`（扬程）, `power`（功率）, `efficiency`（效率）, `status` |
| valve | `flow`（流量）, `headloss`（水头损失）, `status`（状态）, `setting`（设定值） |

**返回数据结构：**

```json
{
  "task_id": "task_abc123",
  "object_id": "node_J1",
  "object_type": "node",
  "timeseries": {
    "timestamps": [0, 1, 2, 3, 4, 5],
    "pressure": [32.5, 31.8, 30.2, 28.9, 29.5, 31.0],
    "head": [82.5, 81.8, 80.2, 78.9, 79.5, 81.0]
  },
  "units": {
    "timestamps": "hours",
    "pressure": "m",
    "head": "m"
  },
  "statistics": {
    "pressure": { "min": 28.9, "max": 32.5, "mean": 30.65, "std": 1.32 },
    "head": { "min": 78.9, "max": 82.5, "mean": 80.65, "std": 1.32 }
  }
}
```

**图表生成与数据分析流程：**

1. **获取时序数据** — 调用 `query_timeseries` 获取原始数据
2. **生成可视化图表** — 利用图表工具（如 canvas/前端组件）绘制：
   - 时序曲线图（压力/流量随时间变化）
   - 多对象对比图（多个节点压力对比）
   - 统计分布图（参数的 min/max/mean 范围）
3. **数据准确性分析** — 自动检测以下异常：
   - 负压检测：节点压力 < 0，标记为供水不足风险
   - 流速异常：管道流速 > 3 m/s 或 < 0.3 m/s
   - 水头损失梯度异常：单位长度水头损失超出合理范围
   - 收敛性评估：检查各时间步迭代次数
4. **生成分析报告** — 汇总异常点、提出优化建议
5. **推送结果** — 将图表和分析报告推送至调用端

---

## 整体工作流

```
用户请求
  │
  ▼
┌─────────────────┐
│ 1. list_scenarios│  获取场景清单
└────────┬────────┘
         │ 用户选择场景
         ▼
┌─────────────────────┐
│ 2. create_simulation │  创建仿真任务
└────────┬────────────┘
         │ 返回 task_id
         ▼
┌──────────────────────────┐
│ 3. subscribe_task_progress│  SSE 实时进度
│    Engine ──SSE──► Skill  │
│    Skill  ──推送──► 调用端 │
└────────┬─────────────────┘
         │ 仿真完成
         ▼
┌─────────────────────┐
│ 4. query_timeseries  │  查询时序数据
│    → 生成图表        │
│    → 分析准确性      │
│    → 推送结果        │
└─────────────────────┘
```

## 技术栈

- **协议层：** MCP（Model Context Protocol）
- **通信方式：** SSE（Server-Sent Events）用于实时推送
- **仿真引擎：** hydros-engine（水力仿真计算核心）
- **数据格式：** JSON

## 项目结构（规划）

```
hydros-engine-skill/
├── README.md
├── skill.json              # Skill 元数据定义
├── src/
│   ├── index.ts            # 入口，注册 MCP 工具
│   ├── tools/
│   │   ├── list_scenarios.ts
│   │   ├── create_simulation.ts
│   │   ├── subscribe_progress.ts
│   │   └── query_timeseries.ts
│   ├── sse/
│   │   ├── client.ts       # SSE Client（连接 Engine）
│   │   ├── filter.ts       # 消息过滤与转换
│   │   └── reconnect.ts    # 自动重连策略
│   ├── analysis/
│   │   ├── anomaly.ts      # 异常检测
│   │   └── report.ts       # 分析报告生成
│   └── types/
│       └── index.ts        # 类型定义
├── package.json
└── tsconfig.json
```
