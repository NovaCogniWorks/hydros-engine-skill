# hydros-engine-skill

基于 hydros-engine-executor MCP 服务的 Claude Code Skill，编排 MCP 工具调用，引导用户完成水力仿真场景查询、任务运行、进度跟踪和结果分析的完整流程。

## 定位

```
┌──────────────────────────────────────────────────┐
│                   Claude Code                     │
│                                                   │
│  ┌─────────────────────┐                          │
│  │ hydros-engine-skill │  ◄── 本项目（Skill）      │
│  │  • 流程编排          │                          │
│  │  • 智能分析          │                          │
│  │  • 图表生成          │                          │
│  └────────┬────────────┘                          │
│           │ 调用 MCP 工具                          │
│           ▼                                       │
│  ┌─────────────────────┐                          │
│  │ hydros-engine-      │  ◄── 外部提供的 MCP 服务  │
│  │ executor            │                          │
│  │  • biz_scenario_id_ │                          │
│  │    lists            │                          │
│  │  • create_simulation│                          │
│  │    _task            │                          │
│  │  • get_task_status  │                          │
│  │  • get_timeseries_  │                          │
│  │    data             │                          │
│  └────────┬────────────┘                          │
└───────────┼──────────────────────────────────────┘
            │ SSE / HTTP
            ▼
     ┌──────────────┐
     │ hydros-engine │  水力仿真引擎
     └──────────────┘
```

**hydros-engine-executor**（外部服务）提供一组面向水力仿真的 MCP 工具，常用的包括：

| MCP 工具 | 功能 |
|----------|------|
| `biz_scenario_id_lists` | 获取场景清单及配置 URL |
| `subscribe_to_simulation_events` | 建立仿真订阅通道 |
| `create_simulation_task` | 创建仿真任务 |
| `get_task_status` | 跟踪仿真进度、状态和异常信息 |
| `update_task_speed` | 调整仿真运行倍速 |
| `get_timeseries_data` | 查询并导出水网对象时序数据 |

**hydros-engine-skill**（本项目）是一个 Claude Code Skill，职责是：
- 编排上述 MCP 工具的调用顺序和逻辑
- 引导用户完成从场景选择到结果分析的完整工作流
- 对返回的仿真结果数据进行图表生成和准确性分析

---

## Skill 核心能力

### 能力 1：场景查询与展示

**触发：** 用户询问可用仿真场景、模型列表等

**Skill 行为：**
1. 调用 MCP 工具 `biz_scenario_id_lists` 获取场景清单和配置地址
2. 读取场景配置，补充默认参数、预置事件和简要拓扑信息
3. 引导用户选择目标场景，并确认是否按默认参数启动

**示例对话：**
```
用户：有哪些可用的仿真场景？
Skill：调用 biz_scenario_id_lists → 返回格式化列表
      "当前可用场景包括：
       1. 京石段-感知演示场景
       2. 京石段-天气预报/用水计划/故障等综合场景
       3. 京石段-SDK-测试
       请选择要运行的场景，或直接告诉我场景 ID。"
```

---

### 能力 2：仿真任务创建与进度跟踪

**触发：** 用户选择场景并要求运行仿真

**Skill 行为：**
1. 根据用户选择，先调用 `subscribe_to_simulation_events` 建立订阅通道
2. 再调用 `create_simulation_task` 创建仿真任务
3. 创建成功后持续调用 `get_task_status` 轮询进度和状态
4. 自动向用户播报文本进度条、当前步数、状态和异常信息
5. 如有需要，可调用 `update_task_speed` 调整运行倍速

**示例对话：**
```
用户：运行场景 1 的延时仿真，24小时
Skill：调用 subscribe_to_simulation_events → create_simulation_task
      "仿真任务已提交 (TASK_xxx)，正在持续监测中..."
      "████░░░░░░40.0% | 240/600"
      "██████████100.0% | 600/600
       仿真完成，开始读取结果并生成报告。"
```

---

### 能力 3：结果查询、图表生成与数据分析

**触发：** 仿真完成后，用户要求查看结果或分析数据

**Skill 行为：**
1. 调用 `get_timeseries_data` 获取结果文件地址，再读取完整 CSV
2. **生成可视化图表：**
   - 水位、流量、闸门开度时序曲线
   - 热力图、纵剖面图、场景拓扑图
   - 统计概览图和异常摘要图
3. **数据准确性分析：**
   - 水位、流量、闸门开度异常检测
   - 沿程水头损失和关键断面变化分析
   - 关键事件、故障影响和运行风险提示
4. **生成分析结论：** 汇总异常、给出调度和汇报建议

**示例对话：**
```
用户：整体分析一下这次仿真结果
Skill：调用 get_timeseries_data → 读取 CSV → 生成图表和分析报告
      "本次仿真共输出 N 条记录，覆盖 600 步。
       已生成水位、流量、闸门、热力图和纵剖面图，
       并汇总出异常点、关键发现和建议。"
```

---

## Skill 工作流总览

```
用户意图识别
  │
  ├─ "查看场景" ──────► 调用 biz_scenario_id_lists
  │                        │
  │                        ▼
  │                   读取场景配置 / 预置事件 / 拓扑摘要
  │
  ├─ "运行仿真" ──────► 调用 subscribe_to_simulation_events
  │                         │
  │                         ▼
  │                    调用 create_simulation_task
  │                         │
  │                         ▼
  │                    调用 get_task_status 持续轮询
  │                         ▼
  │                    仿真完成后自动进入结果阶段
  │
  └─ "查看/分析结果" ──► 调用 get_timeseries_data
                            │
                       ┌────┴─────────────┐
                       │                   │
                       ▼                   ▼
                  生成图表            数据异常分析
                  （水位/流量/         （水位/流量/
                   热力图/纵剖面）      水头损失等）
                       │                   │
                       └────────┬──────────┘
                                ▼
                          汇总展示给用户
```

## Skill 触发条件

当用户的请求涉及以下关键词/意图时，触发本 Skill：

- 水力仿真、水网模拟、管网分析
- 仿真场景、场景列表
- 运行仿真、启动计算、创建仿真任务
- 仿真结果、时序数据、压力/流量/水头查询
- 管网数据分析、结果图表

## Skill 配置

本 Skill 依赖外部 MCP 服务 `hydros-engine-executor`，需在 Claude Code 的 MCP 配置中添加该服务连接。

```json
{
  "mcpServers": {
    "hydros-engine-executor": {
      "url": "https://hydroos.cn/mcps/hydros-engine-executor"
    }
  }
}
```

## 项目结构

```
hydros-engine-skill/
├── README.md
└── skills/
    ├── hydros-engine-skill-executor/
    │   ├── SKILL.md
    │   ├── assets/
    │   ├── references/
    │   └── scripts/
    └── hydros-engine-skill-analyst/
        ├── SKILL.md
        ├── references/
        └── scripts/
```
