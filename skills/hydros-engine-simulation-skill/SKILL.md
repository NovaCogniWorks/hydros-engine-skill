---
name: hydros-simulation
description: |
  水力仿真引擎全流程编排工具。通过调用 hydro-engine MCP 服务（tool namespace: hydro_engine_mcp）完成场景查询、仿真任务创建、进度跟踪、时序结果导出与读取、异常分析、图表输出，并在需要时生成交互式分析工作台、HTML 汇报报告、Markdown 报告、拓扑可视化页或渠道纵剖面页。
  当用户提到以下任何内容时触发此 skill：水力仿真、水力仿真分析、仿真场景、仿真任务、水位分析、流量分析、闸门控制、渠道仿真、京石段、hydros、仿真引擎、创建仿真、运行仿真、仿真结果、时序数据分析、水力报告、仿真进度、场景列表、仿真可视化、交互式分析工作台、HTML 仪表板、HTML 报告、Markdown 报告、结果工作台、拓扑图、拓扑可视化、场景拓扑、渠道拓扑、水网拓扑、纵剖面、纵剖面图、水面线、上游下游流向、闸站展示。
  即使用户只是模糊地说“跑一下仿真”“看看水位数据”“分析一下结果”“做个页面看仿真数据”“获取场景拓扑”“画一个纵剖面”，也应该触发此 skill。
---

# Hydros Engine 水力仿真 Skill

## 核心职责

编排 hydros 仿真的完整流程，并在结果阶段提供图表、异常分析、HTML 汇报报告、Markdown 报告、交互式分析工作台、场景拓扑可视化和渠道纵剖面展示支持。

## 沟通与硬规则

- 始终使用中文与用户沟通，技术术语和代码标识保持原文。
- 在调用 `create_simulation_task` 前，必须先调用 `subscribe_to_simulation_events` 建立 SSE 事件订阅通道。
- `biz_scenario_id` 和 `biz_scenario_config_url` 必须成对使用，并且只能来自 `biz_scenario_id_lists` 的返回结果。
- 只要创建的是 live 仿真任务，就不能在“任务已创建”或“出现首条进度”后停止；必须持续监测到终态（`COMPLETED` 或 `FAILED`）。
- 在调用 `get_timeseries_data` 前，必须先确认任务状态为 `COMPLETED`。
- `get_timeseries_data` 返回的是 `resource_uri`；如果后续要给本地脚本消费，必须继续调用 `read_mcp_resource(server=\"hydro-engine-mcp\", uri=resource_uri)` 读取 CSV 文本，再落成本地 `.csv` 文件。
- 如果用户要做交互式分析工作台、HTML 报告或其他 HTML 页面，先读 [references/hydros-html-prompt.md](references/hydros-html-prompt.md)。
- 如果需要理解数据结构、聚合口径或指标映射，先读 [references/hydros-data-contract.md](references/hydros-data-contract.md)。
- 如果需要快速交付一个可直接打开的页面，优先复用模板资产，而不是从零开始。
- 需要交互式分析工作台时，优先复用 [assets/hydros-dashboard-template/index.html](assets/hydros-dashboard-template/index.html)。
- 需要 HTML 汇报报告、完整曲线展示或外部 payload 驱动页面时，优先复用 [assets/hydros-report-template/index.html](assets/hydros-report-template/index.html) 和同目录 `report.data.js`。

## 资源导航

- `scripts/generate_charts.py`
  用于生成 matplotlib 图表。
- `scripts/analyze_anomalies.py`
  用于异常检测和问题汇总。
- `scripts/build_csv_report.py`
  用于把本地 CSV 结果快速整理成 HTML 报告和 Markdown 报告；若可获取断面里程与底高程，还会默认附带渠道纵剖面图。输出目录默认按 `report/`、`charts/`、`data/` 分类。
- `scripts/build_csv_dashboard.py`
  用于把本地 CSV 结果快速整理成交互式分析工作台。
- `scripts/build_longitudinal_profile.py`
  用于根据 `objects.yaml` 断面信息和时序结果生成渠道纵剖面 HTML 页面，并可叠加闸站信息和上游到下游流向标识。高程字段优先读取显式的 `t_top_elevation` 和 `bottom_elevation`；若缺失，再根据 `cross_section_geometry.data_points` 的最大值和最小值推导。
- `references/hydros-data-contract.md`
  用于理解时序记录结构、推荐聚合口径、异常信号定义。
- `references/hydros-html-prompt.md`
  用于生成 HTML 页面提示词规范、报告页规格、拓扑页/纵剖面页规格或页面实现约束。
- `assets/hydros-dashboard-template/index.html`
  用于快速起一个单文件交互式分析工作台。
- `assets/hydros-report-template/index.html`
  用于快速起一个单页 HTML 汇报报告，适合汇报、截图、归档和真实结果复盘；报告默认支持纵剖面与时序曲线联动、播放、拖拽、暂停和继续，主要图表的 y 轴应根据当前数据范围自适应缩放。纵剖面需要同时展示断面顶高程、底高程和水面线，其中水位阴影只填充到底高程线，底高程阴影才延伸到坐标轴底部；闸站位置优先用稳定的虚线标识，避免播放时出现抖动。
- `assets/hydros-report-template/report.data.js`
  用于承载报告模板的外部数据 payload，替换后即可接入真实仿真结果。

## 五阶段工作流

### 阶段一：建立 SSE 事件订阅

1. 生成 UUID 作为 `sse_client_id`。
2. 调用 `subscribe_to_simulation_events(sse_client_id)`。
3. 确认返回 `success: true`。
4. 向用户解释：`sse_client_id` 绑定 SSE 事件订阅通道，后续创建任务、跟踪进度都依赖它。

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
5. 如果这是 live 任务，创建成功后立即进入阶段四持续监测，直到任务进入 `COMPLETED` 或 `FAILED`，不要在首个进度点就结束本轮处理。

异常处理：
- `SSE通道未建立`：重新订阅后重试。
- `tenant is null` / `NullPointerException`：提示用户提供正确的 `tenant_id`。
- `valid: false`：提示检查 tenant、user、场景配置是否匹配。

### 阶段四：跟踪仿真进度

可组合使用两种方式：

1. `get_task_status(sse_client_id, biz_scene_instance_id)`
   用于拿到任务当前状态和总步数。
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
- 对 live 任务，默认持续轮询 `get_task_status` 和 `fetch_sse_events`，直到捕获终态；在终态前不要把流程当作完成。
- 如果用户的意图是“跑一个仿真并看结果/出报告/继续等待”，则任务完成后应自动衔接阶段五，无需再次等待用户提醒。
- 如果运行环境是命令行 PTY，而不是聊天消息流，优先用单行进度条展示，如 `██████░░░░░░ 34% | 408/1200`，通过 `\\r` 原地刷新；若当前环境只能追加消息，则退化为节流后的状态输出，不能假装实现原地覆盖。

### 阶段五：获取结果与分析

1. 确认任务状态为 `COMPLETED`。
2. 调用 `get_timeseries_data(biz_scene_instance_id)`，或直接读取用户已有的本地结果文件。
   - 如果返回 `resource_uri`，继续调用 `read_mcp_resource(server="hydro-engine-mcp", uri=resource_uri)` 获取完整 CSV 文本。
   - 如果后续要调用 `scripts/build_csv_report.py`、`scripts/build_csv_dashboard.py` 或其他本地脚本，先把读取到的 CSV 文本落盘为本地 `.csv` 文件，再把该路径传给脚本。
3. 生成统计摘要：
   - 总记录数
   - 采样步数
   - 对象数
   - 指标数
   - 异常点数量
4. 用 `scripts/generate_charts.py` 生成水位、流量、热力图等图表，保存为 PNG 文件。
5. 用 `scripts/analyze_anomalies.py` 生成异常结论。
6. 需要报告时，默认同时产出 HTML 报告和 Markdown 报告；仅当用户明确要求正式文档时，再补充 Word 报告。
   - HTML 报告和 Markdown 报告是默认必要产物，除非用户明确只要其中一种。
   - 如果可获取 `objects.yaml` 中的断面里程与底高程，HTML 报告和 Markdown 报告应默认附带渠道纵剖面图；如果用户明确要单独纵剖面页，再额外输出独立页面。
   - 每次报告输出都要按目录分类：`report/` 存放 HTML 和 Markdown，`charts/` 存放 PNG 图表，`data/` 存放 `report.data.js`、统计摘要和其他中间数据。
   - 图表展示时，不要把 y 轴固定在过大的全局范围；应根据当前图表中的实际数值自动收紧范围，避免水位等小幅波动被压扁。
   - 完整曲线区块不能只放图；`water_level`、`water_flow`、`gate_opening` 都要配套文字解读，解释波动范围、重点对象、控制动作和建议关注点。
   - 对完整曲线中的占位零值要单独识别，尤其是 `water_level`、`water_flow` 的首步占位值；展示和解读时要剔除或明确说明，不能把这类占位值误判为真实异常或真实停流。
   - Markdown 报告必须是图文并茂的，用 `![描述](相对路径)` 嵌入 generate_charts.py 生成的 PNG 图表。每张图表前后都应有对应的文字分析，解释图中的关键发现和趋势，而不是只列数据表格。
   - 图表嵌入顺序建议：水位时序图 → 流量时序图 → 闸门开度图 → 分水口流量图 → 热力图，每张图后紧跟 2-3 句分析说明。
7. 需要 HTML 页面时，读取 HTML 参考并复用模板资产。
8. 如果用户明确要“报告页”“汇报页”“导出截图”或“完整曲线”，优先选报告模板；如果用户明确要筛选、联动、钻取，优先选工作台模板。
   - 使用报告模板时，如果既有纵剖面数据也有时序结果，页面应默认提供统一时间轴，让纵剖面、水位、流量、闸门开度按同一步号联动，并支持播放、拖拽、暂停、继续。
   - 报告中的 `Task Snapshot` 要尽量补全任务元信息：开始时间、结束时间、时间步长、输出步长、仿真时长、场景 YAML ID、任务状态等；能从场景 YAML 和 CSV 计算的要直接计算，缺失时再明确标注“无法推导”。
   - 时间口径默认按“`data_index` 是计算步号”处理；若没有更可靠的步长信息，按 `120 秒/步` 推导。也就是说，`data_index=1200` 表示第 1200 个计算步，若输出步长为 `60`，则代表每 `60` 个计算步输出一次结果。
   - 用户可见的任务状态统一使用中文，例如 `COMPLETED` 展示为“已完成”。
   - “后续动作”区块统一命名为“后续建议动作”。
9. 报告模板默认采用 `index.html + report.data.js` 分离模式，避免把真实数据直接硬编码进 HTML。
10. 如果用户明确要“拓扑图”“场景拓扑”“水网拓扑”或“渠道拓扑”，优先读取场景 YAML 和 `hydros_objects_modeling_url` 指向的 `objects.yaml`，整理 `connections`、对象类型和关键节点关系，生成 HTML 拓扑可视化页。
11. 如果用户明确要“纵剖面”“纵剖面图”或“水面线”，优先基于 `objects.yaml` 中断面的 `location`、`bottom_elevation` 叠加时序结果中的水位（`water_level`）生成渠道纵剖面 HTML 页面。
   - 高程字段优先读取显式的 `t_top_elevation` 和 `bottom_elevation`；如果缺失，再根据 `cross_section_geometry.data_points` 的最大值和最小值推导顶高程与底高程。
   - 纵剖面的 x 轴和 y 轴都应根据当前展示数据的实际数值范围自适应收紧，不要使用过宽的固定范围。
12. 如果用户要求“增加闸站展示”“展示水流流向”，在纵剖面页中额外展示闸站卡片、闸门组成，以及“上游 -> 下游”的流向标识。

## 快捷入口

| 用户意图 | 动作 |
| --- | --- |
| “连接仿真引擎” / “建立 SSE” | 执行阶段一 |
| “列出场景” | 执行阶段一到阶段二 |
| “跑一个仿真” | 执行阶段一到阶段四；若用户同时关心结果、报告或明确要求等待完成，则继续自动执行阶段五 |
| “查看进度” / “任务状态” | 执行阶段四 |
| “拉取结果” / “分析数据” | 执行阶段五 |
| “生成报告” | 执行阶段五，默认输出 HTML 报告和 Markdown 报告 |
| “做个页面看仿真数据” / “做 HTML 仪表板” | 先执行阶段五拿到数据，再读 references 并复用交互式分析工作台模板 |
| “出一份 HTML 报告” / “做汇报页” / “看完整曲线” | 先执行阶段五拿到数据，再读 references 并复用 HTML 汇报报告模板 |
| “获取拓扑” / “场景拓扑” / “渠道拓扑” | 读取场景配置和 `objects.yaml`，输出 HTML 拓扑可视化页 |
| “画一个纵剖面” / “做纵剖面页” | 基于断面里程、底高程和水位结果生成渠道纵剖面 HTML |
| “增加闸站展示” / “展示水流流向” | 在纵剖面页中增强闸站信息和上游到下游流向标识 |
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
