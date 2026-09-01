# astrbot_plugin_shared_life_context

共享生活状态上下文核心插件，为主动对话、QQ 空间动态、表情选择和未来人格行为提供统一的 `shared_life_context`。

当前版本 `0.5.0` 将刷新逻辑拆成两层：

- 每日 `daily_plan` 层：只生成今日粗粒度生活节奏和跨日 bias。
- 分时段 `period_refresh` 层：只刷新当前时段生活残留和当前状态。

`/slc set`、`/slc set_plan` 仍然只是人工修正接口，不是主路径。

## Fix

- `0.5.1` 修复 AstrBot object schema 缺失 `items` 导致插件安装失败。

## 安装

将目录放入 AstrBot 插件目录，例如：

```text
AstrBot/data/plugins/astrbot_plugin_shared_life_context/
```

数据优先写入：

```text
data/plugin_data/astrbot_plugin_shared_life_context/
```

如果 AstrBot 数据目录不可用，则回退到：

```text
astrbot_plugin_shared_life_context/data/
```

## 数据文件

```text
shared_life_context.json
shared_life_memory.json
```

`shared_life_context.json` 是当前导出的共享上下文。

`shared_life_memory.json` 是上游连续记忆层，用于防重复和制造轻微连续性。

## 两层刷新

### 每日 daily 层

触发方式：

- 后台命中 `auto_refresh_time`
- 手动 `/slc auto_refresh`
- 启动时 `auto_refresh_on_startup=true`

只负责写入：

```text
daily_plan
carry_over_trace
latent_topic_bias
chat_style_bias
association_style_bias
topic_bias
last_daily_plan_refresh_at
```

每日层不会刷新：

```text
current_period
current_activity
micro_experience
ambient_mood
life_state
energy_level
activity_hint
mood_hint
social_hint
relationship_hint
```

这样可以避免 `daily_plan` 在一天里被多次重抽。

### 分时段 period 层

触发方式：

- 后台命中 `period_refresh_times`
- 手动 `/slc period_refresh`
- 手动 `/slc auto_refresh` 顺手刷新一次当前 period

只负责写入：

```text
current_period
current_activity
micro_experience
ambient_mood
life_state
energy_level
activity_hint
mood_hint
social_hint
relationship_hint
last_period_refresh_at
last_period_key
```

分时段层不会覆盖：

```text
daily_plan
carry_over_trace
latent_topic_bias
chat_style_bias
association_style_bias
topic_bias
```

## 时段规则

默认：

```json
{
  "morning": "08:30",
  "afternoon": "14:00",
  "evening": "20:00",
  "night": "00:30"
}
```

含义：

- `08:30` 后为 `上午`
- `14:00` 后为 `下午`
- `20:00` 后为 `晚上`
- `00:30` 后为 `深夜`

`period_refresh` 会根据当前真实时间和配置窗口强制设置 `current_period`，不会盲信 LLM 输出。

## 命令

```text
/slc show
/slc json
/slc set <field> <value>
/slc set_plan <morning|afternoon|evening|night> <value>
/slc add_topic <topic>
/slc remove_topic <topic>
/slc plan
/slc prompt
/slc qzone_prompt
/slc chat_context
/slc memory
/slc add_trace <trace>
/slc regen_memory
/slc reset_memory
/slc auto_status
/slc auto_refresh
/slc period_status
/slc period_refresh
/slc reset
```

新增：

- `/slc period_refresh`：手动刷新当前时段状态，不覆盖 `daily_plan`。
- `/slc period_status`：查看分时段刷新配置、当前时段、上次刷新和下次刷新提示。

`/slc auto_refresh` 现在表示“每日完整刷新入口”：先刷新 daily 层，再顺手刷新当前 period 层。

## 配置项

```text
auto_refresh_enabled
auto_refresh_time
auto_refresh_on_startup
auto_refresh_min_interval_hours
period_refresh_enabled
period_refresh_times
period_refresh_min_interval_hours
timezone
llm_provider_id
daily_life_prompt
period_life_prompt
enable_chat_context_export
```

`daily_life_prompt` 留空时使用内置每日层 prompt。

`period_life_prompt` 留空时使用内置分时段 prompt。

`llm_provider_id` 支持 `_special=select_provider`，留空时使用当前会话 provider 或 AstrBot 默认 provider。

## period_life_prompt 边界

分时段 prompt 必须强调：

```text
只刷新当前时段生活残留，不生成今日计划。
不要输出 daily_plan。
不要写具体事件。
不要写精确时间线。
不要把状态写成正在做某事。
优先生成当前时段合理的生活残留、感官小事、语气倾向。
```

允许输出字段只有：

```json
{
  "current_period": "...",
  "current_activity": {
    "value": "...",
    "mode": "background_only",
    "do_not_push_as_topic": true
  },
  "micro_experience": "...",
  "ambient_mood": "...",
  "life_state": "...",
  "energy_level": "高/中/低",
  "activity_hint": "...",
  "mood_hint": "...",
  "social_hint": "...",
  "relationship_hint": "..."
}
```

如果 LLM 输出 `daily_plan` 或其他未知字段，`period_refresh` 会失败且不覆盖原 context。

## 核心字段

`daily_plan` 是 `coarse ambient rhythm`，只表示粗粒度背景节奏，不是剧情日程。

`current_activity` 是 background-only 生活残留：

```json
{
  "value": "下午练琴后手还有点酸",
  "mode": "background_only",
  "do_not_push_as_topic": true
}
```

新增 metadata：

```text
last_daily_plan_refresh_at
last_period_refresh_at
last_period_key
```

保留：

```text
last_auto_refresh_at
```

用于兼容旧逻辑。

## 导出规则

`/slc prompt`、`/slc qzone_prompt`、`/slc chat_context` 都包含：

```text
用户输入优先级 > 当前对话情绪 > shared_life_context
```

以及默认忽略规则：

```text
默认不要主动使用 shared_life_context 内容。
只有当它能自然增加一句回复真实感时，才允许轻微引用其中一个细节。
如果一句回复不需要它，应完全忽略它。
背景不是必须使用的信息。
不要因为看见某个生活状态，就试图把它带进回复。
```

QQ 空间 prompt 优先使用最新 period 层的：

```text
micro_experience
ambient_mood
current_activity
```

这样白天发动态时不会继续引用昨晚的“深夜”“空空的”“舌根涩味”等过期状态。

## 安全边界

- 不修改主 prompt
- 不修改其他插件
- 不发送 QQ 空间
- 不直接请求外部网络
- 不保存敏感隐私
- 自动刷新失败不覆盖原 context
- JSON 损坏会备份为 `.broken.<timestamp>.json`
- 读 JSON 兼容 UTF-8 BOM
- 写文件使用 tmp + replace 原子写入
