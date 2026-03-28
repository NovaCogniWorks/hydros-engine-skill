---
name: hydros-simulation
description: |
  水力仿真引擎全流程编排工具。通过调用 hydro-engine MCP 服务（tool namespace: hydro_engine_mcp）完成场景查询、仿真任务创建、进度跟踪、时序结果导出与读取、异常分析、图表输出，并在需要时生成交互式分析工作台、HTML 汇报报告、Markdown 报告、拓扑可视化页或渠道纵剖面页。
  当用户提到以下任何内容时触发此 skill：水力仿真、水力仿真分析、仿真场景、仿真任务、水位分析、流量分析、闸门控制、渠道仿真、京石段、hydros、仿真引擎、创建仿真、运行仿真、仿真结果、时序数据分析、水力报告、仿真进度、场景列表、仿真可视化、交互式分析工作台、HTML 仪表板、HTML 报告、Markdown 报告、结果工作台、拓扑图、拓扑可视化、场景拓扑、渠道拓扑、水网拓扑、纵剖面、纵剖面图、水面线、上游下游流向、闸站展示。
  即使用户只是模糊地说“跑一下仿真”“看看水位数据”“分析一下结果”“做个页面看仿真数据”“获取场景拓扑”“画一个纵剖面”，也应该触发此 skill。
---

# Hydros Engine 水力仿真 Skill

## 初始条件

- 在读取“核心职责”或进入任何仿真流程前，先检查 `hydro-engine-mcp` 是否已安装并可连通。
- 优先调用 `list_mcp_resource_templates(server="hydro-engine-mcp")` 或等价轻量探测确认 MCP 握手正常。
- 如果需要做 HTTP 直连排查，使用：
  - URL: `https://hydroos.cn/mcp`
  - Header `Accept: application/json, text/event-stream`
  - Header `Authorization: Bearer <token>`
- 如果用户尚未配置 token，则按 `Authorization token: ""` 视为未配置，直接报告缺失并停止后续步骤。
- 当 token 缺失或 MCP 未安装时，明确提示用户：
  - 先访问 `https://hydroos.cn/playground/`
  - 完成注册或登录
  - 在“账号管理”中获取 API token
  - 将该 token 配置到 `Authorization: Bearer <token>` 后再继续
- 只有在上述检查通过后，才允许继续执行本 skill 的任何后续工作流。

## 核心职责

编排 hydros 仿真的完整流程，并在结果阶段提供图表、异常分析、HTML 汇报报告、Markdown 报告、交互式分析工作台、场景拓扑可视化和渠道纵剖面展示支持。

## 沟通与硬规则

- 始终使用中文与用户沟通，技术术语和代码标识保持原文。
- 在调用 `create_simulation_task` 前，必须先调用 `subscribe_to_simulation_events` 建立 SSE 事件订阅通道。
- `biz_scenario_id` 和 `biz_scenario_config_url` 必须成对使用，并且只能来自 `biz_scenario_id_lists` 的返回结果。
- 当用户只是回复场景 ID、场景名称，或说“就这个”“选这个”时，只能视为“选定场景”，不能直接视为“接受默认参数并立即启动”；必须先展示该场景的默认 `total_steps`、`sim_step_size`、`output_step_size`，并等待用户确认或修改。
- 只要创建的是 live 仿真任务，就不能在”任务已创建”或”出现首条进度”后停止；必须持续监测到终态（`COMPLETED` 或 `FAILED`）。
- 如果用户已经明确表达”启动””运行””跑”某个场景或仿真任务，默认含义就是”启动后继续盯任务”；不要再额外追问是否继续等待，除非用户明确要求只启动不跟踪。
- 如果用户明确表达“停止仿真”“结束仿真”“终止任务”“废弃这个任务”，默认含义就是执行不可恢复的终止/取消；不要再追问“是暂停还是终止”。只有当用户明确说“暂停”“先停一下”“稍后继续”时，才走暂停语义。
- 禁止在任务已启动后再用提问或选项的方式征询“要不要继续盯进度”“要不要继续等待”“要不要我后面再查”。这类问题会把本应自动持续执行的流程错误地还给用户决策，属于违规交互。
- 绝对不能在未经用户明确同意的情况下取消（`cancel_simulation_task`）或终止仿真任务。取消是不可逆操作，任务一旦取消就无法恢复。即使轮询时间较长、加速失败或出现其他非致命问题，也只能继续等待或向用户报告情况，由用户决定是否取消。
- 在调用 `get_timeseries_data` 前，必须先确认任务状态为 `COMPLETED`。
- `get_timeseries_data` 返回的是 `resource_uri`；如果后续要给本地脚本消费，必须继续调用 `read_mcp_resource(server=\"hydro-engine-mcp\", uri=resource_uri)` 读取 CSV 文本，再落成本地 `.csv` 文件。
- 如果用户要做交互式分析工作台、HTML 报告或其他 HTML 页面，先读 [references/hydros-html-prompt.md](references/hydros-html-prompt.md)。
- 如果需要理解数据结构、聚合口径或指标映射，先读 [references/hydros-data-contract.md](references/hydros-data-contract.md)。
- 如果需要快速交付一个可直接打开的页面，优先复用模板资产，而不是从零开始。
- 需要交互式分析工作台时，优先复用 [assets/hydros-dashboard-template/index.html](assets/hydros-dashboard-template/index.html)。
- 需要完整版 HTML 报告、完整曲线展示或外部 payload 驱动页面时，优先复用 [assets/hydros-report-template/index.html](assets/hydros-report-template/index.html) 和同目录 `report.data.js`。
- 当用户明确要“报告”“完整报告”“HTML 报告”“汇报页”时，不要先交付临时分析报告、手写摘要页或简版 HTML 作为最终产物；如果本地 CSV 尚未就位，先完成 `read_mcp_resource -> 落盘 .csv -> build_csv_report.py`，再输出遵循模板的正式报告。
- HTML 正式报告应尽量包含完整曲线图产物和渠道纵剖面图；若 `chart1_water_level.png`、`chart2_water_flow.png`、`chart4_gate_opening.png`、`chart5_disturbance_flow.png`、`chart6_heatmap.png`、`chart7_longitudinal_profile.png` 中有缺失，仍可交付 HTML，但必须在报告正文里显式写明缺失项、缺失原因和影响范围，不能把缺图问题只留在聊天回复里解释。

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
  用于生成符合模板规范的完整版 HTML 报告，适合汇报、截图、归档和真实结果复盘；报告默认支持纵剖面与时序曲线联动、播放、拖拽、暂停和继续，主要图表的 y 轴应根据当前数据范围自适应缩放。纵剖面需要同时展示断面顶高程、底高程和水面线，其中水位阴影只填充到底高程线，底高程阴影才延伸到坐标轴底部；闸站位置优先用稳定的虚线标识，避免播放时出现抖动。不要用自定义轻量页或单页汇报版替代该模板。
- `assets/hydros-report-template/report.data.js`
  用于承载报告模板的外部数据 payload，替换后即可接入真实仿真结果。
- 以上 Python 脚本凡涉及时间轴、总步数、时长或输出频率计算时，都必须优先使用用户显式提供的 `total_steps`、`sim_step_size`、`output_step_size`，其次再用场景 YAML；禁止写死默认步长。
- 报告和工作台必须同时识别两类信息：一类是用户输入/场景配置里的仿真参数，一类是 CSV 实际导出的采样点与时间轴字段；两者一旦不一致，必须在报告的“异常与建议”“数据质量”或等价区块里显式写出，不要只在聊天回复里口头说明。

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
- `biz_scenario_id`
- `biz_scenario_config_url`
- `sse_client_id`

可选参数（如需自定义）：
- `total_steps`（仿真总步数）
- `output_step_size`（输出步长）

注意：`tenant_id` 已由系统自动分配，无需用户提供。

参数确认规则：

- 首次创建任务时，展示场景默认仿真参数（`total_steps`、`sim_step_size`、`output_step_size`），询问用户是否需要调整。
- 如果用户在同一条消息中已经给出了所有参数（如”用默认参数启动”、”步数 800”），直接创建任务，不再额外确认。
- 如果用户只给出场景 ID / 场景名称，而没有明确说”用默认参数启动””直接运行”或没有显式提供参数值，必须先停在参数确认这一步，不能自动创建任务。

#### 获取场景配置参数的降级策略

在向用户确认参数前，需要先获取场景的默认配置。按以下顺序尝试：

1. **WebFetch**: 尝试用 WebFetch 直接获取 `biz_scenario_config_url` 的内容
2. **Bash + curl**: 如果 WebFetch 失败（网络限制、企业安全策略等），用 `curl -s <url>` 获取
3. **MCP 水网对象**: 如果 HTTP 请求都失败，尝试调用 `get_waterway_lists` 获取水网配置（可能包含相关参数）
4. **合理默认值**: 如果以上都失败，使用京石段场景的典型默认值：
   - `total_steps`: 1200
   - `sim_step_size`: 120（秒）
   - `output_step_size`: 7200（秒，即2小时）
   并明确告知用户这些是推测值，建议确认后再启动。

执行步骤：
1. 如有必要，先重新确认 SSE 连接有效。
2. 尝试获取场景配置参数（按上述降级策略）。
3. 向用户展示仿真参数供确认，格式示例：
   > 准备启动场景 [场景名称]，请确认参数：
   > - 总步数: 1200（默认）
   > - 计算步长: 120s（默认）
   > - 输出步长: 7200s（默认）
   >
   > 需要调整吗？或直接确认启动。

   反例：如果用户上一条消息只有 `100001`，这表示”选择场景 100001”，此时仍然必须先发上面的确认消息，不能直接调用 `create_simulation_task`。
4. 用户确认后，调用 `create_simulation_task`。
4. 保存并展示：
   - `biz_scene_instance_id`
   - `task_status`
   - `total_steps`
   - `default_render_objects`
   - `valid`
5. 如果这是 live 任务，创建成功后立即进入阶段四持续监测，直到任务进入 `COMPLETED` 或 `FAILED`，不要在首个进度点就结束本轮处理。
6. 对”启动 100001””运行这个场景”这类明确启动指令，默认把”持续监测到终态”视为同一轮动作的一部分，不需要再次征询用户。
7. 创建成功后的第一条反馈应直接包含任务 ID、当前状态、当前进度和“正在持续监测中”的事实；不要把“是否继续盯进度”作为可选后续动作抛给用户。

异常处理：
- `SSE通道未建立`：重新订阅后重试。
- `NullPointerException`：可能是场景配置问题，检查场景 ID 和配置 URL 是否正确。
- `valid: false`：提示检查场景配置是否匹配。

停止语义：
- “停止仿真”“结束仿真”“终止任务”“取消这个任务”默认执行 `cancel_simulation_task`。
- “暂停”“先停一下”“停住但别结束”“稍后继续”默认执行 `pause_simulation_task`。
- 当用户只说“停止”，不要把“暂停还是终止”作为澄清问题抛回给用户；默认按终止处理，并在执行结果里清楚说明这是不可恢复操作。

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
- 对 `STEPPING` 状态必须展示文本进度条快照、`current_step / total_steps` 和百分比。
- 文本进度条快照默认使用 10 格宽度，格式固定为 `███░░░░░░ 15.4% | 185/1200`；已完成部分用 `█`，未完成部分用 `░`，百分比保留 1 位小数。
- 当任务首次进入 `STEPPING` 状态时，在进度输出中附带一句提示，告知用户当前速度和预估剩余时间，并说明可以随时输入"加速"或"4x"来调整倍速（可选：0.25x、0.5x、1x、2x、4x）。这条提示只出现一次，之后不再重复。关键点：不要用阻塞式提问（如 AskUserQuestion）来询问加速，因为那会中断轮询循环，导致监测停止。正确做法是把加速提示作为进度输出的一部分，然后立即继续轮询；如果用户在后续消息中主动要求加速，再调用 `update_task_speed`。
- 对 `FAILED` 状态优先提取 `failure_exception`。
- 对 SSE 事件整理为时间线，突出状态切换和关键进度节点。
- 对 live 任务，默认持续轮询 `get_task_status` 和 `fetch_sse_events`，直到捕获终态；在终态前不要把流程当作完成。
- 禁止把“继续等待”“继续盯进度”“稍后再查”“是否拉结果”写成三选一或多选一的尾句；正确做法是继续轮询，并在终态后再自然衔接结果获取或报告生成。
- 如果用户的意图是“跑一个仿真并看结果/出报告/继续等待”，则任务完成后应自动衔接阶段五，无需再次等待用户提醒。
- 如果运行环境是命令行 PTY，而不是聊天消息流，优先用单行文本进度条展示，如 `██████░░░░ 34.0% | 408/1200`，通过 `\r` 原地刷新。
- 如果当前环境只能追加消息，则每次进度播报也必须使用同样格式的“文本进度条快照”，例如 `███░░░░░░ 15.4% | 185/1200`；不要只发纯数字快照如 `185/1200`，也不要假装实现原地覆盖。

### 阶段五：获取结果与分析

1. 确认任务状态为 `COMPLETED`。
2. 调用 `get_timeseries_data(biz_scene_instance_id)`，或直接读取用户已有的本地结果文件。
   - 如果返回 `resource_uri`，继续调用 `read_mcp_resource(server="hydro-engine-mcp", uri=resource_uri)` 获取完整 CSV 文本。
   - 如果后续要调用 `scripts/build_csv_report.py`、`scripts/build_csv_dashboard.py` 或其他本地脚本，先把读取到的 CSV 文本落盘为本地 `.csv` 文件，再把该路径传给脚本。
   - 如果当前会话里已经明确了 `total_steps`、`sim_step_size`、`output_step_size`，调用这些脚本时要把参数一并传入；不要让脚本再靠写死默认值推导时间轴。
   - 要显式比较“按用户参数推导的期望总时长 / 期望输出点数”和“CSV 实际可覆盖时长 / 实际采样点数”；如果 CSV 有缺失、时间轴异常或导出点数不足，必须在最终报告里单列说明。
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
   - HTML 报告必须对齐 `assets/hydros-report-template/index.html` 的完整版结构；不要产出自定义轻量页、单页汇报版或仅摘要页来替代模板完整版。
   - 如果用户要的是“报告”而不是“先看结论”，不要先生成一个临时分析报告文件再补正式报告；应直接产出模板化正式报告，必要时在生成过程中通过聊天同步进度即可。
  - HTML 报告和 Markdown 报告默认都应附带渠道纵剖面图；如果纵剖面数据构建失败、PNG 未生成、或引用文件不存在，仍可继续输出报告，但必须在纵剖面区块、数据质量或异常与建议区块里明确标注“本次未生成纵剖面”、缺失原因以及这会影响哪些分析结论。
  - 每次报告输出都要按目录分类：`report/` 存放 HTML 和 Markdown，`charts/` 存放 PNG 图表，`data/` 存放 `report.data.js`、统计摘要和其他中间数据。
   - 图表展示时，不要把 y 轴固定在过大的全局范围；应根据当前图表中的实际数值自动收紧范围，避免水位等小幅波动被压扁。
   - 热力图要按真实采样步绘制，不能把 `1..max(data_index)` 的每个整数步都展开成列；否则会把稀疏采样的结果画成一排细线。若首步存在占位零值，也要在热力图中剔除或单独说明。
  - 完整曲线区块不能只放图；`water_level`、`water_flow`、`gate_opening` 都要配套文字解读，解释波动范围、重点对象、控制动作和建议关注点。
  - 正式 HTML 报告引用到的 PNG 图表应尽量真实存在，至少关注 `chart1_water_level.png`、`chart2_water_flow.png`、`chart4_gate_opening.png`、`chart5_disturbance_flow.png`、`chart6_heatmap.png`、`chart7_longitudinal_profile.png`。若磁盘上存在缺失，报告必须显式列出缺失图表和影响范围，避免把它伪装成无缺陷的完整报告。
   - 对完整曲线中的占位零值要单独识别，尤其是 `water_level`、`water_flow` 的首步占位值；展示和解读时要剔除或明确说明，不能把这类占位值误判为真实异常或真实停流。
   - Markdown 报告必须是图文并茂的，用 `![描述](相对路径)` 嵌入 generate_charts.py 生成的 PNG 图表。每张图表前后都应有对应的文字分析，解释图中的关键发现和趋势，而不是只列数据表格。
   - 图表嵌入顺序建议：水位时序图 → 流量时序图 → 闸门开度图 → 分水口流量图 → 热力图，每张图后紧跟 2-3 句分析说明。
7. 需要 HTML 页面时，读取 HTML 参考并复用模板资产。
8. 如果用户明确要“报告页”“汇报页”“导出截图”或“完整曲线”，优先选报告模板；如果用户明确要筛选、联动、钻取，优先选工作台模板。
   - 当选择报告模板时，默认含义是输出符合 `assets/hydros-report-template/index.html` 的完整版 HTML 报告，而不是临时拼接的简化页面。
   - 使用报告模板时，如果既有纵剖面数据也有时序结果，页面应默认提供统一时间轴，让纵剖面、水位、流量、闸门开度按同一步号联动，并支持播放、拖拽、暂停、继续。
   - 报告中的 `Task Snapshot` 要尽量补全任务元信息：开始时间、结束时间、时间步长、输出步长、仿真时长、场景 YAML ID、任务状态等；能从场景 YAML 和 CSV 计算的要直接计算，缺失时再明确标注“无法推导”。
   - 时间口径优先级：用户显式参数 `total_steps` / `sim_step_size` / `output_step_size` > 场景 YAML > CSV 自身可靠字段（如非空的 `step_index`）> 仅展示离散采样序号。禁止写死 `120 秒/步` 之类的默认值。
   - 当 `data_index` 看起来只是输出序号、`step_index` 为空、或 `source_time` 出现异常未来时间时，要明确告诉用户“CSV 时间轴不可靠”，不要把它误解释成真实计算步号。
   - 如果用户参数推导出的总时长与 CSV 能覆盖的总时长不一致，报告中要明确写出两者差值，并把它视为 CSV 导出问题或数据质量问题。
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
| “跑一个仿真” / “启动 100001” / “运行这个场景” | 执行阶段一到阶段四并默认持续监测到终态；若用户同时关心结果、报告或明确要求等待完成，则继续自动执行阶段五 |
| “查看进度” / “任务状态” | 执行阶段四 |
| “拉取结果” / “分析数据” | 执行阶段五 |
| “生成报告” | 执行阶段五，默认输出 HTML 报告和 Markdown 报告 |
| “做个页面看仿真数据” / “做 HTML 仪表板” | 先执行阶段五拿到数据，再读 references 并复用交互式分析工作台模板 |
| “出一份 HTML 报告” / “做汇报页” / “看完整曲线” | 先执行阶段五拿到数据，再读 references 并复用符合 `index.html` 的完整版 HTML 报告模板 |
| “获取拓扑” / “场景拓扑” / “渠道拓扑” | 读取场景配置和 `objects.yaml`，输出 HTML 拓扑可视化页 |
| “画一个纵剖面” / “做纵剖面页” | 基于断面里程、底高程和水位结果生成渠道纵剖面 HTML |
| “增加闸站展示” / “展示水流流向” | 在纵剖面页中增强闸站信息和上游到下游流向标识 |
| “仿真失败了” | 进入阶段四，先查失败原因 |

如果用户直接要求后续阶段但缺少前置状态，先补齐前置信息或从前置阶段开始。

## 常见失败原因

- 初始化超时：部分智能体未上线，建议换场景或检查智能体服务。
- NullPointerException：可能是场景配置问题或依赖服务异常。
- 智能体注册失败：场景依赖的服务不可用。
- 输出为空：提示用户检查 `default_render_objects` 是否为空。

## 会话状态

始终维护以下状态：

```text
sse_client_id
biz_scenario_id
biz_scenario_config_url
biz_scene_instance_id
task_status
total_steps
default_render_objects
```
