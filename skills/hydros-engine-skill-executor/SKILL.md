---
name: hydros-engine-skill-executor
description: |
  水力仿真引擎全流程编排工具。通过调用 hydros-engine-mdm 获取场景建模元数据，再调用 hydros-engine-executor 完成仿真任务创建、进度跟踪、时序结果导出与读取、异常分析、图表输出，并生成 HTML 汇报报告、Markdown 报告、拓扑可视化页或渠道纵剖面页。

  当用户提到水力仿真、场景分析、仿真任务、水位流量分析、渠道仿真、hydros 引擎、仿真结果可视化、拓扑图、纵剖面等相关内容时触发。即使用户只是模糊地说”跑一下仿真””看看数据””分析一下结果””做个分析页面””获取拓扑””画纵剖面”，也应该触发此 skill。
---

# Hydros Engine 水力仿真 Skill

## 目录
- [初始条件](#初始条件)
- [核心职责](#核心职责)
- [沟通与硬规则](#沟通与硬规则)
- [资源导航](#资源导航)
- [五阶段工作流](#五阶段工作流)
  - [阶段一：建立 SSE 事件订阅](#阶段一建立-sse-事件订阅)
  - [阶段二：查询与选择场景](#阶段二查询与选择场景)
  - [阶段三：创建仿真任务](#阶段三创建仿真任务)
  - [阶段四：跟踪仿真进度](#阶段四跟踪仿真进度)
  - [阶段五：获取结果与分析](#阶段五获取结果与分析)
- [快捷入口](#快捷入口)
- [常见失败原因](#常见失败原因)
- [会话状态](#会话状态)

## 初始条件

在进入任何仿真流程前，先确认以下前置条件：

1. **MCP 服务检查**：同时检查 `hydros-engine-executor` 和 `hydros-engine-mdm` 两个 MCP 服务是否都已配置且可用。不同 AI 助手的 MCP 配置文件位置：
   - Claude Code: `.claude.json` 或 `~/.claude.json`
   - Codex: `~/.codex/config.toml`
   - Copaw: `workspaces/agent.json`
   其中 `hydros-engine-executor` 负责仿真任务链路，`hydros-engine-mdm` 作为场景建模元数据、拓扑和 `objects.yaml` 相关流程的前置条件。这样可以及早发现连接问题，避免在后续流程中遇到意外失败。

2. **Token 配置**：检查用户是否已配置 Bearer token。如果 token 缺失，引导用户：
   - 访问 `https://hydroos.cn/playground/` 注册或登录
   - 在”账号管理”中获取 API token
   - 将 token 提供给我来完成配置

3. **使用正确的工具链**：仿真执行、进度跟踪和结果导出优先走已注册的 `hydros-engine-executor` MCP 工具。场景建模元数据、拓扑和 `objects.yaml` 相关动作开始前，先确认 `hydros-engine-mdm` 已接通；如果缺失，先报告“元数据前置条件不足”，不要静默跳过。

4. **结果下载默认路径**：凡是用户要求"下载结果文件""落盘到本地""保存结果文件"，默认走标准下载链：
    - 调用 `get_timeseries_data` 启动结果导出任务
    - 持续轮询 `get_export_status`，直到导出与 Excel 上传结果为 `COMPLETED`
    - 从 `get_export_status` 的完成结果中提取 `resource_uri` 或实际下载地址
    - 使用标准 HTTP GET 直接下载到本地文件
    - 对 `.csv` 结果可直接写盘；对 `.xlsx` 结果优先落成真实 Excel 文件
    - 不要把大段结果文本通过终端交互会话、`cat > file`、分块粘贴或聊天输出中转来落盘，这类方式容易被截断，生成坏文件。

**详细连接排查指南**：如遇连接问题，参考 [references/mcp-connection-guide.md](references/mcp-connection-guide.md)。

## 核心职责

编排 hydros 仿真的完整流程，并在结果阶段提供图表、异常分析、HTML 汇报报告、Markdown 报告、场景拓扑可视化和渠道纵剖面展示支持。

## 沟通与硬规则

- 始终使用中文与用户沟通，技术术语和代码标识保持原文。
- 凡是执行本 skill 目录下的 Python 脚本，一律使用 `python3`，不要使用 `python`。
- 先调用 `subscribe_to_simulation_events` 建立 SSE 事件订阅通道，再创建仿真任务。这样可以确保任务创建后的进度事件能被正确接收，避免错过关键状态更新。
- `biz_scenario_id` 和 `biz_scenario_config_url` 成对使用，且只能来自 `hydros-engine-mdm` 的 `biz_scenario_id_lists` 返回结果。这样可以保证场景配置的一致性和有效性。
- 用户选定场景后，在参数确认前先拉取并缓存一份 `objects.yaml`，再基于这份本地文件输出场景拓扑总结。下载方式要与脚本实现保持一致：先从场景 YAML 读取 `hydros_objects_modeling_url`，对 URL 做规范化编码，再通过标准 HTTP GET 下载，最后以 UTF-8 一次性写入本地缓存文件。这样后续生成纵剖面图时可以直接复用，避免重复拉取和口径漂移。
- 场景建模元数据、拓扑和 `objects.yaml` 属于元数据链路。进入这部分前，先确认 `hydros-engine-mdm` 已配置可用；若未配置，明确告诉用户当前缺少元数据前置条件，不要假设拓扑正确或跳过说明继续产出结果。
- 用户选定场景后，调用 `hydros-engine-mdm` 的 `get_scenario_events` 查询预置事件，与默认参数一起展示。这让用户全面了解场景配置，一次性确认所有关键参数。
- 用户只回复场景 ID 或”选这个”时，视为”选定场景”而非”立即启动”。先展示默认参数供确认，避免使用错误配置启动任务。
- 创建 live 仿真任务后，持续监测到终态（`COMPLETED` 或 `FAILED`）。中途停止会导致用户无法及时了解任务结果。
- 用户说”启动””运行”时，默认包含”持续跟踪”。避免额外追问，保持流程流畅。
- 只在实际进入轮询循环后才说”正在持续监测中”。确保状态描述与实际行为一致，避免误导用户。
- 用户说”停止仿真””终止任务”时，默认执行不可恢复的取消操作。只有明确说”暂停”时才走暂停语义。
- 任务启动后避免用提问方式征询”要不要继续盯进度”。这会打断自动化流程，增加用户负担。
- 未经用户明确同意不要取消任务。取消是不可逆操作，即使遇到非致命问题也应继续等待或报告情况，由用户决定。
- 轮询中断时明确说明”监测已中断”。保持状态描述的真实性，避免伪装成持续监测。
- 必须明确区分”skill 的输出约束”和”聊天前端的渲染能力”。skill 不能让聊天界面凭空出现原生进度组件，但无论处于哪种运行环境，都必须把当前进度渲染为统一格式的文本进度条。
- 统一进度条格式固定为 `███░░░░░░15.4% | 185/1200`。`█/░` 区宽度固定 10 格，后面紧跟百分比，不加额外空格，再接 ` | current/total`。
- **进度条展现模式**：
  - **轮询模式（当前实现）**：每 5-10 秒查询一次进度，跳跃式更新。适合长时间运行的任务，网络开销小，实现简单。
  - **流式模式（可选）**：利用 Claude 的流式输出特性，在轮询循环中每次查询后立即输出进度条。通过缩短轮询间隔（2-5 秒）和连续输出，让进度更新更流畅。参考 `scripts/streamable_progress_demo.py` 查看两种模式的对比演示。
- 在追加消息型聊天环境里，”自动显示进度条”的正确含义是：只要本轮仍在持续轮询，代理就必须主动连续发送文本进度条快照，不需要用户再次提醒；如果本轮被用户中断，则自动刷新链条随之中断，恢复后必须先说明”监测曾中断，现已恢复”。
- 调用 `get_timeseries_data` 前先确认任务状态为 `COMPLETED`。未完成的任务可能返回不完整的数据。
- `get_timeseries_data` 现在只负责启动结果导出任务，不保证立即可下载。后续必须轮询 `get_export_status(biz_scene_instance_id)`，直到状态为 `COMPLETED`。
- 只有当 `get_export_status` 返回 `COMPLETED` 且给出 `resource_uri` 或下载地址时，才允许进入下载步骤。若状态为 `FAILED`，立即报告“结果导出失败”，不要继续生成任何图表或报告。
- 当目标是“下载结果文件到本地”而不是立刻做报告时，也必须走同一条标准链路：`get_timeseries_data -> get_export_status 轮询 -> resource_uri/下载地址 -> 标准 HTTP GET 下载到本地 -> 本地一次性校验`。不要把结果内容通过终端标准输入、交互式 `cat`、消息复制粘贴等方式中转。
- 如果本地已经存在同名结果文件，覆盖前先核对文件大小或数据行数；如发现明显偏小、数据行数异常少，优先视为“落盘被截断”，重新按标准链路完整下载，不要在坏文件基础上追加写入。
- 如果结果文件下载、读取、写盘或完整性校验任一步失败，立即报告“结果下载失败”，并停止后续分析、图表和报告生成。不要回退到任何默认数据、历史缓存、旧结果文件或明显残缺的数据文件继续产出结果。
- 如果用户要做 HTML 报告或其他 HTML 页面，先读 [references/hydros-html-prompt.md](references/hydros-html-prompt.md)。
- 如果需要理解数据结构、聚合口径或指标映射，先读 [references/hydros-data-contract.md](references/hydros-data-contract.md)。
- 如果需要快速交付一个可直接打开的页面，优先复用模板资产，而不是从零开始。
- 需要完整版 HTML 报告、结果曲线展示或可直接打开的单文件页面时，优先复用 [assets/hydros-report-template/index.html](assets/hydros-report-template/index.html) 模板，并按当前脚本实现把真实 payload 内联到 `simulation_report.html`。
- 当用户明确要“报告”“完整报告”“HTML 报告”“汇报页”时，不要先交付临时分析报告、手写摘要页或简版 HTML 作为最终产物；如果本地结果文件尚未就位，先完成 `get_timeseries_data -> get_export_status 轮询 -> resource_uri/下载地址 -> 落盘结果文件 -> build_timeseries_report.py`，再输出遵循模板的正式报告。
- HTML 正式报告应尽量包含结果曲线图产物和渠道纵剖面图；若 `chart1_water_level.png`、`chart2_water_flow.png`、`chart4_gate_opening.png`、`chart5_disturbance_flow.png`、`chart6_heatmap.png`、`chart7_longitudinal_profile.png` 中有缺失，仍可交付 HTML，但必须在报告正文里显式写明缺失项、缺失原因和影响范围，不能把缺图问题只留在聊天回复里解释。
- 正式 HTML 报告生成完成后，默认通过 Hydros OpenAPI 匿名文件上传接口直接上传本地 `simulation_report.html`，并把接口返回结果作为交付结果的一部分；除非用户明确只要本地文件，否则不要停在“本地已生成 HTML”这一步。
- HTML 报告上传统一使用 `curl --form` 直传到 `https://hydroos.cn/openapi/engine/api/v1/file/anonymous/upload/<biz_scene_instance_id>`；如果接口返回 `ACCESS_UNAUTHORIZED` 或其他失败响应，明确报告“远端上传失败”和接口错误，不要伪装成本地报告失败。
- 直传命令模板如下。`Content-Type: multipart/form-data; boundary=...` 由 `curl --form` 自动生成，通常不要手写固定 boundary，避免请求头与 multipart 请求体不一致：

    ```bash
    curl --location --request POST \
      "https://hydroos.cn/openapi/engine/api/v1/file/anonymous/upload/<biz_scene_instance_id>" \
      --header "User-Agent: Apifox/1.0.0 (https://apifox.com)" \
      --header "Accept: */*" \
      --header "Host: hydroos.cn" \
      --header "Connection: keep-alive" \
      --form "file=@\"output/<biz_scene_instance_id>/report/simulation_report.html\""
    ```

## 资源导航

- `scripts/generate_charts.py`
  用于生成 matplotlib 图表。
- `scripts/analyze_anomalies.py`
  用于异常检测和问题汇总。
- `scripts/streamable_progress_demo.py`
  用于演示轮询模式和流式模式的进度条实现。支持三种演示模式：`--mode polling`（轮询模式）、`--mode streamable`（流式模式）、`--mode comparison`（对比演示）。可用于理解两种进度条实现方式的区别。
- `scripts/build_timeseries_report.py`
  用于把本地结果文件（CSV 或 XLSX）快速整理成 HTML 报告和 Markdown 报告；若可获取断面里程与底高程，还会默认附带渠道纵剖面图。输出目录默认统一为 `output/<biz_scene_instance_id>/`，其下再按 `report/`、`charts/`、`data/` 分类。脚本会自动兼容 `objects.yaml` 等远程 URL 中的中文路径并优先复用本地缓存。
- `scripts/build_longitudinal_profile.py`
  用于根据 `objects.yaml` 断面信息和时序结果生成渠道纵剖面 HTML 页面，并可叠加闸站信息和上游到下游流向标识。高程字段优先读取显式的 `t_top_elevation` 和 `bottom_elevation`；若缺失，再根据 `cross_section_geometry.data_points` 的最大值和最小值推导。脚本会自动兼容中文路径 URL。
- `references/hydros-data-contract.md`
  用于理解时序记录结构、推荐聚合口径、异常信号定义。
- `references/hydros-html-prompt.md`
  用于生成 HTML 页面提示词规范、报告页规格、拓扑页/纵剖面页规格或页面实现约束。
- `assets/hydros-report-template/index.html`
  用于生成符合模板规范的完整版 HTML 报告，适合汇报、截图、归档和真实结果复盘；报告默认支持纵剖面与时序曲线联动、播放、拖拽、暂停和继续，主要图表的 y 轴应根据当前数据范围自适应缩放。纵剖面需要同时展示断面顶高程、底高程和水面线，其中水位阴影只填充到底高程线，底高程阴影才延伸到坐标轴底部；闸站位置优先用稳定的虚线标识，避免播放时出现抖动。不要用自定义轻量页或单页汇报版替代该模板。
- `assets/hydros-report-template/report.data.js`
  作为兼容产物保留，用于调试或外部二次接线；正式交付默认以内联数据的 `simulation_report.html` 为准。
- 以上 Python 脚本涉及时间轴、总步数、时长或输出频率计算时，优先使用用户显式提供的 `total_steps`、`sim_step_size`、`output_step_size`，其次再用场景 YAML。避免写死默认步长，确保计算准确性。
- 仿真覆盖总时长的硬规则：`simulation_duration_seconds = total_steps * output_step_size`。`sim_step_size` 是内部计算步长，只能用于解释数值求解粒度或计算步信息，不能用来计算总仿真时长。
- 报告应同时识别两类信息：用户输入的仿真参数和结果文件实际导出的数据。两者不一致时，在报告的”异常与建议”或”数据质量”区块显式说明，避免用户误解数据质量。

## 五阶段工作流

### 阶段一：建立 SSE 事件订阅

1. 生成 UUID 作为 `sse_client_id`。
2. 调用 `subscribe_to_simulation_events(sse_client_id)`。
3. 确认返回 `success: true`。
4. 向用户解释：`sse_client_id` 绑定 SSE 事件订阅通道，后续创建任务、跟踪进度都依赖它。

异常处理：
- 连接失败时，提示用户检查 `hydros-engine-executor` MCP 服务。
- 如果场景建模元数据、拓扑或 `objects.yaml` 相关步骤失败，同时提示用户检查 `hydros-engine-mdm` MCP 配置是否完整。
- 如果后续报 “SSE通道未建立”，用同一个 `sse_client_id` 重新订阅。

### 阶段二：查询与选择场景

1. 调用 `hydros-engine-mdm` 的 `biz_scenario_id_lists`。
2. 将场景整理为 markdown 表格，至少包含：序号、场景 ID、场景名称、核心能力。
3. 保存每个场景的 `biz_scenario_config_url`，后续创建任务时必须使用。
4. 给出推荐场景，优先描述中包含“测试”或“SDK”的场景，其次选依赖较少的场景。
5. 一旦用户明确选定某个场景（例如只回复场景 ID、场景名称，或说“就这个”“选这个”），在进入阶段三前，先确认 `hydros-engine-mdm` 前置配置已完成，再基于场景 YAML 里的 `hydros_objects_modeling_url` 拉取并缓存 `objects.yaml`，再默认补一段简要拓扑总结。
   下载方式固定为：
   - 先确认 `hydros-engine-mdm` 已配置可用，再读取场景 YAML，提取 `hydros_objects_modeling_url`
   - 对下载地址做 URL 规范化，兼容中文路径和特殊字符
   - 通过标准 HTTP GET 直接下载 `objects.yaml`
   - 以 UTF-8 文本形式一次性写入本地缓存文件，供本轮后续步骤复用
6. 在进入阶段三前，调用 `hydros-engine-mdm` 的 `get_scenario_events` 查询该场景支持注入的预置事件，并整理为简要事件清单；后续参数确认时必须和默认仿真参数一起展示给用户选择。

场景拓扑简要总结要求：
- 至少给出 `waterway_id`、主水网/渠道名称、对象总览（如 `UnifiedCanal`、`CrossSection`、`DisturbanceNode`、`GateStation`、`Gate` 的数量或主要成员）。
- 用 1 到 3 句话概括主链路拓扑，例如“主渠从 QD-1 依次连接到 QD-14，中间穿插若干分水口、退水闸和 2 个闸站”。
- 点出关键控制节点或特殊对象，例如 `ZM1`、`ZM2`、主要分水口、退水闸、入口断面。
- 这是“简单总结”，默认放在场景确认反馈里即可，不要等用户追问后才补。
- 如果 `objects.yaml` 暂时不可读，也要明确说明“当前无法读取对象拓扑，只展示场景基本信息”；如果根因是元数据链路未接通，要同时指出 `hydros-engine-mdm` 前置条件缺失，不要静默跳过。
- 已成功拉取的 `objects.yaml` 默认视为本轮会话资产，后续生成纵剖面、拓扑页或正式报告时优先复用这份本地文件，不要再次重复拉取。

场景预置事件展示要求：
- 优先调用 `hydros-engine-mdm` 的 `get_scenario_events`，按场景 ID 查询支持注入的预置事件。
- 展示事件清单时必须带序号，默认使用 `1. 2. 3.` 这种连续编号，方便用户按序号选择或引用。
- 至少展示每个事件的名称/类型、作用对象、触发步或触发时间、是否默认启用。
- 这部分默认放在参数确认之前，与 `total_steps`、`sim_step_size`、`output_step_size` 同时出现，供用户一起决定是否按默认配置启动。
- 如果 `hydros-engine-mdm / get_scenario_events` 返回空列表，要明确写“该场景当前无可注入预置事件”。
- 如果当前环境暂时无法调用 `hydros-engine-mdm / get_scenario_events`，要明确写“当前无法读取场景预置事件，仅展示仿真参数”，不要静默跳过。

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
- 首次创建任务时，还要同步展示通过 `hydros-engine-mdm / get_scenario_events` 查询到的预置事件清单，并让用户一并确认“是否按默认事件配置启动”。
- 如果用户在同一条消息中已经给出了所有参数（如”用默认参数启动”、”步数 800”），直接创建任务，不再额外确认。
- 如果用户只给出场景 ID / 场景名称，而没有明确说”用默认参数启动””直接运行”或没有显式提供参数值，必须先停在参数确认这一步，不能自动创建任务。

#### 获取场景配置参数的降级策略

在向用户确认参数前，需要先获取场景的默认配置。按以下顺序尝试：

1. **WebFetch**: 尝试用 WebFetch 直接获取 `biz_scenario_config_url` 的内容
2. **Bash + curl**: 如果 WebFetch 失败（网络限制、企业安全策略等），用 `curl -s <url>` 获取
3. **MCP 水网对象**: 如果 HTTP 请求都失败，尝试调用 `hydros-engine-mdm` 的 `get_waterway_lists` 获取水网配置（可能包含相关参数）
4. **合理默认值**: 如果以上都失败，使用京石段场景的典型默认值：
   - `total_steps`: 1200
   - `sim_step_size`: 120（秒）
   - `output_step_size`: 7200（秒，即2小时）
   并明确告知用户这些是推测值，建议确认后再启动。

执行步骤：
1. 如有必要，先重新确认 SSE 连接有效。
2. 先确认阶段二的“场景拓扑简要总结”已经输出；如果还没输出，必须先补这段总结，再继续下面步骤。
3. 先确认阶段二的“场景预置事件清单”已经输出；如果还没输出，必须先补这段清单，再继续下面步骤。
4. 尝试获取场景配置参数（按上述降级策略）。
5. 调用 `hydros-engine-mdm` 的 `get_scenario_events` 获取该场景支持注入的预置事件；如果失败，必须在反馈中明确说明。
6. 向用户展示仿真参数和预置事件供确认，格式示例：
   > 准备启动场景 [场景名称]，请确认参数：
   > - 总步数: 1200（默认）
   > - 计算步长: 120s（默认）
   > - 输出步长: 7200s（默认）
   > - 预置事件:
   >   1. 事件 A：step=60，默认关闭
   >   2. 事件 B：step=180，默认开启
   >
   > 需要调整参数、修改事件选择，或直接确认启动。

   反例：如果用户上一条消息只有 `100001`，这表示”选择场景 100001”，此时仍然必须先发上面的确认消息，不能直接调用 `create_simulation_task`。
   反例：如果用户已经展示了默认参数，但还没有给出 `objects.yaml` 简要拓扑总结，也不能直接调用 `create_simulation_task`。
   反例：如果用户已经展示了默认参数，但还没有给出 `hydros-engine-mdm / get_scenario_events` 返回的预置事件清单，也不能直接调用 `create_simulation_task`。
7. 用户确认后，调用 `create_simulation_task`。
8. 保存并展示：
   - `biz_scene_instance_id`
   - `task_status`
   - `total_steps`
   - `default_render_objects`
   - `valid`
9. 如果这是 live 任务，创建成功后立即进入阶段四持续监测，直到任务进入 `COMPLETED` 或 `FAILED`，不要在首个进度点就结束本轮处理。
10. 对”启动 100001””运行这个场景”这类明确启动指令，默认把”持续监测到终态”视为同一轮动作的一部分，不需要再次征询用户。
11. 创建成功后的第一条反馈应直接包含任务 ID、当前状态、当前进度和“正在持续监测中”的事实；不要把“是否继续盯进度”作为可选后续动作抛给用户。
12. 如果要给出“预计剩余时间”或“预计完成时间”，必须基于真实轮询中观测到的步进速度计算，不能用 `total_steps * sim_step_size` 推导成墙钟剩余时间。

异常处理：
- `SSE通道未建立`：重新订阅后重试。
- `NullPointerException`：可能是场景配置问题，检查场景 ID 和配置 URL 是否正确。
- `valid: false`：提示检查场景配置是否匹配。

停止语义：
- “停止仿真”“结束仿真”“终止任务”“取消这个任务”默认执行 `cancel_simulation_task`。
- “暂停”“先停一下”“停住但别结束”“稍后继续”默认执行 `pause_simulation_task`。
- 当用户只说“停止”，不要把“暂停还是终止”作为澄清问题抛回给用户；默认按终止处理，并在执行结果里清楚说明这是不可恢复操作。

### 阶段四：跟踪仿真进度

使用 `get_task_status(sse_client_id, biz_scene_instance_id)` 轮询任务状态和当前步数。

标准监测方法：

1. 创建 live 任务成功后，立刻进入轮询循环。
2. 每一轮轮询必须至少执行一次 `get_task_status(sse_client_id, biz_scene_instance_id)`。
3. 每一轮都要用最新一次 `get_task_status` 的结果更新“最新可信状态”：
   - 优先使用最新的终态（`COMPLETED` / `FAILED`）。
   - 进度、状态和异常信息都以 `get_task_status` 返回结果为准。
4. 每一轮结束后，只有在准备继续下一轮轮询时，才可以对用户表述为“正在持续监测中”。
5. 如果已经停止轮询，或当前回合不会继续执行下一轮，则必须明确表述为“本轮已查询到最新状态”，不能伪装成持续监测。
6. 推荐轮询间隔为 5 到 10 秒；如果任务步进非常快，可缩短到 2 到 5 秒，但不能只查一次就结束。
7. “持续监测完成”的判定只有两种：
   - 捕获到终态 `COMPLETED` 或 `FAILED`
   - 用户明确要求停止跟踪、取消任务或结束当前流程

剩余时间与完成时间估算规则：

1. 墙钟剩余时间只能基于真实监测样本估算，不能直接使用 `sim_step_size`、`output_step_size` 或“仿真总时长”替代。
2. 至少拿到 2 个有效监测样本后，才允许开始估算：
   - 样本至少包含：查询时间、`current_step`、`total_steps`
   - 有效样本要求：`current_step` 递增，且两次查询之间存在非零时间差
3. 推荐优先使用最近 2 到 5 个有效样本计算实际步进速度：
   - `实际速度 = 步数增量 / 墙钟耗时`
   - `预计剩余时间 = (total_steps - current_step) / 实际速度`
4. 如果样本过少、步数没有推进、或速度波动过大，必须明确写“当前样本不足，暂不提供可靠 ETA”，不要编造剩余时间。
5. `sim_step_size` 只用于解释内部计算步长；仿真覆盖总时长按 `total_steps * output_step_size` 计算。二者都不能用于估算任务还要运行多少分钟。
6. 只有当 ETA 来自刚刚完成的真实轮询样本时，才允许写“预计 X 分钟内完成”或“预计 HH:MM 完成”。

状态播报规则：

- 当预计剩余时间较短并且当前轮询会继续执行时，直接报告当前进度和基于真实样本计算的 ETA，然后继续监测；不要反问用户“要不要继续等待”。
- 当预计剩余时间较长时，也不要抛三选一让用户决定；默认继续监测，只向用户报告“当前进度 + 基于真实样本的 ETA + 将继续监测”。
- 只有当当前环境无法继续维持轮询，或会话确实要结束时，才允许明确说明“本轮无法继续驻留监测”；这时必须说明原因，不能伪装成还在持续监测。

真实性校验要求：
- 任意一次“正在持续监测中”的回复，都必须能对应到本轮刚刚执行过的真实 MCP 调用结果，而不是沿用上一次的旧状态。
- 如果回复里出现“最新进度”“当前状态”“正在监测”，必须能同时指出最近一次真实查询得到的 `task_status`、`current_step` 或 `failure_exception`。
- 不允许只在创建任务后说一句“我会持续监测”，然后没有后续轮询动作。

状态流转：

```text
INIT -> WAITING_AGENTS -> READY -> STEPPING -> COMPLETED
                                             -> FAILED
```

展示要求：
- 对 `STEPPING` 状态必须展示文本进度条快照、`current_step / total_steps` 和百分比。
- 文本进度条快照默认使用 10 格宽度，格式固定为 `███░░░░░░15.4% | 185/1200`；已完成部分用 `█`，未完成部分用 `░`，百分比保留 1 位小数。
- 任何包含当前进度、当前状态、最新进度、正在监测等内容的回复，第一行都必须先给出这条文本进度条；后面才允许补状态、ETA 或说明。
- 在追加消息型聊天环境中，每一条进度播报都应把最新文本进度条放在回复第一行，后面再补状态、ETA 或说明；不要把进度条埋在长段解释后面。
- 当任务首次进入 `STEPPING` 状态时，在进度输出中附带一句提示，告知用户当前速度和预估剩余时间；如果此时样本还不足以给出可靠 ETA，就明确写“暂未形成可靠 ETA”。同时说明可以随时输入"加速"或"4x"来调整倍速（可选：0.25x、0.5x、1x、2x、4x）。这条提示只出现一次，之后不再重复。关键点：不要用阻塞式提问（如 AskUserQuestion）来询问加速，因为那会中断轮询循环，导致监测停止。正确做法是把加速提示作为进度输出的一部分，然后立即继续轮询；如果用户在后续消息中主动要求加速，再调用 `update_task_speed`。
- 对 `FAILED` 状态优先提取 `failure_exception`。
- 对 live 任务，默认持续轮询 `get_task_status`，直到捕获终态；在终态前不要把流程当作完成。
- 禁止把“继续等待”“继续盯进度”“稍后再查”“是否拉结果”写成三选一或多选一的尾句；正确做法是继续轮询，并在终态后再自然衔接结果获取或报告生成。
- 如果用户的意图是“跑一个仿真并看结果/出报告/继续等待”，则任务完成后应自动衔接阶段五，无需再次等待用户提醒。
- 如果运行环境是命令行 PTY，而不是聊天消息流，优先用单行文本进度条展示，如 `██████░░░░34.0% | 408/1200`，通过 `\r` 原地刷新；但对用户可见的进度文本格式仍必须与聊天环境保持一致。
- 如果当前环境只能追加消息，则每次进度播报也必须使用同样格式的“文本进度条快照”，例如 `███░░░░░░15.4% | 185/1200`；不要只发纯数字快照如 `185/1200`，也不要假装实现原地覆盖。
- 如果用户明确要求“进度条”，且当前环境不是 PTY，就把需求降级解释为“文本进度条快照 + 持续追加播报”；但降级后显示格式仍必须完全一致，不能换成别的样式。

### 阶段五：获取结果与分析

1. **数据获取**：
    - 确认任务状态为 `COMPLETED`
    - 调用 `get_timeseries_data(biz_scene_instance_id)` 启动结果导出任务
    - 持续轮询 `get_export_status(biz_scene_instance_id)`，直到状态为 `COMPLETED` 或 `FAILED`
    - 当状态为 `COMPLETED` 时，提取 `resource_uri` 或实际下载地址
    - 优先使用标准 HTTP GET 将结果文件一次性落盘到本地：

    ```bash
    curl -L \
      "https://.../SIM_xxx.xlsx" \
      -o "output/SIM_xxx.xlsx"
    ```

    - 如果 `get_export_status` 返回 `FAILED`，直接报告阶段五失败并停止，不允许继续下载、图表生成或报告产出
    - 结果文件只有在导出与 Excel 上传完成后才能下载；正式交付优先用脚本，不要依赖终端复制粘贴或 stdout 重定向
    - 写完后立刻校验文件大小、数据行数，必要时补充总记录数核对
    - 如果用户后续要生成 HTML 报告、Markdown 报告、拓扑页或纵剖面页，则在这一步一并基于场景 YAML 下载并缓存 `objects.yaml`；下载方式同样是"读取 `hydros_objects_modeling_url` -> 规范化 URL -> 标准 HTTP GET 下载 -> 以 UTF-8 一次性写入本地缓存文件"；若本轮前面已经缓存过，则优先复用，不要重复拉取
    - 如果结果文件下载失败、写盘失败，或校验后判断为坏文件/残缺文件，则直接报告阶段五失败并停止，不允许继续生成图表、异常分析或任何正式报告
    - 传递用户显式提供的仿真参数给脚本，避免写死默认值

2. **统计摘要**：生成总记录数、采样步数、对象数、指标数、异常点数量。

3. **图表生成**：用 `scripts/generate_charts.py` 生成水位、流量、热力图等图表。注意 y 轴自适应收紧，热力图按真实采样步绘制，识别并剔除占位零值。

4. **异常分析**：用 `scripts/analyze_anomalies.py` 检测负压、流速异常、水头损失等。

5. **报告生成**：
   - **默认产出**：HTML 报告 + Markdown 报告（除非用户明确只要其中一种）
   - **HTML 报告**：对齐 `assets/hydros-report-template/index.html` 完整版结构，包含纵剖面与时序曲线联动
   - **Markdown 报告**：图文并茂，每张图表配套文字分析
   - **目录结构**：统一落盘到 `output/<biz_scene_instance_id>/`；其中 `report/` 存放报告，`charts/` 存放图表，`data/` 存放结果文件、`objects.yaml` 和分析中间文件
   - **数据验证**：比较期望与实际的时长/点数，不一致时在报告中说明
   - **上传交付**：当 HTML 正式报告生成完成后，使用 Hydros OpenAPI 匿名上传接口直传本地 `simulation_report.html` 文件；优先把接口返回的报告地址或资源信息交付给用户，同时保留本地 `simulation_report.html`
   - **上传命令**：

     ```bash
     curl --location --request POST \
       "https://hydroos.cn/openapi/engine/api/v1/file/anonymous/upload/<biz_scene_instance_id>" \
       --header "User-Agent: Apifox/1.0.0 (https://apifox.com)" \
       --header "Accept: */*" \
       --header "Host: hydroos.cn" \
       --header "Connection: keep-alive" \
       --form "file=@\"output/<biz_scene_instance_id>/report/simulation_report.html\""
     ```

   - **上传约束**：如果 OpenAPI 返回 `ACCESS_UNAUTHORIZED`、网络错误或其他失败响应，应明确报告“本地报告生成成功，远端上传失败”，并给出接口返回错误
   - **失败处理**：如果 HTML 已生成但上传失败，要明确区分“本地报告生成成功”和“远端上传失败”，并把失败原因单独报告；不要伪装成整份报告都失败

6. **模板选择**：
   - 用户要”报告页””汇报页””结果曲线” → 报告模板
   - 用户要”拓扑页””纵剖面页”或其他专题 HTML 页 → 对应专题页面模板或实现

7. **拓扑与纵剖面**：
   - 拓扑图：读取 `objects.yaml`，整理 connections 和对象关系
   - 纵剖面：基于断面 location、bottom_elevation 和水位生成，支持闸站展示和流向标识

**详细报告生成规范**：参考 [references/report-generation-guide.md](references/report-generation-guide.md)。

## 快捷入口

| 用户意图 | 动作 |
| --- | --- |
| “连接仿真引擎” / “建立 SSE” | 执行阶段一 |
| “列出场景” | 执行阶段一到阶段二 |
| 用户回复场景 ID / “选这个场景” | 视为完成阶段二选择动作；先输出该场景基于 `objects.yaml` 的简要拓扑总结，再进入阶段三参数确认 |
| “跑一个仿真” / “启动 100001” / “运行这个场景” | 执行阶段一到阶段四并默认持续监测到终态；若用户同时关心结果、报告或明确要求等待完成，则继续自动执行阶段五 |
| “查看进度” / “任务状态” | 执行阶段四 |
| “拉取结果” / “分析数据” | 执行阶段五 |
| “生成报告” | 执行阶段五，默认输出 HTML 报告和 Markdown 报告 |
| “做个页面看仿真数据” / “做 HTML 页面” | 先执行阶段五拿到数据，再读 references 并生成报告页或专题 HTML 页面 |
| “出一份 HTML 报告” / “做汇报页” / “看结果曲线” | 先执行阶段五拿到数据，再读 references 并复用符合 `index.html` 的完整版 HTML 报告模板 |
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
