---
name: hydros-engine-skill-executor
description: |
  水力仿真引擎全流程编排工具。通过线上 Hydros Engine MCP 统一入口完成场景查询、仿真任务创建、进度跟踪、时序结果导出与读取、异常分析、图表输出，并生成 HTML 汇报报告、Markdown 报告、拓扑可视化页或渠道纵剖面页。

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

1. **MCP 服务检查**：检查 Hydros Engine MCP 是否已配置且可用。当前线上已验证的 MCP URL 是 `https://mcp.hydroos.pub/`；客户端里的 server 名称可继续使用 `hydros-engine-executor`。不同 AI 助手的 MCP 配置文件位置：
   - Claude Code: `.claude.json` 或 `~/.claude.json`
   - Codex: `~/.codex/config.toml`
   - Copaw: `workspaces/agent.json`
   历史文档中的 `https://hydroos.cn/mcps/hydros-engine-executor` 和 `https://hydroos.cn/mcps/hydros-engine-mdm` 已不再作为新配置推荐。当前生产环境通过统一 MCP endpoint 暴露场景查询、仿真执行、结果导出和 resource 读取能力。

2. **Token 配置**：检查用户是否已配置 Bearer token。如果 token 缺失，引导用户：
   - 获取 Hydros API token
   - 将 token 写入本机 MCP 配置或环境变量，例如 `MCP_TOKEN` / `HYDROS_API_TOKEN`
   - MCP Header 保留 `Execution-Source`、`Production-Code` 和 `Accept`

3. **使用正确的工具链**：场景查询、仿真执行、进度跟踪、结果导出和结果读取优先走已注册的 Hydros Engine MCP 工具。不要回退到旧 `/mcps/...` URL 或猜测式业务 REST 路径。

4. **结果下载默认路径**：凡是用户要求"下载结果文件""落盘到本地""保存结果文件"，默认走标准下载链：
    - 调用 `get_timeseries_data` 启动结果导出任务
    - 持续轮询 `get_export_status`，直到导出与 Excel 上传结果为 `COMPLETED`
    - 从 `get_export_status` 的完成结果中优先提取 `resource_uri`
    - 对 `hydroengine://downloads/...` 这类 URI，优先通过 MCP `resources/read` 读取结果，再落盘或解析
    - 只有当返回的是明确可访问的 HTTP 下载地址时，才使用标准 HTTP GET 直接下载到本地文件
    - 不要默认把 HTTPS `/s3/...` 地址当作可直接下载文件；这类地址可能需要 JWT，MCP token 不一定可用
    - 不要把大段结果文本通过终端交互会话、`cat > file`、分块粘贴或聊天输出中转来落盘，这类方式容易被截断，生成坏文件。

**详细连接排查指南**：如遇连接问题，参考 [references/mcp-connection-guide.md](references/mcp-connection-guide.md)。

## 核心职责

编排 hydros 仿真的完整流程，并在结果阶段提供图表、异常分析、HTML 汇报报告、Markdown 报告、场景拓扑可视化和渠道纵剖面展示支持。

## 沟通与硬规则

- 始终使用中文与用户沟通，技术术语和代码标识保持原文。
- 凡是执行本 skill 目录下的 Python 脚本，一律使用 `python3`，不要使用 `python`。
- 先调用 `subscribe_to_simulation_events` 建立 SSE 事件订阅通道，再创建仿真任务。这样可以确保任务创建后的进度事件能被正确接收，避免错过关键状态更新。
- `biz_scenario_id` 和 `biz_scenario_config_url` 成对使用，且只能来自 MCP `biz_scenario_id_lists` 返回结果。这样可以保证场景配置的一致性和有效性。
- 用户选定场景后，在参数确认前先拉取并缓存一份 `objects.yaml`，再基于这份本地文件输出场景拓扑总结。下载方式要与脚本实现保持一致：先从场景 YAML 读取 `hydros_objects_modeling_url`，对 URL 做规范化编码，再通过标准 HTTP GET 下载，最后以 UTF-8 一次性写入本地缓存文件。这样后续生成纵剖面图时可以直接复用，避免重复拉取和口径漂移。
- 场景建模元数据、拓扑和 `objects.yaml` 属于元数据链路。进入这部分前，先确认统一 MCP endpoint 已配置可用；若相关工具不可用，明确说明当前缺少元数据能力，不要假设拓扑正确或跳过说明继续产出结果。
- 用户选定场景后，如 MCP 暴露 `get_scenario_events`，调用它查询预置事件，并与默认参数一起展示。这让用户全面了解场景配置，一次性确认所有关键参数。
- 用户只回复场景 ID 或”选这个”时，视为”选定场景”而非”立即启动”。先展示默认参数供确认，避免使用错误配置启动任务。
- 创建 live 仿真任务后，持续监测到终态（`COMPLETED` 或 `FAILED`）。中途停止会导致用户无法及时了解任务结果。
- `create_simulation_task` 的关键返回值通常嵌套在 `result.data` 下，`biz_scene_instance_id`、`total_steps`、`task_status` 等字段优先从 `result.data` 里读取，不要假设它们平铺在顶层。
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
- 调用 `get_timeseries_data` 前先确认最新 `get_task_step({ biz_scene_instance_id, sse_client_id })` 返回的当前步数已经达到配置的 `total_steps`。正常完成路径不要额外调用 `get_task_status`；只有仿真出错或结果获取被拒绝时，才查询 `get_task_status` 留存全量状态记录。运行中只使用 `get_task_step` 返回的 `received_hydro_events` 判断是否有事件发生；不要每轮调用完整事件查询。
- **终态判定补充**：如果 `get_task_step` 已显示 `current_step >= total_steps`，但 `task_status` 仍是 `STEPPING` / `READY` / 其他非终态，不要立刻把任务当成已完成，也不要立刻启动结果导出；应继续短轮询 `get_task_step` 直到状态真正切到 `COMPLETED`，若随后转为 `FAILED` 或长时间不收敛，再调用 `get_task_status` 留存失败原因或最终状态。
- `get_timeseries_data` 现在只负责启动结果导出任务，不保证立即可下载。后续必须轮询 `get_export_status(biz_scene_instance_id)`，直到状态为 `COMPLETED`。
- 只有当 `get_export_status` 返回 `COMPLETED` 且给出 `resource_uri` 或下载地址时，才允许进入下载步骤。若状态为 `FAILED`，立即报告“结果导出失败”，不要继续生成任何图表或报告。
- 当目标是“下载结果文件到本地”而不是立刻做报告时，也必须走同一条标准链路：`get_timeseries_data -> get_export_status 轮询 -> resource_uri/下载地址 -> resources/read 或标准 HTTP GET -> 本地一次性校验`。不要把结果内容通过终端标准输入、交互式 `cat`、消息复制粘贴等方式中转。
- 如果本地已经存在同名结果文件，覆盖前先核对文件大小或数据行数；如发现明显偏小、数据行数异常少，优先视为“落盘被截断”，重新按标准链路完整下载，不要在坏文件基础上追加写入。
- 如果结果文件下载、读取、写盘或完整性校验任一步失败，立即报告“结果下载失败”，并停止后续分析、图表和报告生成。不要回退到任何默认数据、历史缓存、旧结果文件或明显残缺的数据文件继续产出结果。
- 即使 `resources/read`、MCP tool 或会话日志表面上返回了“成功”，只要当前运行环境里拿到的仍然只是被截断的文本片段、Markdown 表格片段、日志回显片段，且无法证明已经完整落盘为当前任务的原始结果文件，也必须视为“结果读取失败/结果落盘失败”。
- 只有当“当前 `biz_scene_instance_id` 的结果文件”已经完整下载、完整写盘并通过完整性校验后，才允许继续生成正式 `charts/`、`data/`、HTML 报告和 Markdown 报告。
- 如果用户要求“完整报告（charts、data、report）”，而当前任务结果尚未完整落盘，只允许交付阻塞说明、失败说明或进度说明；不允许先交付临时正式报告，也不允许拿历史任务产物补齐当前任务目录。
- 历史任务目录中的图表、数据文件、HTML/Markdown 报告只可用于内部排障、人工比对或调试参考，不可复制到新的 `biz_scene_instance_id` 目录中充当本次任务的正式交付物。
- 如果用户要做 HTML 报告或其他 HTML 页面，先读 [references/hydros-html-prompt.md](references/hydros-html-prompt.md)。
- 如果需要理解数据结构、聚合口径或指标映射，先读 [references/hydros-data-contract.md](references/hydros-data-contract.md)。
- 如果场景是 `200060`（梯级电站），在阶段五默认追加“机组分组堆叠面积图”校核；识别条件固定为 `device_type = Turbine` 且 `command_type = output_power`，并把它视为正式结果解读的必检项。
- 如果场景是 `200060`（梯级电站），`梯级电站来流-出力对比` 必须优先使用 `GateStation` 的`闸前断面` `water_flow` 作为站级来流代理，并与同站全部 `Turbine/output_power` 聚合后的总出力同图对比；不要依赖 MPC 明细里一定存在 `water_flow` 命令。
- 如果场景是 `200060`（梯级电站），正式报告默认还应追加两张更直观的协同调度图：`梯级总出力构成` 和 `机组分组堆叠面积图`。前者用于看站间分工、接力和退让，后者用于看站内机组主力承担、轮换接力以及负荷是否过度集中。
- 如果场景是 `200060`（梯级电站），`GateStation` 相关口径必须同时兼容 `电站` 和 `闸站` 两种业务分类；判定 `闸前断面` / `闸后断面` 时，优先信 `objects.yaml` 中断面或引用上的 `alias_name`（如“闸前”“闸后”），只有别名缺失时才退回 `INLET` / `OUTLET` 或断面顺序推断，避免建模口径与角色字段不一致时把站级来流代理映射反。
- 如果场景是 `200060`（梯级电站），机组分组堆叠面积图必须提供按站点分组的水轮机下拉选择；默认一次只展示一个站点的机组堆叠，不再额外渲染独立的“水轮机出力结果曲线”主卡片。
- 如果场景是 `200060`（梯级电站），机组分组堆叠面积图里的各台机组序列必须使用可区分的离散配色，图例颜色与面积/线条颜色一一对应，且序列名称必须展示可读的 `站点名/机组名`。
- 如果场景是 `200060`（梯级电站），上述新增两张图不能只生成图片本身；主页面必须同步渲染对应卡片，并给出解读内容。`梯级总出力构成` 至少要解释哪一站承担主力、总出力平台切换主要由谁驱动；`机组分组堆叠面积图` 至少要解释是否存在机组轮换、是否长期由少数机组承担主力，以及负荷集中度是否偏高。
- `水位-流量联动对比` 不能只输出“已选取若干断面做复核”这类占位式描述；正式报告里必须给出可读解读，至少包含“本次选取了哪些关键断面”“哪一个断面的联动变化最值得优先关注”“如何判断是流量主导还是水位主导”“出现异常时应优先复核什么原因”这四类信息。
- 只要进入“正式报告重生成 / 报告修复 / HTML 报告回写”路径，就必须显式保留当前场景自己的 `scenario_yaml_url`、`objects.yaml` 来源和任务参数；如果当前任务原始元数据缺失，只允许先说明阻塞或从当前任务链路重新获取，不能省略 `scenario_yaml_url` 直接重生报告，否则会把标题、场景名、时长口径和纵剖面元数据退回默认值。
- 历史任务目录下的 `report.data.js`、图表、HTML 报告只允许用于人工对比、排障和口径核对；它们可以帮助确认“哪里不一致”，但不能被直接复制、改名、拼接或作为新 `biz_scene_instance_id` 的正式 `charts/`、`data/`、`report/` 产物来源。
- 如果需要快速交付一个可直接打开的页面，优先复用模板资产，而不是从零开始。
- 需要完整版 HTML 报告、结果曲线展示或可直接打开的单文件页面时，优先复用 [assets/hydros-report-template/index.html](assets/hydros-report-template/index.html) 模板，并按当前脚本实现把真实 payload 内联到 `simulation_report.html`。
- 当用户明确要“报告”“完整报告”“HTML 报告”“汇报页”时，不要先交付临时分析报告、手写摘要页或简版 HTML 作为最终产物；如果本地结果文件尚未就位，先完成 `get_timeseries_data -> get_export_status 轮询 -> resource_uri/下载地址 -> 落盘结果文件 -> build_timeseries_report.py`，再输出遵循模板的正式报告。
- “使用了正确 HTML 模板”不等于“形成了正式报告”。正式报告必须同时满足“模板来自 `assets/hydros-report-template/index.html`”和“底层数据来自当前 `biz_scene_instance_id` 的完整结果文件”这两个条件；任一条件不满足，都只能算未完成正式交付。
- HTML 正式报告应尽量包含结果曲线图产物和渠道纵剖面图；若 `chart1_water_level.png`、`chart2_water_flow.png`、`chart4_gate_opening.png`、`chart5_disturbance_flow.png`、`chart7_longitudinal_profile.png` 中有缺失，仍可交付 HTML，但必须在报告正文里显式写明缺失项、缺失原因和影响范围，不能把缺图问题只留在聊天回复里解释。
- 正式 HTML 报告生成完成后，默认先交付本地 `simulation_report.html`。如当前环境提供并验证了报告上传工具或 API，再上传并把接口返回结果作为交付结果的一部分；不要继续使用旧 `hydroos.cn` 匿名上传地址作为默认动作。
- 如果远端上传失败，明确报告“本地报告生成成功，远端上传失败”和接口错误，不要伪装成本地报告失败。
- 直传命令模板如下。`Content-Type: multipart/form-data; boundary=...` 由 `curl --form` 自动生成，通常不要手写固定 boundary，避免请求头与 multipart 请求体不一致：

    ```bash
    # 示例：仅在已确认当前环境存在可用上传接口时使用。
    curl --location --request POST \
      "https://api.hydroos.pub/openapi/engine/api/v1/file/anonymous/upload/<biz_scene_instance_id>" \
      --header "Accept: */*" \
      --form "file=@\"output/<biz_scene_instance_id>/report/simulation_report.html\""
    ```

## 200060 全流程兜底清单

如果当前场景是 `200060`（梯级电站），在正式交付前至少完成下面 6 项自检：

1. **结果完整性**：确认当前 `biz_scene_instance_id` 的结果文件已完整落盘，且包含 `device_type = Turbine`、`command_type = output_power` 记录；不能借历史任务产物补齐。
2. **场景元数据**：确认报告重生时显式传入当前场景自己的 `scenario_yaml_url`、`objects.yaml` 来源和任务参数，避免标题、场景名和纵剖面口径退回默认值。
3. **站级口径**：确认 `GateStation` 同时兼容 `电站/闸站` 分类，且 `闸前/闸后` 优先按 `alias_name` 判定；`梯级电站来流-出力对比` 使用 `闸前断面 water_flow` 作为站级来流代理。
4. **图表生成**：确认 `chart9/10/11` 均已生成，其中 `chart10` 用于站间协同，`chart11` 用于机组分组堆叠面积展示与集中度复核；`Turbine/output_power` 数据需用于支撑 `chart11`，不再要求主页面单独展示 `chart6`。
5. **主页面渲染**：确认主页面已渲染 `梯级电站来流-出力对比`、`梯级总出力构成`、`机组分组堆叠面积图` 3 个卡片，且每个卡片都有解读文案，不是只有图片或占位说明。
6. **最终交付**：确认 HTML、Markdown、`report.data.js`、`analysis_summary.json` 与当前任务目录一致；若任一图表、解读或口径缺失，必须在报告正文和聊天结论里显式说明影响范围。

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
- 连接失败时，提示用户检查 Hydros Engine MCP 是否已配置为 `https://mcp.hydroos.pub/`，并确认 token 与必需 Header 完整。
- 如果场景建模元数据、拓扑或 `objects.yaml` 相关步骤失败，先说明元数据能力暂不可用，再继续可执行的仿真主链路；不要把旧 `hydros-engine-mdm` 双服务配置作为新用户的修复建议。
- 如果后续报 “SSE通道未建立”，用同一个 `sse_client_id` 重新订阅。

### 阶段二：查询与选择场景

1. 调用 MCP `biz_scenario_id_lists`。
2. 将场景整理为 markdown 表格，至少包含：序号、场景 ID、场景名称、核心能力。
3. 保存每个场景的 `biz_scenario_config_url`，后续创建任务时必须使用。
4. 给出推荐场景，优先描述中包含“测试”或“SDK”的场景，其次选依赖较少的场景。
5. 一旦用户明确选定某个场景（例如只回复场景 ID、场景名称，或说“就这个”“选这个”），在进入阶段三前，基于场景 YAML 里的 `hydros_objects_modeling_url` 拉取并缓存 `objects.yaml`，再默认补一段简要拓扑总结。
   下载方式固定为：
   - 读取场景 YAML，提取 `hydros_objects_modeling_url`
   - 对下载地址做 URL 规范化，兼容中文路径和特殊字符
   - 通过标准 HTTP GET 直接下载 `objects.yaml`
   - 以 UTF-8 文本形式一次性写入本地缓存文件，供本轮后续步骤复用
6. 在进入阶段三前，如 MCP 提供 `get_scenario_events`，调用它查询该场景支持注入的预置事件，并整理为简要事件清单；后续参数确认时和默认仿真参数一起展示给用户选择。若工具不可用，明确说明“当前无法读取场景预置事件，仅展示仿真参数”。

场景拓扑简要总结要求：
- 至少给出 `waterway_id`、主水网/渠道名称、对象总览（如 `UnifiedCanal`、`CrossSection`、`DisturbanceNode`、`GateStation`、`Gate` 的数量或主要成员）。
- 用 1 到 3 句话概括主链路拓扑，例如“主渠从 QD-1 依次连接到 QD-14，中间穿插若干分水口、退水闸和 2 个闸站”。
- 点出关键控制节点或特殊对象，例如 `ZM1`、`ZM2`、主要分水口、退水闸、入口断面。
- 这是“简单总结”，默认放在场景确认反馈里即可，不要等用户追问后才补。
- 如果 `objects.yaml` 暂时不可读，也要明确说明“当前无法读取对象拓扑，只展示场景基本信息”；不要静默跳过。
- 已成功拉取的 `objects.yaml` 默认视为本轮会话资产，后续生成纵剖面、拓扑页或正式报告时优先复用这份本地文件，不要再次重复拉取。

场景预置事件展示要求：
- 优先调用 MCP `get_scenario_events`，按场景 ID 查询支持注入的预置事件。
- 展示事件清单时必须带序号，默认使用 `1. 2. 3.` 这种连续编号，方便用户按序号选择或引用。
- 至少展示每个事件的名称/类型、作用对象、触发步或触发时间、是否默认启用。
- 这部分默认放在参数确认之前，与 `total_steps`、`sim_step_size`、`output_step_size` 同时出现，供用户一起决定是否按默认配置启动。
- 如果 `get_scenario_events` 返回空列表，要明确写“该场景当前无可注入预置事件”。
- 如果当前环境暂时无法调用 `get_scenario_events`，要明确写“当前无法读取场景预置事件，仅展示仿真参数”，不要静默跳过。

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
- 首次创建任务时，如可读取预置事件，还要同步展示通过 `get_scenario_events` 查询到的预置事件清单，并让用户一并确认“是否按默认事件配置启动”。
- 如果用户在同一条消息中已经给出了所有参数（如”用默认参数启动”、”步数 800”），直接创建任务，不再额外确认。
- 如果用户只给出场景 ID / 场景名称，而没有明确说”用默认参数启动””直接运行”或没有显式提供参数值，必须先停在参数确认这一步，不能自动创建任务。

#### 获取场景配置参数的降级策略

在向用户确认参数前，需要先获取场景的默认配置。按以下顺序尝试：

1. **WebFetch**: 尝试用 WebFetch 直接获取 `biz_scenario_config_url` 的内容
2. **Bash + curl**: 如果 WebFetch 失败（网络限制、企业安全策略等），用 `curl -s <url>` 获取
3. **MCP 水网对象**: 如果 HTTP 请求都失败，尝试调用 MCP `get_waterway_lists` 获取水网配置（可能包含相关参数）
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
5. 如 MCP 提供 `get_scenario_events`，调用它获取该场景支持注入的预置事件；如果失败，必须在反馈中明确说明。
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
   反例：如果当前 MCP 明确支持 `get_scenario_events`，但还没有给出预置事件清单，也不能直接调用 `create_simulation_task`。
7. 用户确认后，调用 `create_simulation_task`。
8. 保存并展示：
   - `biz_scene_instance_id`
   - `task_status`
   - `total_steps`
   - `default_render_objects`
   - `valid`
9. 如果这是 live 任务，创建成功后立即进入阶段四持续监测，直到 `get_task_step` 返回的当前步数达到 `total_steps`，或异常路径已经用 `get_task_status` 留存失败记录；不要在首个进度点就结束本轮处理。
10. 对”启动 100001””运行这个场景”这类明确启动指令，默认把”持续监测到终态”视为同一轮动作的一部分，不需要再次征询用户。
11. 创建成功后的第一条反馈应直接包含任务 ID、创建成功事实、当前进度和“正在持续监测中”的事实；不要把“是否继续盯进度”作为可选后续动作抛给用户。
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

使用 `get_task_step({ biz_scene_instance_id, sse_client_id })` 轮询当前步数。常规进度跟踪不调用 `get_task_status`；只有仿真出错、步数查询异常、或需要留存失败原因时，才调用 `get_task_status({ biz_scene_instance_id, sse_client_id })` 获取全量状态记录。

运行中事件监测规则：
- 常规进度监测只读取 `get_task_step` 返回的 `received_hydro_events`，用它判断是否已有事件发生。
- 如果 `received_hydro_events` 相比上一轮出现新增事件，在进度播报中简要写明事件名称、触发步和事件 ID。
- 运行中不要每轮调用 `get_simulation_scenario_events`，因为完整事件记录可能包含天气时序、预警规则等大体量详情。
- 只有用户明确要求展开事件详情，或出现异常/结果突变需要解释时，才调用 `get_simulation_scenario_events(biz_scene_instance_id)`。

`get_task_step` 传参要求：
- `biz_scene_instance_id`：仿真任务实例 ID，来自 `create_simulation_task` 返回结果。
- `sse_client_id`：SSE 客户端 ID，必须与本任务创建前调用 `subscribe_to_simulation_events` 时使用的值一致。

标准监测方法：

1. 创建 live 任务成功后，立刻进入轮询循环。
2. 每一轮轮询必须至少执行一次 `get_task_step({ biz_scene_instance_id, sse_client_id })`。
4. 每一轮都要用最新一次 `get_task_step` 的结果更新“最新可信进度”：
   - 进度以 `get_task_step` 返回的当前步数为准。
   - 已发生事件以 `get_task_step.received_hydro_events` 为准；只记录和播报新增事件摘要，不拉取完整事件详情。
   - `total_steps` 使用创建任务时确认的配置值；不要为了读取 `total_steps` 而常规调用 `get_task_status`。
   - 当 `current_step >= total_steps` 但 `task_status` 还不是 `COMPLETED` / `FAILED` / `CANCELLED` / `PAUSED` 时，视为“到达末步但尚未收敛到终态”，继续短轮询，不要提前结束。
   - 只有当 `task_status` 真正进入终态，或异常路径已经通过 `get_task_status` 留存了全量失败记录，才结束持续监测。
   - 若 `get_task_step` 调用失败、返回异常、或 SSE/任务事件显示仿真失败，再调用一次 `get_task_status({ biz_scene_instance_id, sse_client_id })` 获取全量状态和 `failure_exception`，作为错误记录。

5. 如果已经停止轮询，或当前回合不会继续执行下一轮，则必须明确表述为“本轮已查询到最新进度”，不能伪装成持续监测。
6. 推荐轮询间隔为 5 到 10 秒；如果任务步进非常快，可缩短到 2 到 5 秒，但不能只查一次就结束。
7. “持续监测完成”的判定只有两种：
   - `get_task_step` 返回的当前步数达到或超过 `total_steps`
   - 仿真出错后已用 `get_task_status` 留存全量失败记录
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
- 如果回复里出现“最新进度”“当前进度”“正在监测”，必须能指出最近一次真实 `get_task_step` 查询得到的 `current_step`；只有异常路径才补充 `get_task_status` 返回的 `task_status` 或 `failure_exception`。
- 如果回复里提到“事件已发生”或“新增事件”，必须来自最近一次真实 `get_task_step.received_hydro_events`，或来自按需调用的 `get_simulation_scenario_events`。
- 不允许只在创建任务后说一句“我会持续监测”，然后没有后续轮询动作。

状态流转仅用于理解任务生命周期；常规进度轮询不依赖 `get_task_status` 获取这些状态：

```text
INIT -> WAITING_AGENTS -> READY -> STEPPING -> COMPLETED
                                             -> FAILED
```

展示要求：
- 对运行中任务必须展示文本进度条快照、`current_step / total_steps` 和百分比。
- 文本进度条快照默认使用 10 格宽度，格式固定为 `███░░░░░░15.4% | 185/1200`；已完成部分用 `█`，未完成部分用 `░`，百分比保留 1 位小数。
- 任何包含当前进度、最新进度、正在监测等内容的回复，第一行都必须先给出这条文本进度条；后面才允许补 ETA 或说明。
- 在追加消息型聊天环境中，每一条进度播报都应把最新文本进度条放在回复第一行，后面再补状态、ETA 或说明；不要把进度条埋在长段解释后面。
- 当任务首次进入 `STEPPING` 状态时，在进度输出中附带一句提示，告知用户当前速度和预估剩余时间；如果此时样本还不足以给出可靠 ETA，就明确写“暂未形成可靠 ETA”。同时说明可以随时输入"加速"或"4x"来调整倍速（可选：0.25x、0.5x、1x、2x、4x）。这条提示只出现一次，之后不再重复。关键点：不要用阻塞式提问（如 AskUserQuestion）来询问加速，因为那会中断轮询循环，导致监测停止。正确做法是把加速提示作为进度输出的一部分，然后立即继续轮询；如果用户在后续消息中主动要求加速，再调用 `update_task_speed`。
- 对异常或失败状态，调用 `get_task_status` 后优先提取 `failure_exception`。
- 对 live 任务，默认持续轮询 `get_task_step`，直到当前步数达到 `total_steps`；在达到前不要把流程当作完成。
- 禁止把“继续等待”“继续盯进度”“稍后再查”“是否拉结果”写成三选一或多选一的尾句；正确做法是继续轮询，并在终态后再自然衔接结果获取或报告生成。
- 如果用户的意图是“跑一个仿真并看结果/出报告/继续等待”，则任务完成后应自动衔接阶段五，无需再次等待用户提醒。
- 如果运行环境是命令行 PTY，而不是聊天消息流，优先用单行文本进度条展示，如 `██████░░░░34.0% | 408/1200`，通过 `\r` 原地刷新；但对用户可见的进度文本格式仍必须与聊天环境保持一致。
- 如果当前环境只能追加消息，则每次进度播报也必须使用同样格式的“文本进度条快照”，例如 `███░░░░░░15.4% | 185/1200`；不要只发纯数字快照如 `185/1200`，也不要假装实现原地覆盖。
- 如果用户明确要求“进度条”，且当前环境不是 PTY，就把需求降级解释为“文本进度条快照 + 持续追加播报”；但降级后显示格式仍必须完全一致，不能换成别的样式。

### 阶段五：获取结果与分析

1. **数据获取**：
    - 确认最新 `get_task_step({ biz_scene_instance_id, sse_client_id })` 返回的当前步数已经达到 `total_steps`
    - 如果步数未达标、步数查询异常、或结果获取返回任务失败/未完成，再调用 `get_task_status({ biz_scene_instance_id, sse_client_id })` 查询全量状态并记录原因
    - 调用 `get_timeseries_data(biz_scene_instance_id)` 启动结果导出任务
    - 持续轮询 `get_export_status(biz_scene_instance_id)`，直到状态为 `COMPLETED` 或 `FAILED`
    - 当状态为 `COMPLETED` 时，优先提取 `resource_uri`；只有明确给出可访问下载地址时再提取 HTTP 下载地址
    - 对 `hydroengine://downloads/...` 这类 `resource_uri`，优先通过 MCP `resources/read` 读取结果，再落盘或解析。只有当返回的是明确可访问的 HTTP 下载地址时，才使用标准 HTTP GET 将结果文件一次性落盘到本地：

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
    - 即使 `resources/read`、`get_mpc_simulation_results` 或其他结果读取调用返回成功，只要代理当前真正拿到的仍是片段化文本、被截断的 Markdown 表格、日志回显或无法验证完整性的中间内容，也一律按“坏文件/残缺文件/未完整落盘”处理，直接停止阶段五。
    - 阶段五产出的 `charts/`、`data/`、`report/` 必须与当前 `biz_scene_instance_id` 一一对应；如果当前任务结果拿不全，就直接报告失败或阻塞，不允许借用、复制或改名历史任务目录下的产物来凑齐当前任务交付。
    - 传递用户显式提供的仿真参数给脚本，避免写死默认值
    - 如果场景是 `200060`，导出结果后必须检查是否存在 `device_type = Turbine` 且 `command_type = output_power` 的记录；若缺失，明确报告“结果导出未包含水轮机出力数据 / 结果导出不完整”，不要静默跳过

2. **完整事件记录**：
    - 生成正式报告、事件复盘或运行过程记录时，调用 `get_simulation_scenario_events(biz_scene_instance_id)` 获取完整工况事件。
    - 用户明确要求“查看事件”“工况事件”“过程记录”“事件详情”时，调用 `get_simulation_scenario_events(biz_scene_instance_id)`。
    - 若结果曲线出现突变、仿真异常、MPC 控制结果异常，或需要解释某个事件对对象/时序的影响，调用完整事件查询做关联分析。
    - 不要把 `get_simulation_scenario_events` 放入阶段四高频轮询；阶段四只用 `received_hydro_events` 轻量判断事件是否发生。

3. **统计摘要**：生成总记录数、采样步数、对象数、指标数、异常点数量；若场景是 `200060`，额外统计水轮机出力序列数、缺失情况和最大出力变化机组。

4. **图表生成**：用 `scripts/generate_charts.py` 生成水位、流量、闸门开度和分水口流量等图表。注意 y 轴自适应收紧。若场景是 `200060`，额外生成以下正式报告图表，并把它们视为场景交付的一部分：
    - `chart9_station_inflow_power_comparison.png`：梯级电站来流-出力对比；要求使用 `GateStation/闸前断面/water_flow` 作为站级来流代理。
    - `chart10_station_output_composition.png`：梯级总出力构成；用于看站间分工、接力和退让。
    - `chart11_turbine_dispatch_heatmap.png`：机组分组堆叠面积图；用于看站内机组主力承担、轮换接力和负荷集中度，主页面需提供按站点分组的水轮机下拉切换。

5. **异常分析**：用 `scripts/analyze_anomalies.py` 检测负压、流速异常、水头损失等。

6. **报告生成**：
   - **默认产出**：HTML 报告 + Markdown 报告（除非用户明确只要其中一种）
  - **HTML 报告**：对齐 `assets/hydros-report-template/index.html` 完整版结构，包含纵剖面与时序曲线联动；对 `200060` 必须额外展示梯级电站来流-出力对比、梯级总出力构成和机组分组堆叠面积图，其中机组图按站点分组下拉切换。
  - **主页面渲染**：对 `200060`，新增图表不能只生成图片文件；主页面必须同步渲染对应卡片、标题、说明文字和解读内容，不能只在 Markdown 里补充说明。
  - **图表实现口径**：`chart8/9/10/11` 在 HTML 主页面必须优先走 `reportData.charts.* + ECharts` 渲染，不能再用静态 `<img>` 作为主展示实现；`charts/*.png` 仅作为 Markdown 报告、离线归档和缺省交付产物保留。
  - **Markdown 报告**：图文并茂，每张图表配套文字分析
  - **目录结构**：统一落盘到 `output/<biz_scene_instance_id>/`；其中 `report/` 存放报告，`charts/` 存放图表，`data/` 存放结果文件、`objects.yaml` 和分析中间文件
  - **数据验证**：比较期望与实际的时长/点数，不一致时在报告中说明；对 `200060` 还要验证是否包含 `Turbine/output_power` 数据、`chart9/10/11` 是否全部生成、对应解读是否已写入 payload 并在主页面渲染，且 `chart11` 是否支持按站点分组的下拉切换。任一项缺失，都必须在 HTML/Markdown 正文和聊天结论里同时说明。
   - **上传交付**：当 HTML 正式报告生成完成后，默认先交付本地 `simulation_report.html`。如当前环境提供并验证了报告上传工具或 API，再上传并把接口返回结果作为交付结果的一部分。
   - **上传命令示例**：

     ```bash
     # 仅在当前环境确认该上传接口可用时执行。
     curl --location --request POST \
       "https://api.hydroos.pub/engine/api/v1/file/anonymous/upload/<biz_scene_instance_id>" \
       --header "Accept: */*" \
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
| “查看进度” / “任务状态” | 执行阶段四；常规只查 `get_task_step`，异常时再查 `get_task_status` |
| “查看事件” / “工况事件” / “过程记录” / “事件详情” | 调用 `get_simulation_scenario_events` 获取完整事件记录 |
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
current_step
total_steps
received_hydro_events
default_render_objects
```

