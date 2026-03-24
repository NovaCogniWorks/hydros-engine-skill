---
name: hydros-simulation
description: |
  水力仿真引擎全流程编排工具。通过调用 hydro-engine MCP 服务完成场景查询、仿真任务创建、进度跟踪、结果拉取、异常分析、图表/报告输出，并在需要时生成友好的 HTML 分析界面或可视化工作台。
  当用户提到以下任何内容时触发此 skill：水力仿真、水动力学、仿真场景、仿真任务、水位分析、流量分析、闸门控制、渠道仿真、京石段、hydros、仿真引擎、创建仿真、运行仿真、仿真结果、时序数据分析、水力报告、仿真进度、场景列表、仿真可视化、分析面板、HTML 仪表板、结果工作台。
  即使用户只是模糊地说“跑一下仿真”“看看水位数据”“分析一下结果”“做个页面看仿真数据”，也应该触发此 skill。
---

# Hydros Engine 水力仿真 Skill

## 核心职责

编排 hydros 仿真的完整流程，并在结果阶段提供图表、异常分析、报告和 HTML 分析界面支持。

## 沟通与硬规则

- 始终使用中文与用户沟通，技术术语和代码标识保持原文。
- 在调用 `create_simulation_task` 前，必须先调用 `subscribe_to_simulation_events` 建立 SSE 通道。
- `biz_scenario_id` 和 `biz_scenario_config_url` 必须成对使用，并且只能来自 `biz_scenario_id_lists` 的返回结果。
- 在调用 `get_timeseries_data` 前，必须先确认任务状态为 `COMPLETED`。
- 如果用户要做 HTML 仪表板、结果工作台或分析页面，先读 [references/hydros-html-prompt.md](references/hydros-html-prompt.md)。
- 如果需要理解数据结构、聚合口径或指标映射，先读 [references/hydros-data-contract.md](references/hydros-data-contract.md)。
- 如果需要快速交付一个可直接打开的页面，优先复用模板资产，而不是从零开始。
- 需要交互式分析工作台时，优先复用 [assets/hydros-dashboard-template/index.html](assets/hydros-dashboard-template/index.html)。
- 需要可汇报的单页结果报告、完整曲线展示或外部 payload 驱动页面时，优先复用 [assets/hydros-report-template/index.html](assets/hydros-report-template/index.html) 和同目录 `report.data.js`。

## 资源导航

- `scripts/generate_charts.py`
  用于生成 matplotlib 图表。
- `scripts/analyze_anomalies.py`
  用于异常检测和问题汇总。
- `references/hydros-data-contract.md`
  用于理解时序记录结构、推荐聚合口径、异常信号定义。
- `references/hydros-html-prompt.md`
  用于生成友好的 HTML 分析界面 prompt、报告页规格或页面实现约束。
- `assets/hydros-dashboard-template/index.html`
  用于快速起一个单文件分析工作台。
- `assets/hydros-report-template/index.html`
  用于快速起一个单页分析报告，适合汇报、截图、归档和真实结果复盘。
- `assets/hydros-report-template/report.data.js`
  用于承载报告模板的外部数据 payload，替换后即可接入真实仿真结果。

## 五阶段工作流

### 阶段一：建立 SSE 连接

1. 生成 UUID 作为 `sse_client_id`。
2. 调用 `subscribe_to_simulation_events(sse_client_id)`。
3. 确认返回 `success: true`。
4. 向用户解释：`sse_client_id` 绑定 SSE 通道，后续创建任务、跟踪进度都依赖它。

异常处理：
- 连接失败时，提示用户检查 `hydro-engine` MCP 服务。
- 如果后续报 “SSE通道未建立”，用同一个 `sse_client_id` 重新订阅。

### 阶段二：查询与选择场景

1. 调用 `biz_scenario_id_lists(sse_client_id)`。
2. 将场景整理为 markdown 表格，至少包含：序号、场景 ID、场景名称、核心能力。
3. 保存每个场景的 `biz_scenario_config_url`，后续创建任务时必须使用。
4. 给出推荐场景，优先描述中包含“测试”或“SDK”的场景，其次选依赖较少的场景。

异常处理：
- `401 ACCESS_UNAUTHORIZED`：提示用户检查认证。
- 空列表：提示用户检查引擎是否注册了场景。

### 阶段三：创建仿真任务

必须收集：
- `tenant_id`
- `biz_scenario_id`
- `biz_scenario_config_url`
- `sse_client_id`

执行步骤：
1. 如有必要，先重新确认 SSE 连接有效。
2. 向用户确认 `tenant_id`，这是最容易出错的参数。
3. 调用 `create_simulation_task`。
4. 保存并展示：
   - `biz_scene_instance_id`
   - `task_status`
   - `total_steps`
   - `default_render_objects`
   - `valid`

异常处理：
- `SSE通道未建立`：重新订阅后重试。
- `tenant is null` / `NullPointerException`：提示用户提供正确的 `tenant_id`。
- `valid: false`：提示检查 tenant、user、场景配置是否匹配。

### 阶段四：跟踪仿真进度

可组合使用两种方式：

1. `get_task_status(sse_client_id, biz_scene_instance_id)`
   用于拿到任务当前状态和步数。
2. `fetch_sse_events(sse_client_id)`
   用于拿到增量事件流并整理成时间线。

状态流转：

```text
INIT -> WAITING_AGENTS -> READY -> STEPPING -> COMPLETED
                                             -> FAILED
```

展示要求：
- 对 `STEPPING` 状态展示 `current_step / total_steps` 和百分比。
- 对 `FAILED` 状态优先提取 `failure_exception`。
- 对 SSE 事件整理为时间线，突出状态切换和关键进度节点。

### 阶段五：获取结果与分析

1. 确认任务状态为 `COMPLETED`。
2. 调用 `get_timeseries_data(biz_scene_instance_id)`，或直接读取用户已有的本地结果文件。
3. 生成统计摘要：
   - 总记录数
   - 时间步数
   - 对象数
   - 指标数
   - 异常记录数
4. 用 `scripts/generate_charts.py` 生成水位、流量、热力图等图表，保存为 PNG 文件。
5. 用 `scripts/analyze_anomalies.py` 生成异常结论。
6. 需要报告时，优先生成 Word；若不具备条件，生成 Markdown。
   - Markdown 报告必须是图文并茂的，用 `![描述](相对路径)` 嵌入 generate_charts.py 生成的 PNG 图表。每张图表前后都应有对应的文字分析，解释图中的关键发现和趋势，而不是只列数据表格。
   - 图表嵌入顺序建议：水位时序图 → 流量时序图 → 闸门开度图 → 分水口流量图 → 热力图，每张图后紧跟 2-3 句分析说明。
7. 需要友好可视化界面时，读取 HTML 参考并复用模板资产。
8. 如果用户明确要”报告页””汇报页””导出截图”或”完整曲线”，优先选报告模板；如果用户明确要筛选、联动、钻取，优先选工作台模板。
9. 报告模板默认采用 `index.html + report.data.js` 分离模式，避免把真实数据直接硬编码进 HTML。

## 快捷入口

| 用户意图 | 动作 |
| --- | --- |
| “连接仿真引擎” / “建立 SSE” | 执行阶段一 |
| “列出场景” | 执行阶段一到阶段二 |
| “跑一个仿真” | 执行阶段一到阶段三 |
| “查看进度” / “任务状态” | 执行阶段四 |
| “拉取结果” / “分析数据” | 执行阶段五 |
| “生成报告” | 执行阶段五，优先报告输出 |
| “做个页面看仿真数据” / “做 HTML 仪表板” | 先执行阶段五拿到数据，再读 references 并复用工作台模板 |
| “出一份 HTML 报告” / “做汇报页” / “看完整曲线” | 先执行阶段五拿到数据，再读 references 并复用报告模板 |
| “仿真失败了” | 进入阶段四，先查失败原因 |

如果用户直接要求后续阶段但缺少前置状态，先补齐前置信息或从前置阶段开始。

## 常见失败原因

- 初始化超时：部分智能体未上线，建议换场景或检查智能体服务。
- NullPointerException：多半是 `tenant_id` 错误。
- 智能体注册失败：场景依赖的服务不可用。
- 输出为空：提示用户检查 `default_render_objects` 是否为空。

## 会话状态

始终维护以下状态：

```text
sse_client_id
tenant_id
biz_scenario_id
biz_scenario_config_url
biz_scene_instance_id
task_status
total_steps
default_render_objects
```
