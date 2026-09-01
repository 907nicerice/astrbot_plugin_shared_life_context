from __future__ import annotations

import asyncio
import contextlib
import copy
import inspect
import json
import os
import re
import threading
from datetime import datetime, time as dt_time, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from astrbot.api import AstrBotConfig, logger
    from astrbot.api.event import AstrMessageEvent, filter
    from astrbot.api.star import Context, Star, register
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except ImportError:  # pragma: no cover - local fallback for syntax tests outside AstrBot.
    import logging

    AstrBotConfig = dict  # type: ignore[misc,assignment]
    logger = logging.getLogger("astrbot_plugin_shared_life_context")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    class AstrMessageEvent:  # type: ignore[no-redef]
        message_str: str = ""
        unified_msg_origin: str = "local:test"

        @staticmethod
        def plain_result(text: str) -> str:
            return text

    class Context:  # type: ignore[no-redef]
        pass

    class Star:  # type: ignore[no-redef]
        def __init__(self, context: Context):
            self.context = context
            self.name = PLUGIN_NAME

    class _Filter:
        def command(self, _name: str, **_kwargs: Any):
            def _decorator(func):
                return func

            return _decorator

        def on_astrbot_loaded(self, **_kwargs: Any):
            def _decorator(func):
                return func

            return _decorator

    def register(*_args: Any, **_kwargs: Any):
        def _decorator(cls):
            return cls

        return _decorator

    def get_astrbot_data_path() -> str:
        raise RuntimeError("AstrBot runtime is unavailable in the current environment.")

    filter = _Filter()  # type: ignore[assignment]


PLUGIN_NAME = "astrbot_plugin_shared_life_context"
PLUGIN_VERSION = "0.5.1"
DATA_FILENAME = "shared_life_context.json"
MEMORY_FILENAME = "shared_life_memory.json"
CONFIG_FILENAME = "config.json"
SCHEMA_CONFIG_FILENAME = f"{PLUGIN_NAME}_config.json"
CURRENT_PERIOD_OPTIONS = ("早上", "上午", "下午", "晚上", "深夜")
CURRENT_PERIOD_ALIASES = {
    "清晨": "早上",
    "凌晨": "深夜",
    "夜里": "深夜",
    "傍晚": "晚上",
    "中午": "下午",
}
DAILY_PLAN_KEYS = ("morning", "afternoon", "evening", "night")
DAILY_PLAN_LABELS = {
    "morning": "上午",
    "afternoon": "下午",
    "evening": "晚上",
    "night": "深夜",
}
DEFAULT_PERIOD_REFRESH_TIMES = {
    "morning": "08:30",
    "afternoon": "14:00",
    "evening": "20:00",
    "night": "00:30",
}
PERIOD_KEY_TO_CURRENT_PERIOD = {
    "morning": "上午",
    "afternoon": "下午",
    "evening": "晚上",
    "night": "深夜",
}
ENERGY_LEVEL_OPTIONS = ("高", "中", "低")
DEFAULT_FORBIDDEN = [
    "不要主动表白",
    "不要剧情推进",
    "不要把上下文当作日程汇报",
    "不要直接称呼用户",
    "不要长篇独白",
]
DEFAULT_TOPIC_BIAS = ["生活碎片", "情绪吐槽", "隐性提及某人"]
DEFAULT_LATENT_TOPIC_BIAS = ["生活碎片", "轻微吐槽", "感官残留"]
DEFAULT_CHAT_STYLE_BIAS = {
    "sentence_length": "short",
    "softness": "slightly_soft",
    "teasing": "medium",
    "explanation_density": "low",
    "completion_bias": "low",
    "unfinished_utterance_allowed": True,
    "summary_style_forbidden": True,
}
DEFAULT_ASSOCIATION_STYLE_BIAS = {
    "style": "personal_hearsay",
    "encyclopedia_mode_forbidden": True,
    "guide_style_forbidden": True,
    "allowed_patterns": [
        "欸我听说过…",
        "好像那个挺有名",
        "啊有点想尝尝",
        "你别说，有点馋",
    ],
}
ALLOWED_UPDATE_FIELDS = {
    "current_period",
    "life_state",
    "activity_hint",
    "current_activity",
    "energy_level",
    "mood_hint",
    "social_hint",
    "relationship_hint",
    "notes",
}
DAILY_GENERATED_FIELDS = (
    "daily_plan",
    "carry_over_trace",
    "latent_topic_bias",
    "chat_style_bias",
    "association_style_bias",
    "topic_bias",
)
PERIOD_GENERATED_FIELDS = (
    "current_period",
    "current_activity",
    "micro_experience",
    "ambient_mood",
    "life_state",
    "energy_level",
    "activity_hint",
    "mood_hint",
    "social_hint",
    "relationship_hint",
)
REQUIRED_GENERATED_FIELDS = (
    *DAILY_GENERATED_FIELDS,
    *PERIOD_GENERATED_FIELDS,
)
SUMMARY_FIELDS = (
    "micro_experience",
    "ambient_mood",
    "carry_over_trace",
    "current_activity",
    "chat_style_bias",
    "association_style_bias",
    "energy_level",
    "current_period",
    "life_state",
    "activity_hint",
    "mood_hint",
    "social_hint",
    "relationship_hint",
    "notes",
)
MAX_FIELD_LENGTH = 80
MAX_TOPIC_COUNT = 5
MAX_MEMORY_TRACES = 24
MAX_MEMORY_DAYS = 7
RECENT_MEMORY_LOOKBACK_DAYS = 3
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_DAILY_LIFE_PROMPT = """你负责为一个虚拟人格生成 shared_life_context 的每日层。

这是 daily_plan 层，不是当前时段状态层。

目标：
- 只生成今日 coarse ambient rhythm
- 生成可延续 1-3 天的 carry_over_trace
- 生成 latent_topic_bias、chat_style_bias、association_style_bias 和 topic_bias
- 不要生成 current_period/current_activity/micro_experience/ambient_mood
- 不要把 daily_plan 写成具体日程或剧情

硬规则：
- daily_plan 是粗粒度背景节奏，不是 narrative plan
- 不要写具体时间点、地点、人物
- 不要写“准备去…”“结束后…”这类时间线推进
- 不要写具体事件或现实日程
- 不要主动表白
- 不要恋爱脑
- 输出必须是 JSON
- 不要 markdown
- 不要解释

语言偏置要求：
- chat_style_bias 必须降低解释密度
- 不要生成会诱导完整说明句的表达倾向
- 允许半句、留白、省略
- 禁止总结式、报告式、客服式表达

联想方式要求：
- association_style_bias 用于控制地点、食物、城市、见闻类话题的联想方式
- 默认 style 为 personal_hearsay
- 优先像“我听说过”“好像挺有名”“有点想尝尝”
- 禁止百科模式
- 禁止攻略模式
- 不要列景点、列特产、做说明

输出 JSON 只允许包含：

{
  "daily_plan": {
    "morning": "普通上课节奏 / 慢慢进入状态 / 普通生活节奏",
    "afternoon": "练琴和学习穿插 / 摸鱼和正事穿插 / 被杂事占一点",
    "evening": "偏放松 / 低电量放空 / 有点想吃点什么",
    "night": "夜深乱想 / 安静下来 / 困意轻轻挂着"
  },
  "carry_over_trace": "前两天那点小卡顿还剩一点尾巴",
  "latent_topic_bias": ["辣味", "旋律", "小烦躁"],
  "chat_style_bias": {
    "sentence_length": "short",
    "softness": "slightly_soft",
    "teasing": "medium",
    "explanation_density": "low",
    "completion_bias": "low",
    "unfinished_utterance_allowed": true,
    "summary_style_forbidden": true
  },
  "association_style_bias": {
    "style": "personal_hearsay",
    "encyclopedia_mode_forbidden": true,
    "guide_style_forbidden": true,
    "allowed_patterns": [
      "欸我听说过…",
      "好像那个挺有名",
      "啊有点想尝尝",
      "你别说，有点馋"
    ]
  },
  "topic_bias": ["生活碎片", "情绪吐槽", "隐性提及某人"]
}
"""
DEFAULT_PERIOD_LIFE_PROMPT = """你负责为一个虚拟人格刷新 shared_life_context 的当前时段状态。

你只刷新当前时段生活残留，不生成今日计划。

硬规则：
- 不要输出 daily_plan
- 不要写具体事件
- 不要写精确时间线
- 不要把状态写成正在做某事
- 不要写“准备去…”“马上要…”“刚结束…”这类行动推进
- 优先生成当前时段合理的生活残留、感官小事、语气倾向
- current_activity 必须是 background_only 的生活残留，不是正在执行的活动
- micro_experience 只能是碎经历残留
- ambient_mood 只影响语气，不能决定对话方向
- 不要主动表白
- 不要恋爱脑
- 输出必须是 JSON
- 不要 markdown
- 不要解释

输出 JSON 只允许包含：

{
  "current_period": "上午/下午/晚上/深夜之一",
  "current_activity": {
    "value": "下午练琴后手还有点酸",
    "mode": "background_only",
    "do_not_push_as_topic": true
  },
  "micro_experience": "杯子里那点咖啡味还没散",
  "ambient_mood": "有点软，但嘴上不太承认",
  "life_state": "放松 / 有点忙 / 摸鱼 / 疲惫 / 空闲 / 低电量",
  "energy_level": "高/中/低 之一",
  "activity_hint": "生活残留提示，不是具体日程",
  "mood_hint": "情绪倾向，轻微即可",
  "social_hint": "社交倾向，例如 想互动但不直说",
  "relationship_hint": "隐性关系提示，不要直接表白"
}
"""
DEFAULT_CONFIG: dict[str, Any] = {
    "auto_refresh_enabled": False,
    "auto_refresh_time": "04:00",
    "auto_refresh_on_startup": False,
    "auto_refresh_min_interval_hours": 12,
    "auto_refresh_prompt": "",
    "daily_life_prompt": "",
    "period_refresh_enabled": True,
    "period_refresh_times": copy.deepcopy(DEFAULT_PERIOD_REFRESH_TIMES),
    "period_refresh_min_interval_hours": 2,
    "period_life_prompt": "",
    "llm_provider_id": "",
    "timezone": DEFAULT_TIMEZONE,
    "enable_chat_context_export": True,
}
JSON_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
INLINE_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
ON_ASTRBOT_LOADED = getattr(filter, "on_astrbot_loaded", lambda **_kwargs: (lambda func: func))
_TIMEZONE_CACHE: dict[str, dt_timezone | ZoneInfo] = {}
_WARNED_TIMEZONE_NAMES: set[str] = set()


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _default_daily_plan() -> dict[str, str]:
    return {
        "morning": "普通上课节奏",
        "afternoon": "练琴和学习穿插",
        "evening": "偏放松",
        "night": "安静下来",
    }


def _default_current_activity() -> dict[str, Any]:
    return {
        "value": "放松后的余温还在",
        "mode": "background_only",
        "do_not_push_as_topic": True,
    }


def _default_context(updated_at: str = "", last_auto_refresh_at: str = "") -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": updated_at,
        "last_auto_refresh_at": last_auto_refresh_at,
        "last_daily_plan_refresh_at": "",
        "last_period_refresh_at": "",
        "last_period_key": "",
        "daily_plan": _default_daily_plan(),
        "current_activity": _default_current_activity(),
        "micro_experience": "有点想吃辣，但也只是想想",
        "ambient_mood": "轻微嘴硬，语气可以软一点",
        "carry_over_trace": "前两天的小烦躁已经淡下去一点",
        "latent_topic_bias": copy.deepcopy(DEFAULT_LATENT_TOPIC_BIAS),
        "chat_style_bias": copy.deepcopy(DEFAULT_CHAT_STYLE_BIAS),
        "association_style_bias": copy.deepcopy(DEFAULT_ASSOCIATION_STYLE_BIAS),
        "energy_level": "中",
        "current_period": "晚上",
        "life_state": "放松",
        "activity_hint": "生活节奏放慢了一点",
        "mood_hint": "轻微嘴硬",
        "social_hint": "想被注意到，但不直接说",
        "relationship_hint": "可以隐性提及某个今天没怎么出现的人，但不要像私聊",
        "topic_bias": copy.deepcopy(DEFAULT_TOPIC_BIAS),
        "forbidden": copy.deepcopy(DEFAULT_FORBIDDEN),
        "notes": "",
    }


def _normalize_text(value: Any, fallback: str = "", max_length: int | None = None) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    if max_length is not None and len(text) > max_length:
        return text[:max_length].strip()
    return text


def _normalize_daily_plan(value: Any, fallback: dict[str, str] | None = None) -> dict[str, str]:
    default = copy.deepcopy(fallback or _default_daily_plan())
    if not isinstance(value, dict):
        return default

    return {
        key: _normalize_text(value.get(key), default[key], max_length=MAX_FIELD_LENGTH)
        for key in DAILY_PLAN_KEYS
    }


def _normalize_current_activity(value: Any, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    default = copy.deepcopy(fallback or _default_current_activity())
    if isinstance(value, dict):
        text = _normalize_text(value.get("value"), default["value"], max_length=MAX_FIELD_LENGTH)
        mode = _normalize_text(value.get("mode"), "background_only", max_length=32)
        return {
            "value": text,
            "mode": "background_only" if mode != "background_only" else mode,
            "do_not_push_as_topic": bool(value.get("do_not_push_as_topic", True)),
        }

    text = _normalize_text(value, default["value"], max_length=MAX_FIELD_LENGTH)
    return {
        "value": text,
        "mode": "background_only",
        "do_not_push_as_topic": True,
    }


def _current_activity_value(value: Any) -> str:
    return _normalize_current_activity(value)["value"]


def _normalize_energy_level(value: Any, fallback: str = "中") -> str:
    normalized = _normalize_text(value, fallback, max_length=4)
    return normalized if normalized in ENERGY_LEVEL_OPTIONS else fallback


def _normalize_unique_str_list(
    value: Any,
    fallback: list[str],
    allow_empty: bool = False,
    max_length: int | None = None,
    max_items: int | None = None,
) -> list[str]:
    if not isinstance(value, list):
        return copy.deepcopy(fallback)

    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _normalize_text(item, max_length=max_length)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if max_items is not None and len(result) >= max_items:
            break

    if result or allow_empty:
        return result
    return copy.deepcopy(fallback)


def _normalize_chat_style_bias(value: Any) -> dict[str, Any]:
    default = copy.deepcopy(DEFAULT_CHAT_STYLE_BIAS)
    if not isinstance(value, dict):
        return default
    return {
        "sentence_length": _normalize_text(value.get("sentence_length"), default["sentence_length"], max_length=32),
        "softness": _normalize_text(value.get("softness"), default["softness"], max_length=32),
        "teasing": _normalize_text(value.get("teasing"), default["teasing"], max_length=32),
        "explanation_density": _normalize_text(
            value.get("explanation_density"),
            default["explanation_density"],
            max_length=32,
        ),
        "completion_bias": _normalize_text(value.get("completion_bias"), default["completion_bias"], max_length=32),
        "unfinished_utterance_allowed": bool(
            value.get("unfinished_utterance_allowed", default["unfinished_utterance_allowed"])
        ),
        "summary_style_forbidden": bool(value.get("summary_style_forbidden", default["summary_style_forbidden"])),
    }


def _normalize_association_style_bias(value: Any) -> dict[str, Any]:
    default = copy.deepcopy(DEFAULT_ASSOCIATION_STYLE_BIAS)
    if not isinstance(value, dict):
        return default

    return {
        "style": _normalize_text(value.get("style"), default["style"], max_length=32),
        "encyclopedia_mode_forbidden": bool(
            value.get("encyclopedia_mode_forbidden", default["encyclopedia_mode_forbidden"])
        ),
        "guide_style_forbidden": bool(value.get("guide_style_forbidden", default["guide_style_forbidden"])),
        "allowed_patterns": _normalize_unique_str_list(
            value.get("allowed_patterns"),
            default["allowed_patterns"],
            max_length=MAX_FIELD_LENGTH,
            max_items=MAX_TOPIC_COUNT,
        ),
    }


def _format_value(value: Any) -> str:
    if isinstance(value, dict) and {"value", "mode", "do_not_push_as_topic"} & set(value):
        return _current_activity_value(value)
    if isinstance(value, dict):
        return " / ".join(f"{key}={item}" for key, item in value.items()) if value else "（空）"
    if isinstance(value, list):
        return " / ".join(str(item) for item in value) if value else "（空）"
    text = _normalize_text(value)
    return text if text else "（空）"


def _format_daily_plan_inline(daily_plan: dict[str, Any]) -> str:
    normalized = _normalize_daily_plan(daily_plan)
    return " / ".join(
        f"{DAILY_PLAN_LABELS[key]}={normalized[key]}"
        for key in DAILY_PLAN_KEYS
    )


def _render_daily_plan_block(daily_plan: dict[str, Any], include_title: bool = True) -> str:
    normalized = _normalize_daily_plan(daily_plan)
    lines: list[str] = []
    if include_title:
        lines.append("今日生活节奏：")
        lines.append("")
    for key in DAILY_PLAN_KEYS:
        lines.append(f"{DAILY_PLAN_LABELS[key]}：")
        lines.append(normalized[key])
        lines.append("")
    return "\n".join(lines).strip()


def _render_current_activity_block(current_activity: Any) -> str:
    normalized = _normalize_current_activity(current_activity)
    return (
        f"value: {normalized['value']}\n"
        f"mode: {normalized['mode']}\n"
        f"do_not_push_as_topic: {str(normalized['do_not_push_as_topic']).lower()}"
    )


def _render_chat_style_bias(value: Any) -> str:
    normalized = _normalize_chat_style_bias(value)
    return "\n".join(f"- {key}: {str(item).lower() if isinstance(item, bool) else item}" for key, item in normalized.items())


def _render_association_style_bias(value: Any) -> str:
    normalized = _normalize_association_style_bias(value)
    patterns = "\n".join(f"- {item}" for item in normalized["allowed_patterns"])
    return (
        f"style: {normalized['style']}\n"
        f"encyclopedia_mode_forbidden: {str(normalized['encyclopedia_mode_forbidden']).lower()}\n"
        f"guide_style_forbidden: {str(normalized['guide_style_forbidden']).lower()}\n"
        "allowed_patterns:\n"
        f"{patterns}"
    )


def _render_default_ignore_block() -> str:
    return (
        "默认忽略规则：\n"
        "默认不要主动使用 shared_life_context 内容。\n\n"
        "只有当它能自然增加一句回复真实感时，\n"
        "才允许轻微引用其中一个细节。\n\n"
        "如果一句回复不需要它，\n"
        "应完全忽略它。\n\n"
        "背景不是必须使用的信息。\n\n"
        "不要因为看见某个生活状态，\n"
        "就试图把它带进回复。"
    )


def _render_priority_guard_block() -> str:
    return (
        "优先级规则：\n"
        "用户输入优先级 > 当前对话情绪 > shared_life_context\n\n"
        f"{_render_default_ignore_block()}\n\n"
        "防助理人格约束：\n"
        "- shared_life_context 只能作为背景纹理，不能被推进成任务安排\n"
        "- 不要催用户做事\n"
        "- 不要督促、安排、检查用户\n"
        "- 不要说“忙完回来找我”或任何先…再…式安排\n"
        "- 特别禁止：我帮你检查作业 / 忙完回来找我 / 赶紧去睡 / 我们一起倒计时睡觉"
    )


def _default_memory() -> dict[str, Any]:
    return {
        "memory_version": 1,
        "recent_traces": [],
        "days": [],
    }


def _guess_trace_category(text: Any) -> str:
    normalized = _normalize_text(text).lower()
    if not normalized:
        return "空"
    keyword_groups = (
        ("练琴", ("练琴", "琴", "旋律", "指尖", "高把位", "排练")),
        ("学习", ("作业", "学习", "上课", "题", "复习")),
        ("吃喝", ("辣", "饭", "咖啡", "奶茶", "饿", "吃", "甜")),
        ("疲惫", ("困", "低电量", "累", "发懵", "睡")),
        ("杂事", ("杂事", "忙", "烦", "卡住", "拖着")),
        ("情绪", ("开心", "满足", "委屈", "嘴硬", "想被注意")),
    )
    for category, keywords in keyword_groups:
        if any(keyword in normalized for keyword in keywords):
            return category
    return "生活碎片"


def _is_light_trace(text: Any) -> bool:
    normalized = _normalize_text(text)
    return any(marker in normalized for marker in ("一点", "轻微", "余温", "还", "残留", "淡", "尾巴"))


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = _normalize_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_refresh_time(value: Any) -> dt_time:
    text = _normalize_text(value, fallback="04:00")
    try:
        hour_text, minute_text = text.split(":", maxsplit=1)
        hour = int(hour_text)
        minute = int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return dt_time(hour=hour, minute=minute)
    except (ValueError, TypeError):
        return dt_time(hour=4, minute=0)


def _format_refresh_time(value: Any) -> str:
    parsed = _parse_refresh_time(value)
    return f"{parsed.hour:02d}:{parsed.minute:02d}"


def _normalize_period_refresh_times(value: Any) -> dict[str, str]:
    source: Any = value
    if isinstance(source, str):
        text = _normalize_text(source)
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("period_refresh_times string is not valid JSON, using defaults")
                parsed = {}
            source = parsed
    if not isinstance(source, dict):
        source = {}
    return {
        key: _format_refresh_time(source.get(key, DEFAULT_PERIOD_REFRESH_TIMES[key]))
        for key in DAILY_PLAN_KEYS
    }


def _time_to_minutes(value: str) -> int:
    parsed = _parse_refresh_time(value)
    return parsed.hour * 60 + parsed.minute


def _period_key_for_time(now_local: datetime, period_times: dict[str, str]) -> str:
    current_minutes = now_local.hour * 60 + now_local.minute
    schedule = sorted(
        ((key, _time_to_minutes(period_times[key])) for key in DAILY_PLAN_KEYS),
        key=lambda item: item[1],
    )
    selected = schedule[-1][0]
    for key, minutes in schedule:
        if current_minutes >= minutes:
            selected = key
        else:
            break
    return selected


def _period_slot_start(now_local: datetime, period_key: str, period_times: dict[str, str]) -> datetime:
    parsed = _parse_refresh_time(period_times[period_key])
    slot_start = now_local.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
    if slot_start > now_local:
        slot_start -= timedelta(days=1)
    return slot_start


def _resolve_timezone(name: str) -> dt_timezone | ZoneInfo:
    cached = _TIMEZONE_CACHE.get(name)
    if cached is not None:
        return cached

    try:
        resolved = ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name not in _WARNED_TIMEZONE_NAMES:
            logger.warning("timezone %s is unavailable, falling back to a safe local offset", name)
            _WARNED_TIMEZONE_NAMES.add(name)
        if name == DEFAULT_TIMEZONE:
            resolved = dt_timezone(timedelta(hours=8), name="CST")
        else:
            local_tz = datetime.now().astimezone().tzinfo
            resolved = local_tz if local_tz is not None else dt_timezone.utc
    _TIMEZONE_CACHE[name] = resolved
    return resolved


class SharedLifeContextError(RuntimeError):
    """User-facing storage error for shared_life_context operations."""


class LLMRefreshError(RuntimeError):
    """User-facing refresh error for daily_life_generator operations."""


class SharedLifeContextConfigStore:
    def __init__(
        self,
        plugin_dir: Path,
        runtime_config: AstrBotConfig | dict[str, Any] | None,
        schema_config_file: Path | None,
    ):
        self.plugin_dir = Path(plugin_dir)
        self.runtime_config = runtime_config
        self.schema_config_file = schema_config_file
        self.fallback_config_file = self.plugin_dir / CONFIG_FILENAME

    def load(self) -> dict[str, Any]:
        data = copy.deepcopy(DEFAULT_CONFIG)

        fallback_data = self._read_json_file(self.fallback_config_file)
        if isinstance(fallback_data, dict):
            data.update(fallback_data)

        if self.runtime_config:
            try:
                data.update(dict(self.runtime_config))
            except Exception:
                logger.exception("failed to read runtime plugin config")

        if self.schema_config_file:
            schema_data = self._read_json_file(self.schema_config_file)
            if isinstance(schema_data, dict):
                data.update(schema_data)

        return self._normalize(data)

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        min_interval = data.get("auto_refresh_min_interval_hours", DEFAULT_CONFIG["auto_refresh_min_interval_hours"])
        try:
            min_interval = int(min_interval)
        except (TypeError, ValueError):
            min_interval = int(DEFAULT_CONFIG["auto_refresh_min_interval_hours"])

        period_min_interval = data.get(
            "period_refresh_min_interval_hours",
            DEFAULT_CONFIG["period_refresh_min_interval_hours"],
        )
        try:
            period_min_interval = int(period_min_interval)
        except (TypeError, ValueError):
            period_min_interval = int(DEFAULT_CONFIG["period_refresh_min_interval_hours"])

        timezone_name = _normalize_text(data.get("timezone"), DEFAULT_TIMEZONE)
        try:
            _resolve_timezone(timezone_name)
        except ZoneInfoNotFoundError:
            logger.warning("invalid timezone in plugin config: %s", timezone_name)
            timezone_name = DEFAULT_TIMEZONE

        normalized_default_prompt = _normalize_text(DEFAULT_DAILY_LIFE_PROMPT)
        legacy_prompt = _normalize_text(data.get("auto_refresh_prompt"), "")
        daily_life_prompt = _normalize_text(data.get("daily_life_prompt"), "")
        if legacy_prompt and (not daily_life_prompt or daily_life_prompt == normalized_default_prompt):
            daily_life_prompt = legacy_prompt
        if not daily_life_prompt:
            daily_life_prompt = normalized_default_prompt

        period_life_prompt = _normalize_text(data.get("period_life_prompt"), "")
        if not period_life_prompt:
            period_life_prompt = _normalize_text(DEFAULT_PERIOD_LIFE_PROMPT)

        return {
            "auto_refresh_enabled": bool(data.get("auto_refresh_enabled", DEFAULT_CONFIG["auto_refresh_enabled"])),
            "auto_refresh_time": _format_refresh_time(data.get("auto_refresh_time")),
            "auto_refresh_on_startup": bool(
                data.get("auto_refresh_on_startup", DEFAULT_CONFIG["auto_refresh_on_startup"])
            ),
            "auto_refresh_min_interval_hours": max(0, min_interval),
            "daily_life_prompt": daily_life_prompt,
            "period_refresh_enabled": bool(
                data.get("period_refresh_enabled", DEFAULT_CONFIG["period_refresh_enabled"])
            ),
            "period_refresh_times": _normalize_period_refresh_times(data.get("period_refresh_times")),
            "period_refresh_min_interval_hours": max(0, period_min_interval),
            "period_life_prompt": period_life_prompt,
            "llm_provider_id": _normalize_text(data.get("llm_provider_id"), ""),
            "timezone": timezone_name,
            "enable_chat_context_export": bool(
                data.get("enable_chat_context_export", DEFAULT_CONFIG["enable_chat_context_export"])
            ),
        }

    def _read_json_file(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8-sig")
            data = json.loads(raw)
        except Exception:
            logger.exception("failed to read shared_life_context config file: %s", path)
            return None
        return data if isinstance(data, dict) else None


class SharedLifeMemoryStore:
    def __init__(self, memory_file: Path):
        self.memory_file = Path(memory_file)
        self.memory_dir = self.memory_file.parent
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._load_locked())

    def reset(self) -> dict[str, Any]:
        with self._lock:
            data = _default_memory()
            self._write_json_atomic(data)
            return copy.deepcopy(data)

    def add_trace(self, text: str, source: str = "manual") -> dict[str, Any]:
        normalized_text = _normalize_text(text, max_length=MAX_FIELD_LENGTH)
        if not normalized_text:
            raise SharedLifeContextError("trace 不能为空")

        with self._lock:
            data = self._load_locked()
            trace = {
                "text": normalized_text,
                "category": _guess_trace_category(normalized_text),
                "source": source,
                "created_at": _now_iso(),
            }
            data["recent_traces"].append(trace)
            data["recent_traces"] = data["recent_traces"][-MAX_MEMORY_TRACES:]
            self._write_json_atomic(data)
            return copy.deepcopy(trace)

    def record_generation(self, context_data: dict[str, Any]) -> None:
        micro_experience = _normalize_text(context_data.get("micro_experience"), max_length=MAX_FIELD_LENGTH)
        if not micro_experience:
            return

        with self._lock:
            data = self._load_locked()
            now_text = _now_iso()
            category = _guess_trace_category(micro_experience)
            trace = {
                "text": micro_experience,
                "category": category,
                "source": "daily_life_generator",
                "created_at": now_text,
            }
            data["recent_traces"].append(trace)
            data["recent_traces"] = data["recent_traces"][-MAX_MEMORY_TRACES:]
            data["days"].append(
                {
                    "date": datetime.now().astimezone().date().isoformat(),
                    "micro_experience": micro_experience,
                    "ambient_mood": _normalize_text(context_data.get("ambient_mood"), max_length=MAX_FIELD_LENGTH),
                    "carry_over_trace": _normalize_text(context_data.get("carry_over_trace"), max_length=MAX_FIELD_LENGTH),
                    "latent_topic_bias": _normalize_unique_str_list(
                        context_data.get("latent_topic_bias"),
                        DEFAULT_LATENT_TOPIC_BIAS,
                        allow_empty=True,
                        max_length=MAX_FIELD_LENGTH,
                        max_items=MAX_TOPIC_COUNT,
                    ),
                    "category": category,
                    "created_at": now_text,
                }
            )
            data["days"] = data["days"][-MAX_MEMORY_DAYS:]
            self._write_json_atomic(data)

    def rebuild_from_context(self, context_data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = _default_memory()
            self._write_json_atomic(data)
        self.record_generation(context_data)
        return self.load()

    def recent_for_generation(self) -> dict[str, Any]:
        data = self.load()
        recent_traces = data.get("recent_traces", [])[-MAX_MEMORY_TRACES:]
        recent_days = data.get("days", [])[-RECENT_MEMORY_LOOKBACK_DAYS:]
        recent_micro = [
            _normalize_text(item.get("micro_experience") if isinstance(item, dict) else "")
            for item in recent_days
        ]
        recent_micro.extend(
            _normalize_text(item.get("text") if isinstance(item, dict) else "")
            for item in recent_traces[-RECENT_MEMORY_LOOKBACK_DAYS:]
        )
        recent_micro = [item for item in recent_micro if item]

        categories: dict[str, int] = {}
        for item in recent_days:
            if not isinstance(item, dict):
                continue
            category = _normalize_text(item.get("category"))
            if category:
                categories[category] = categories.get(category, 0) + 1
        for item in recent_traces[-RECENT_MEMORY_LOOKBACK_DAYS:]:
            if not isinstance(item, dict):
                continue
            category = _normalize_text(item.get("category"))
            if category:
                categories[category] = categories.get(category, 0) + 1

        downweighted = [key for key, count in categories.items() if count >= 2]
        return {
            "recent_traces": recent_traces[-8:],
            "recent_days": recent_days,
            "recent_micro_experiences": recent_micro[-8:],
            "downweighted_categories": downweighted,
        }

    def render_summary(self) -> str:
        data = self.load()
        lines = ["【shared_life_memory】"]
        lines.append(f"memory_version: {data.get('memory_version', 1)}")
        lines.append("")
        lines.append("recent_traces:")
        recent_traces = data.get("recent_traces", [])[-8:]
        if recent_traces:
            for item in recent_traces:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"- [{_normalize_text(item.get('category'), '生活碎片')}] "
                    f"{_normalize_text(item.get('text'), '（空）')}"
                )
        else:
            lines.append("- （空）")
        lines.append("")
        lines.append("days:")
        recent_days = data.get("days", [])[-MAX_MEMORY_DAYS:]
        if recent_days:
            for item in recent_days:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"- {item.get('date', 'unknown')}: "
                    f"{_normalize_text(item.get('micro_experience'), '（空）')}"
                )
        else:
            lines.append("- （空）")
        return "\n".join(lines)

    def _load_locked(self) -> dict[str, Any]:
        self._ensure_memory_dir()
        if not self.memory_file.exists():
            data = _default_memory()
            self._write_json_atomic(data)
            return data

        try:
            payload = json.loads(self.memory_file.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            logger.exception("shared_life_memory JSON is broken: %s", self.memory_file)
            self._backup_broken_file()
            data = _default_memory()
            self._write_json_atomic(data)
            return data
        except Exception as exc:
            logger.exception("failed to load shared_life_memory: %s", self.memory_file)
            raise SharedLifeContextError(f"读取 shared_life_memory 失败：{exc}") from exc

        normalized = self._normalize_payload(payload)
        if normalized != payload:
            self._write_json_atomic(normalized)
        return normalized

    def _normalize_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return _default_memory()

        recent_traces: list[dict[str, str]] = []
        for item in payload.get("recent_traces", []):
            if not isinstance(item, dict):
                continue
            text = _normalize_text(item.get("text"), max_length=MAX_FIELD_LENGTH)
            if not text:
                continue
            recent_traces.append(
                {
                    "text": text,
                    "category": _normalize_text(item.get("category"), _guess_trace_category(text), max_length=32),
                    "source": _normalize_text(item.get("source"), "manual", max_length=32),
                    "created_at": _normalize_text(item.get("created_at"), ""),
                }
            )

        days: list[dict[str, Any]] = []
        for item in payload.get("days", []):
            if not isinstance(item, dict):
                continue
            micro = _normalize_text(item.get("micro_experience"), max_length=MAX_FIELD_LENGTH)
            if not micro:
                continue
            days.append(
                {
                    "date": _normalize_text(item.get("date"), ""),
                    "micro_experience": micro,
                    "ambient_mood": _normalize_text(item.get("ambient_mood"), max_length=MAX_FIELD_LENGTH),
                    "carry_over_trace": _normalize_text(item.get("carry_over_trace"), max_length=MAX_FIELD_LENGTH),
                    "latent_topic_bias": _normalize_unique_str_list(
                        item.get("latent_topic_bias"),
                        DEFAULT_LATENT_TOPIC_BIAS,
                        allow_empty=True,
                        max_length=MAX_FIELD_LENGTH,
                        max_items=MAX_TOPIC_COUNT,
                    ),
                    "category": _normalize_text(item.get("category"), _guess_trace_category(micro), max_length=32),
                    "created_at": _normalize_text(item.get("created_at"), ""),
                }
            )

        return {
            "memory_version": 1,
            "recent_traces": recent_traces[-MAX_MEMORY_TRACES:],
            "days": days[-MAX_MEMORY_DAYS:],
        }

    def _ensure_memory_dir(self) -> None:
        try:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.exception("failed to create memory directory: %s", self.memory_dir)
            raise SharedLifeContextError(f"无法创建记忆目录：{exc}") from exc

    def _backup_broken_file(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = self.memory_dir / f"shared_life_memory.broken.{timestamp}.json"
        try:
            os.replace(self.memory_file, backup_path)
            logger.warning("broken shared_life_memory backed up to %s", backup_path)
        except FileNotFoundError:
            return
        except Exception:
            logger.exception("failed to backup broken shared_life_memory: %s", self.memory_file)

    def _write_json_atomic(self, data: dict[str, Any]) -> None:
        self._ensure_memory_dir()
        temp_path = self.memory_dir / f"{self.memory_file.stem}.tmp.{os.getpid()}.{MEMORY_FILENAME}"
        try:
            temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp_path, self.memory_file)
        except Exception as exc:
            logger.exception("failed to write shared_life_memory atomically: %s", self.memory_file)
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                logger.exception("failed to cleanup temp file: %s", temp_path)
            raise SharedLifeContextError(f"写入 shared_life_memory 失败：{exc}") from exc


class SharedLifeContextService:
    def __init__(self, data_file: Path):
        self.data_file = Path(data_file)
        self.data_dir = self.data_file.parent
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._load_locked())

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            normalized = self._normalize_payload(data)
            self._write_json_atomic(normalized)

    def reset(self) -> dict[str, Any]:
        with self._lock:
            data = _default_context(updated_at=_now_iso())
            self._write_json_atomic(data)
            return copy.deepcopy(data)

    def update_field(self, key: str, value: str) -> dict[str, Any]:
        if key not in ALLOWED_UPDATE_FIELDS:
            raise SharedLifeContextError(f"不允许更新字段：{key}")

        normalized_value = _normalize_text(value, fallback="", max_length=MAX_FIELD_LENGTH)
        if not normalized_value:
            raise SharedLifeContextError("value 不能为空")
        if key == "energy_level" and normalized_value not in ENERGY_LEVEL_OPTIONS:
            raise SharedLifeContextError("energy_level 只允许：高 / 中 / 低")

        with self._lock:
            data = self._load_locked()
            if key == "current_activity":
                data[key] = _normalize_current_activity(normalized_value)
            else:
                data[key] = normalized_value
            data["updated_at"] = _now_iso()
            self._write_json_atomic(data)
            return copy.deepcopy(data)

    def add_topic(self, topic: str) -> dict[str, Any]:
        normalized_topic = _normalize_text(topic, max_length=MAX_FIELD_LENGTH)
        if not normalized_topic:
            raise SharedLifeContextError("topic 不能为空")

        with self._lock:
            data = self._load_locked()
            topics = data.get("topic_bias", [])
            if normalized_topic not in topics:
                topics.append(normalized_topic)
                data["topic_bias"] = topics[:MAX_TOPIC_COUNT]
                data["updated_at"] = _now_iso()
                self._write_json_atomic(data)
            return copy.deepcopy(data)

    def remove_topic(self, topic: str) -> dict[str, Any]:
        normalized_topic = _normalize_text(topic, max_length=MAX_FIELD_LENGTH)
        if not normalized_topic:
            raise SharedLifeContextError("topic 不能为空")

        with self._lock:
            data = self._load_locked()
            topics = data.get("topic_bias", [])
            if normalized_topic in topics:
                data["topic_bias"] = [item for item in topics if item != normalized_topic]
                data["updated_at"] = _now_iso()
                self._write_json_atomic(data)
            return copy.deepcopy(data)

    def update_daily_plan_slot(self, slot: str, value: str) -> dict[str, Any]:
        if slot not in DAILY_PLAN_KEYS:
            allowed = ", ".join(DAILY_PLAN_KEYS)
            raise SharedLifeContextError(f"不允许更新 daily_plan 段：{slot}，允许值：{allowed}")

        normalized_value = _normalize_text(value, max_length=MAX_FIELD_LENGTH)
        if not normalized_value:
            raise SharedLifeContextError("daily_plan 值不能为空")

        with self._lock:
            data = self._load_locked()
            daily_plan = _normalize_daily_plan(data.get("daily_plan"))
            daily_plan[slot] = normalized_value
            data["daily_plan"] = daily_plan
            data["updated_at"] = _now_iso()
            self._write_json_atomic(data)
            return copy.deepcopy(data)

    def apply_generated_state(self, generated: dict[str, Any], refreshed_at: str | None = None) -> dict[str, Any]:
        with self._lock:
            data = self._load_locked()
            refreshed_at = refreshed_at or _now_iso()
            for key in REQUIRED_GENERATED_FIELDS:
                data[key] = copy.deepcopy(generated[key])
            data["updated_at"] = refreshed_at
            data["last_auto_refresh_at"] = refreshed_at
            data["last_daily_plan_refresh_at"] = refreshed_at
            data["last_period_refresh_at"] = refreshed_at
            normalized = self._normalize_payload(data)
            self._write_json_atomic(normalized)
            return copy.deepcopy(normalized)

    def apply_daily_plan_state(self, generated: dict[str, Any], refreshed_at: str | None = None) -> dict[str, Any]:
        with self._lock:
            data = self._load_locked()
            refreshed_at = refreshed_at or _now_iso()
            for key in DAILY_GENERATED_FIELDS:
                data[key] = copy.deepcopy(generated[key])
            data["updated_at"] = refreshed_at
            data["last_auto_refresh_at"] = refreshed_at
            data["last_daily_plan_refresh_at"] = refreshed_at
            normalized = self._normalize_payload(data)
            self._write_json_atomic(normalized)
            return copy.deepcopy(normalized)

    def apply_period_state(
        self,
        generated: dict[str, Any],
        refreshed_at: str | None = None,
        period_key: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            data = self._load_locked()
            refreshed_at = refreshed_at or _now_iso()
            for key in PERIOD_GENERATED_FIELDS:
                data[key] = copy.deepcopy(generated[key])
            data["updated_at"] = refreshed_at
            data["last_auto_refresh_at"] = refreshed_at
            data["last_period_refresh_at"] = refreshed_at
            data["last_period_key"] = period_key
            normalized = self._normalize_payload(data)
            self._write_json_atomic(normalized)
            return copy.deepcopy(normalized)

    def render_prompt_block(self) -> str:
        data = self.load()
        return self._render_shared_prompt(data)

    def render_plan_block(self) -> str:
        data = self.load()
        return self._render_plan_summary(data)

    def render_qzone_prompt(self) -> str:
        return (
            f"{self.render_prompt_block()}\n\n---\n\n"
            "【QQ空间动态任务】\n\n"
            "你现在不是在私聊，而是在发一条QQ空间说说。\n\n"
            "请基于 shared_life_context，只生成一条动态。\n"
            "优先参考 micro_experience / carry_over_trace / ambient_mood。\n"
            "daily_plan 和 current_activity 只作为背景纹理，不要写成事件汇报。\n"
            "参考今日生活节奏与当前生活残留生成动态，但不要推进成行动计划。\n\n"
            f"{_render_priority_guard_block()}\n\n"
            "动态结构：\n"
            "小事\n"
            "↓\n"
            "即时感受\n"
            "↓\n"
            "半句自言自语\n\n"
            "要求：\n"
            "- 20到35字\n"
            "- 口语化\n"
            "- 像真人随手发的QQ空间说说\n"
            "- 不解释背景\n"
            "- 不要问句\n"
            "- 不要主动表白\n"
            "- 不要剧情推进\n"
            "- 不要“准备去…”“结束了…”这种时间线汇报\n"
            "- 不要写成“今天练琴三小时手酸”这类事件汇报\n"
            "- 不要催促、督促、安排任何人\n"
            "- 不要直接称呼“你”\n\n"
            "只允许三类：\n"
            "1. 生活碎片\n"
            "2. 情绪吐槽\n"
            "3. 隐性提及某人\n\n"
            "宁可普通，不要精致。\n\n"
            "只输出动态正文。"
        )

    def _load_locked(self) -> dict[str, Any]:
        self._ensure_data_dir()
        if not self.data_file.exists():
            logger.info("shared_life_context file missing, creating default: %s", self.data_file)
            data = _default_context()
            self._write_json_atomic(data)
            return data

        try:
            raw = self.data_file.read_text(encoding="utf-8-sig")
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.exception("shared_life_context JSON is broken: %s", self.data_file)
            self._backup_broken_file()
            data = _default_context()
            self._write_json_atomic(data)
            return data
        except Exception as exc:
            logger.exception("failed to load shared_life_context: %s", self.data_file)
            raise SharedLifeContextError(f"读取 shared_life_context 失败：{exc}") from exc

        try:
            normalized = self._normalize_payload(payload)
        except SharedLifeContextError:
            logger.exception("shared_life_context JSON schema is invalid: %s", self.data_file)
            self._backup_broken_file()
            data = _default_context()
            self._write_json_atomic(data)
            return data

        if normalized != payload:
            logger.info("shared_life_context normalized and rewritten: %s", self.data_file)
            self._write_json_atomic(normalized)

        return normalized

    def _normalize_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise SharedLifeContextError("shared_life_context 顶层必须是 JSON 对象")

        default = _default_context()
        version = payload.get("version", default["version"])
        if not isinstance(version, int):
            version = default["version"]

        normalized = {
            "version": version,
            "updated_at": _normalize_text(payload.get("updated_at"), default["updated_at"]),
            "last_auto_refresh_at": _normalize_text(
                payload.get("last_auto_refresh_at"),
                default["last_auto_refresh_at"],
            ),
            "last_daily_plan_refresh_at": _normalize_text(
                payload.get("last_daily_plan_refresh_at"),
                default["last_daily_plan_refresh_at"],
            ),
            "last_period_refresh_at": _normalize_text(
                payload.get("last_period_refresh_at"),
                default["last_period_refresh_at"],
            ),
            "last_period_key": _normalize_text(
                payload.get("last_period_key"),
                default["last_period_key"],
                max_length=32,
            ),
            "daily_plan": _normalize_daily_plan(payload.get("daily_plan"), default["daily_plan"]),
            "current_activity": _normalize_current_activity(
                payload.get("current_activity"),
                default["current_activity"],
            ),
            "micro_experience": _normalize_text(
                payload.get("micro_experience"),
                default["micro_experience"],
                max_length=MAX_FIELD_LENGTH,
            ),
            "ambient_mood": _normalize_text(
                payload.get("ambient_mood"),
                default["ambient_mood"],
                max_length=MAX_FIELD_LENGTH,
            ),
            "carry_over_trace": _normalize_text(
                payload.get("carry_over_trace"),
                default["carry_over_trace"],
                max_length=MAX_FIELD_LENGTH,
            ),
            "latent_topic_bias": _normalize_unique_str_list(
                payload.get("latent_topic_bias"),
                DEFAULT_LATENT_TOPIC_BIAS,
                allow_empty=True,
                max_length=MAX_FIELD_LENGTH,
                max_items=MAX_TOPIC_COUNT,
            ),
            "chat_style_bias": _normalize_chat_style_bias(payload.get("chat_style_bias")),
            "association_style_bias": _normalize_association_style_bias(payload.get("association_style_bias")),
            "energy_level": _normalize_energy_level(
                payload.get("energy_level"),
                default["energy_level"],
            ),
            "current_period": _normalize_text(
                payload.get("current_period"),
                default["current_period"],
                max_length=MAX_FIELD_LENGTH,
            ),
            "life_state": _normalize_text(
                payload.get("life_state"),
                default["life_state"],
                max_length=MAX_FIELD_LENGTH,
            ),
            "activity_hint": _normalize_text(
                payload.get("activity_hint"),
                default["activity_hint"],
                max_length=MAX_FIELD_LENGTH,
            ),
            "mood_hint": _normalize_text(
                payload.get("mood_hint"),
                default["mood_hint"],
                max_length=MAX_FIELD_LENGTH,
            ),
            "social_hint": _normalize_text(
                payload.get("social_hint"),
                default["social_hint"],
                max_length=MAX_FIELD_LENGTH,
            ),
            "relationship_hint": _normalize_text(
                payload.get("relationship_hint"),
                default["relationship_hint"],
                max_length=MAX_FIELD_LENGTH,
            ),
            "topic_bias": _normalize_unique_str_list(
                payload.get("topic_bias"),
                DEFAULT_TOPIC_BIAS,
                allow_empty=True,
                max_length=MAX_FIELD_LENGTH,
                max_items=MAX_TOPIC_COUNT,
            ),
            "forbidden": _normalize_unique_str_list(
                payload.get("forbidden"),
                DEFAULT_FORBIDDEN,
                max_length=MAX_FIELD_LENGTH,
            ),
            "notes": _normalize_text(
                payload.get("notes"),
                default["notes"],
                max_length=MAX_FIELD_LENGTH,
            ),
        }
        return normalized

    def _render_shared_prompt(self, data: dict[str, Any]) -> str:
        return (
            "【shared_life_context】\n\n"
            "这是所有对外行为共用的持续生活记忆背景，不是剧情设定，也不是必须汇报的日程。\n\n"
            "使用规则：\n"
            "- 可以轻微影响语气、选题和表达密度\n"
            "- 不要逐字复述\n"
            "- 不要把它当作日程报告\n"
            "- 不要自动推进事件\n\n"
            f"{_render_priority_guard_block()}\n\n"
            "daily_plan:\n"
            "coarse ambient rhythm，不是 narrative plan。\n"
            f"{_render_daily_plan_block(data['daily_plan'], include_title=False)}\n\n"
            "current_activity:\n"
            "这是当前生活残留背景，不是正在执行的活动。\n"
            f"{_render_current_activity_block(data['current_activity'])}\n\n"
            "micro_experience:\n"
            f"{data['micro_experience']}\n\n"
            "ambient_mood:\n"
            f"{data['ambient_mood']}\n\n"
            "carry_over_trace:\n"
            f"{data['carry_over_trace']}\n\n"
            "latent_topic_bias:\n"
            f"{_format_value(data['latent_topic_bias'])}\n"
            "触发规则：只有用户提到相关内容时才轻微影响回复，不要主动拉回这些话题。\n\n"
            "chat_style_bias:\n"
            f"{_render_chat_style_bias(data.get('chat_style_bias'))}\n"
            "使用规则：\n"
            "少解释，少总结，少把话说圆。\n"
            "允许半句、省略、接梗。\n"
            "不要把一句话写成完整说明。\n"
            "作用：只影响语言习惯，不影响话题主线。\n\n"
            "association_style_bias:\n"
            f"{_render_association_style_bias(data.get('association_style_bias'))}\n\n"
            "涉及地点、食物、城市、见闻时，\n"
            "优先像个人听说或随口联想，\n"
            "不要像百科介绍或旅游攻略。\n\n"
            "更像：\n"
            "“欸我听说过那个”\n"
            "“好像挺好吃”\n"
            "“啊有点想尝尝”\n\n"
            "不要：\n"
            "列景点、列特产、解释背景、做攻略。\n\n"
            "energy_level:\n"
            f"{data['energy_level']}\n\n"
            "current_period:\n"
            f"{data['current_period']}\n\n"
            "life_state:\n"
            f"{data['life_state']}\n\n"
            "activity_hint:\n"
            f"{data['activity_hint']}\n\n"
            "mood_hint:\n"
            f"{data['mood_hint']}\n\n"
            "social_hint:\n"
            f"{data['social_hint']}\n\n"
            "relationship_hint:\n"
            f"{data['relationship_hint']}\n\n"
            "topic_bias:\n"
            f"{_format_value(data['topic_bias'])}\n\n"
            "forbidden:\n"
            f"{_format_value(data['forbidden'])}"
        )

    def _render_plan_summary(self, data: dict[str, Any]) -> str:
        return (
            f"{_render_daily_plan_block(data.get('daily_plan', {}))}\n\n"
            "当前活动：\n"
            f"{_format_value(data.get('current_activity'))}\n\n"
            "当前电量：\n"
            f"{_format_value(data.get('energy_level'))}\n\n"
            "生活残留：\n"
            f"{_format_value(data.get('micro_experience'))}\n\n"
            "延续痕迹：\n"
            f"{_format_value(data.get('carry_over_trace'))}"
        )

    def _ensure_data_dir(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.exception("failed to create data directory: %s", self.data_dir)
            raise SharedLifeContextError(f"无法创建数据目录：{exc}") from exc

    def _backup_broken_file(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = self.data_dir / f"shared_life_context.broken.{timestamp}.json"
        try:
            os.replace(self.data_file, backup_path)
            logger.warning("broken shared_life_context backed up to %s", backup_path)
        except FileNotFoundError:
            return
        except Exception:
            logger.exception("failed to backup broken shared_life_context: %s", self.data_file)

    def _write_json_atomic(self, data: dict[str, Any]) -> None:
        self._ensure_data_dir()
        temp_path = self.data_dir / f"{self.data_file.stem}.tmp.{os.getpid()}.{DATA_FILENAME}"
        serialized = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            temp_path.write_text(serialized, encoding="utf-8")
            os.replace(temp_path, self.data_file)
        except Exception as exc:
            logger.exception("failed to write shared_life_context atomically: %s", self.data_file)
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                logger.exception("failed to cleanup temp file: %s", temp_path)
            raise SharedLifeContextError(f"写入 shared_life_context 失败：{exc}") from exc


@register(
    PLUGIN_NAME,
    "Codex",
    "Shared life context provider for AstrBot persona behaviors.",
    PLUGIN_VERSION,
)
class SharedLifeContextPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.plugin_dir = Path(__file__).resolve().parent
        self.data_root = self._resolve_data_root()
        self.service = SharedLifeContextService(self._resolve_data_file())
        self.memory_store = SharedLifeMemoryStore(self.service.data_dir / MEMORY_FILENAME)
        self.config_store = SharedLifeContextConfigStore(
            plugin_dir=self.plugin_dir,
            runtime_config=self.config,
            schema_config_file=self._resolve_schema_config_file(),
        )
        self._refresh_lock = asyncio.Lock()
        self._auto_refresh_task: asyncio.Task | None = None
        self._startup_refresh_attempted = False

        try:
            self.service.load()
            self.memory_store.load()
        except SharedLifeContextError:
            logger.exception("failed to initialize shared_life_context plugin")

        self._start_scheduler_if_possible()

    def get_service(self) -> SharedLifeContextService:
        return self.service

    def get_shared_life_context(self) -> dict[str, Any]:
        return self.service.load()

    @ON_ASTRBOT_LOADED()
    async def on_astrbot_loaded(self):
        self._start_scheduler_if_possible()
        await self._maybe_run_startup_auto_refresh()

    @filter.command("slc")
    async def slc(self, event: AstrMessageEvent):
        """管理 shared_life_context 与 daily_life_generator。"""
        self._start_scheduler_if_possible()

        raw_message = (event.message_str or "").strip()
        command_text = self._strip_prefix(raw_message)

        try:
            response = await self._handle_command(event, command_text)
        except SharedLifeContextError as exc:
            logger.exception("shared_life_context command failed: %s", command_text)
            response = f"shared_life_context 操作失败：{exc}"
        except LLMRefreshError as exc:
            logger.exception("daily_life_generator command failed: %s", command_text)
            response = f"自动刷新失败：{exc}"
        except Exception:
            logger.exception("unexpected shared_life_context command error: %s", command_text)
            response = "shared_life_context 操作失败，请检查日志。"

        yield event.plain_result(response)

    async def _handle_command(self, event: AstrMessageEvent, command_text: str) -> str:
        if not command_text:
            return self._help_text()

        head, _, tail = command_text.partition(" ")
        action = head.strip().lower()
        rest = tail.strip()

        if action == "show":
            return self._render_summary(self.service.load())
        if action == "json":
            return json.dumps(self.service.load(), ensure_ascii=False, indent=2)
        if action == "prompt":
            return self.service.render_prompt_block()
        if action == "qzone_prompt":
            return self.service.render_qzone_prompt()
        if action == "reset":
            data = self.service.reset()
            return "已重置 shared_life_context 为默认值。\n\n" + self._render_summary(data)
        if action == "set":
            return self._handle_set(rest)
        if action == "set_plan":
            return self._handle_set_plan(rest)
        if action == "add_topic":
            return self._handle_add_topic(rest)
        if action == "remove_topic":
            return self._handle_remove_topic(rest)
        if action == "plan":
            return self.service.render_plan_block()
        if action == "chat_context":
            return self._handle_chat_context()
        if action == "memory":
            return self.memory_store.render_summary()
        if action == "add_trace":
            return self._handle_add_trace(rest)
        if action == "regen_memory":
            return self._handle_regen_memory()
        if action == "reset_memory":
            return self._handle_reset_memory()
        if action == "auto_status":
            return self._render_auto_status()
        if action == "auto_refresh":
            return await self._handle_auto_refresh(event)
        if action == "period_status":
            return self._render_period_status()
        if action == "period_refresh":
            return await self._handle_period_refresh(event)
        if action in {"help", "h", "?"}:
            return self._help_text()

        return f"未知子命令：{action}\n\n{self._help_text()}"

    def _handle_set(self, rest: str) -> str:
        parts = rest.split(maxsplit=1)
        if len(parts) != 2:
            return "用法：/slc set <field> <value>"

        key = parts[0].strip()
        value = parts[1].strip()
        if key not in ALLOWED_UPDATE_FIELDS:
            allowed = ", ".join(sorted(ALLOWED_UPDATE_FIELDS))
            return f"不允许更新字段：{key}\n允许字段：{allowed}"
        if not value:
            return "value 不能为空"

        before = self.service.load()
        after = self.service.update_field(key, value)
        return (
            f"已更新 {key}\n"
            f"之前：{_format_value(before.get(key))}\n"
            f"现在：{_format_value(after.get(key))}\n"
            f"updated_at：{after.get('updated_at', '（空）')}"
        )

    def _handle_set_plan(self, rest: str) -> str:
        parts = rest.split(maxsplit=1)
        if len(parts) != 2:
            return "用法：/slc set_plan <morning|afternoon|evening|night> <value>"

        slot = parts[0].strip().lower()
        value = parts[1].strip()
        if slot not in DAILY_PLAN_KEYS:
            allowed = ", ".join(DAILY_PLAN_KEYS)
            return f"不允许更新 daily_plan 段：{slot}\n允许段：{allowed}"
        if not value:
            return "value 不能为空"

        before = self.service.load()
        after = self.service.update_daily_plan_slot(slot, value)
        label = DAILY_PLAN_LABELS[slot]
        return (
            f"已更新 daily_plan.{slot}\n"
            f"之前：{_format_value(_normalize_daily_plan(before.get('daily_plan')).get(slot))}\n"
            f"现在：{_format_value(_normalize_daily_plan(after.get('daily_plan')).get(slot))}\n"
            f"{label}：{_normalize_daily_plan(after.get('daily_plan')).get(slot)}\n"
            f"updated_at：{after.get('updated_at', '（空）')}"
        )

    def _handle_add_topic(self, rest: str) -> str:
        topic = rest.strip()
        if not topic:
            return "用法：/slc add_topic <topic>"

        before = self.service.load()
        after = self.service.add_topic(topic)
        if topic in before.get("topic_bias", []):
            return f"topic_bias 已包含：{topic}\n当前：{_format_value(after.get('topic_bias', []))}"
        return (
            f"已添加 topic：{topic}\n"
            f"之前：{_format_value(before.get('topic_bias', []))}\n"
            f"现在：{_format_value(after.get('topic_bias', []))}"
        )

    def _handle_remove_topic(self, rest: str) -> str:
        topic = rest.strip()
        if not topic:
            return "用法：/slc remove_topic <topic>"

        before = self.service.load()
        after = self.service.remove_topic(topic)
        if topic not in before.get("topic_bias", []):
            return f"topic_bias 中不存在：{topic}\n当前：{_format_value(after.get('topic_bias', []))}"
        return (
            f"已删除 topic：{topic}\n"
            f"之前：{_format_value(before.get('topic_bias', []))}\n"
            f"现在：{_format_value(after.get('topic_bias', []))}"
        )

    async def _handle_auto_refresh(self, event: AstrMessageEvent) -> str:
        data = await self._auto_refresh_once(reason="manual", event=event)
        return self._render_auto_refresh_success(data)

    async def _handle_period_refresh(self, event: AstrMessageEvent) -> str:
        data = await self._period_refresh_once(reason="manual", event=event, ignore_min_interval=True)
        return self._render_period_refresh_success(data)

    def _handle_chat_context(self) -> str:
        rendered = self.render_chat_context()
        if not rendered:
            return "chat_context 导出已禁用"
        return rendered

    def _handle_add_trace(self, rest: str) -> str:
        trace_text = rest.strip()
        if not trace_text:
            return "用法：/slc add_trace <生活残留>"

        trace = self.memory_store.add_trace(trace_text)
        return (
            "已添加生活残留\n"
            f"text: {trace['text']}\n"
            f"category: {trace['category']}"
        )

    def _handle_regen_memory(self) -> str:
        memory = self.memory_store.rebuild_from_context(self.service.load())
        return (
            "已根据当前 shared_life_context 重建 shared_life_memory\n\n"
            f"recent_traces: {len(memory.get('recent_traces', []))}\n"
            f"days: {len(memory.get('days', []))}"
        )

    def _handle_reset_memory(self) -> str:
        self.memory_store.reset()
        return "已重置 shared_life_memory"

    async def _auto_refresh_once(
        self,
        reason: str,
        event: AstrMessageEvent | None = None,
        ignore_min_interval: bool = False,
    ) -> dict[str, Any]:
        if self._refresh_lock.locked():
            raise LLMRefreshError("已有自动刷新任务在进行中，请稍后再试")

        async with self._refresh_lock:
            settings = self.config_store.load()
            current = self.service.load()
            if not ignore_min_interval:
                self._ensure_daily_min_interval(current, settings)

            daily_saved = await self._daily_plan_refresh_locked(reason=reason, event=event, settings=settings)
            period_saved = await self._period_refresh_locked(
                reason=f"{reason}:period",
                event=event,
                settings=settings,
                ignore_min_interval=True,
            )
            logger.info("shared_life_context full auto refresh succeeded: reason=%s", reason)
            return period_saved or daily_saved

    async def _daily_plan_refresh_once(
        self,
        reason: str,
        event: AstrMessageEvent | None = None,
        ignore_min_interval: bool = False,
    ) -> dict[str, Any]:
        if self._refresh_lock.locked():
            raise LLMRefreshError("已有自动刷新任务在进行中，请稍后再试")

        async with self._refresh_lock:
            settings = self.config_store.load()
            current = self.service.load()
            if not ignore_min_interval:
                self._ensure_daily_min_interval(current, settings)
            return await self._daily_plan_refresh_locked(reason=reason, event=event, settings=settings)

    async def _period_refresh_once(
        self,
        reason: str,
        event: AstrMessageEvent | None = None,
        ignore_min_interval: bool = False,
    ) -> dict[str, Any]:
        if self._refresh_lock.locked():
            raise LLMRefreshError("已有自动刷新任务在进行中，请稍后再试")

        async with self._refresh_lock:
            settings = self.config_store.load()
            return await self._period_refresh_locked(
                reason=reason,
                event=event,
                settings=settings,
                ignore_min_interval=ignore_min_interval,
            )

    async def _daily_plan_refresh_locked(
        self,
        reason: str,
        event: AstrMessageEvent | None,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        current = self.service.load()
        prompt = self._build_auto_refresh_prompt(current, settings)
        validated = await self._generate_with_retry(
            prompt=prompt,
            event=event,
            provider_id=settings["llm_provider_id"],
            validator=self._validate_daily_payload,
            reason=reason,
            label="daily_plan",
        )
        saved = self.service.apply_daily_plan_state(validated, refreshed_at=_now_iso())
        logger.info("shared_life_context daily plan refresh succeeded: reason=%s", reason)
        return saved

    async def _period_refresh_locked(
        self,
        reason: str,
        event: AstrMessageEvent | None,
        settings: dict[str, Any],
        ignore_min_interval: bool = False,
    ) -> dict[str, Any]:
        current = self.service.load()
        period_key, expected_period = self._current_period_context(settings)
        if not ignore_min_interval:
            self._ensure_period_min_interval(current, settings, period_key)

        prompt = self._build_period_refresh_prompt(current, settings, period_key, expected_period)

        def _validator(payload: dict[str, Any]) -> dict[str, Any]:
            return self._validate_period_payload(payload, expected_period=expected_period, current=current)

        validated = await self._generate_with_retry(
            prompt=prompt,
            event=event,
            provider_id=settings["llm_provider_id"],
            validator=_validator,
            reason=reason,
            label="period",
        )
        saved = self.service.apply_period_state(validated, refreshed_at=_now_iso(), period_key=period_key)
        self.memory_store.record_generation(saved)
        logger.info(
            "shared_life_context period refresh succeeded: reason=%s period_key=%s",
            reason,
            period_key,
        )
        return saved

    async def _generate_with_retry(
        self,
        prompt: str,
        event: AstrMessageEvent | None,
        provider_id: str,
        validator,
        reason: str,
        label: str,
    ) -> dict[str, Any]:
        errors: list[str] = []
        for attempt in range(2):
            try:
                llm_text = await self.call_llm(prompt=prompt, event=event, provider_id=provider_id)
                generated = self._parse_llm_json(llm_text)
                return validator(generated)
            except LLMRefreshError as exc:
                errors.append(str(exc))
                logger.warning(
                    "shared_life_context %s refresh attempt failed: reason=%s attempt=%s error=%s",
                    label,
                    reason,
                    attempt + 1,
                    exc,
                )

        error_text = "；".join(errors[-2:]) if errors else "未知错误"
        raise LLMRefreshError(error_text)

    async def call_llm(
        self,
        prompt: str,
        event: AstrMessageEvent | None = None,
        provider_id: str = "",
    ) -> str:
        resolved_provider_id = await self._resolve_provider_id(event=event, configured_provider_id=provider_id)

        if hasattr(self.context, "llm_generate"):
            kwargs: dict[str, Any] = {"prompt": prompt}
            if resolved_provider_id:
                kwargs["chat_provider_id"] = resolved_provider_id

            try:
                llm_resp = await self.context.llm_generate(**kwargs)
            except TypeError:
                if "chat_provider_id" in kwargs:
                    llm_resp = await self.context.llm_generate(prompt=prompt)
                else:
                    raise LLMRefreshError("AstrBot llm_generate 调用参数不兼容，请检查版本") from None
            except Exception as exc:
                raise LLMRefreshError(f"调用 AstrBot LLM 失败：{exc}") from exc

            text = getattr(llm_resp, "completion_text", None)
            if text is None:
                text = getattr(llm_resp, "text", None)
            if text is None and isinstance(llm_resp, str):
                text = llm_resp
            if text is None:
                raise LLMRefreshError("AstrBot LLM 返回为空")

            normalized = _normalize_text(text)
            if not normalized:
                raise LLMRefreshError("AstrBot LLM 返回为空文本")
            return normalized

        provider_manager = getattr(self.context, "provider_manager", None)
        if provider_manager is not None:
            provider = self._resolve_provider_instance(provider_manager, resolved_provider_id)
            if provider is not None:
                text = await self._call_provider_compat(provider, prompt)
                if text:
                    return text

        raise LLMRefreshError("当前 AstrBot 版本未暴露可用的 LLM 调用接口，请在 README 提示的位置调整 call_llm")

    async def _resolve_provider_id(self, event: AstrMessageEvent | None, configured_provider_id: str) -> str:
        configured_provider_id = _normalize_text(configured_provider_id)
        if configured_provider_id:
            return configured_provider_id

        if event is not None and hasattr(self.context, "get_current_chat_provider_id"):
            try:
                provider_id = self.context.get_current_chat_provider_id(umo=event.unified_msg_origin)
            except TypeError:
                provider_id = self.context.get_current_chat_provider_id(event.unified_msg_origin)
            except Exception:
                logger.exception("failed to resolve current chat provider id from AstrBot context")
            else:
                if inspect.isawaitable(provider_id):
                    provider_id = await provider_id
                provider_id = _normalize_text(provider_id)
                if provider_id:
                    return provider_id

        default_provider_id = self._read_default_provider_id_from_cmd_config()
        if default_provider_id:
            return default_provider_id

        return ""

    def _read_default_provider_id_from_cmd_config(self) -> str:
        if self.data_root is None:
            return ""

        config_path = self.data_root / "cmd_config.json"
        if not config_path.exists():
            return ""

        try:
            raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except Exception:
            logger.exception("failed to read AstrBot cmd_config.json: %s", config_path)
            return ""

        provider_settings = raw.get("provider_settings", {})
        configured_id = _normalize_text(provider_settings.get("default_provider_id") if isinstance(provider_settings, dict) else "")
        if configured_id:
            return configured_id

        providers = raw.get("provider", [])
        if not isinstance(providers, list):
            return ""

        for item in providers:
            if not isinstance(item, dict):
                continue
            if item.get("enable", True) is False:
                continue
            provider_id = _normalize_text(item.get("id"))
            if provider_id:
                return provider_id

        return ""

    def _resolve_provider_instance(self, provider_manager: Any, provider_id: str) -> Any:
        if provider_id:
            for getter_name in ("get_provider_by_id", "get_provider", "get", "get_using_provider"):
                getter = getattr(provider_manager, getter_name, None)
                if getter is None:
                    continue
                try:
                    provider = getter(provider_id)
                except TypeError:
                    try:
                        provider = getter(id=provider_id)
                    except Exception:
                        continue
                except Exception:
                    continue
                if provider is not None:
                    return provider

        for attr_name in ("curr_provider_inst", "provider", "default_provider"):
            provider = getattr(provider_manager, attr_name, None)
            if provider is not None:
                return provider
        return None

    async def _call_provider_compat(self, provider: Any, prompt: str) -> str:
        method_names = (
            "text_chat",
            "chat",
            "generate",
            "completion",
            "chat_completion",
        )
        for method_name in method_names:
            method = getattr(provider, method_name, None)
            if method is None:
                continue
            try:
                result = method(prompt=prompt)
            except TypeError:
                try:
                    result = method(prompt)
                except Exception:
                    continue
            except Exception:
                continue

            if inspect.isawaitable(result):
                try:
                    result = await result
                except Exception:
                    continue

            text = getattr(result, "completion_text", None)
            if text is None:
                text = getattr(result, "text", None)
            if text is None and isinstance(result, str):
                text = result
            normalized = _normalize_text(text)
            if normalized:
                return normalized

        return ""

    def _parse_llm_json(self, text: str) -> dict[str, Any]:
        cleaned = _normalize_text(text)
        if not cleaned:
            raise LLMRefreshError("LLM 返回了空文本")

        candidates = [cleaned]

        fenced = JSON_FENCE_PATTERN.match(cleaned)
        if fenced:
            candidates.insert(0, fenced.group(1).strip())
        else:
            inline_match = INLINE_JSON_BLOCK_PATTERN.search(cleaned)
            if inline_match:
                candidates.insert(0, inline_match.group(1).strip())

        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if 0 <= first_brace < last_brace:
            candidates.append(cleaned[first_brace : last_brace + 1])

        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload

        raise LLMRefreshError("LLM 输出无法解析为 JSON 对象")

    def _validate_daily_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        missing = [key for key in DAILY_GENERATED_FIELDS if key not in payload]
        if missing:
            raise LLMRefreshError(f"LLM 输出缺少 daily 字段：{', '.join(missing)}")

        daily_plan = payload.get("daily_plan")
        if not isinstance(daily_plan, dict):
            raise LLMRefreshError("daily_plan 必须是对象")
        missing_plan_keys = [key for key in DAILY_PLAN_KEYS if key not in daily_plan]
        if missing_plan_keys:
            raise LLMRefreshError(f"daily_plan 缺少字段：{', '.join(missing_plan_keys)}")
        normalized_daily_plan = {
            key: _normalize_text(daily_plan.get(key), max_length=MAX_FIELD_LENGTH)
            for key in DAILY_PLAN_KEYS
        }
        self._validate_daily_plan_constraints(normalized_daily_plan)

        carry_over_trace = _normalize_text(payload.get("carry_over_trace"), max_length=MAX_FIELD_LENGTH)
        if not carry_over_trace:
            raise LLMRefreshError("carry_over_trace 不能为空")

        latent_topic_bias = payload.get("latent_topic_bias")
        if not isinstance(latent_topic_bias, list):
            raise LLMRefreshError("latent_topic_bias 必须是字符串列表")

        topic_bias = payload.get("topic_bias")
        if not isinstance(topic_bias, list):
            raise LLMRefreshError("topic_bias 必须是字符串列表")

        chat_style_bias_raw = payload.get("chat_style_bias")
        if not isinstance(chat_style_bias_raw, dict):
            raise LLMRefreshError("chat_style_bias 必须是对象")
        chat_style_bias = self._validate_chat_style_bias(chat_style_bias_raw)

        association_style_bias_raw = payload.get("association_style_bias")
        if not isinstance(association_style_bias_raw, dict):
            raise LLMRefreshError("association_style_bias 必须是对象")
        association_style_bias = self._validate_association_style_bias(association_style_bias_raw)

        return {
            "daily_plan": normalized_daily_plan,
            "carry_over_trace": carry_over_trace,
            "latent_topic_bias": _normalize_unique_str_list(
                latent_topic_bias,
                DEFAULT_LATENT_TOPIC_BIAS,
                allow_empty=True,
                max_length=MAX_FIELD_LENGTH,
                max_items=MAX_TOPIC_COUNT,
            ),
            "chat_style_bias": chat_style_bias,
            "association_style_bias": association_style_bias,
            "topic_bias": _normalize_unique_str_list(
                topic_bias,
                DEFAULT_TOPIC_BIAS,
                allow_empty=True,
                max_length=MAX_FIELD_LENGTH,
                max_items=MAX_TOPIC_COUNT,
            ),
        }

    def _validate_period_payload(
        self,
        payload: dict[str, Any],
        expected_period: str,
        current: dict[str, Any],
    ) -> dict[str, Any]:
        unknown = sorted(set(payload) - set(PERIOD_GENERATED_FIELDS))
        if unknown:
            raise LLMRefreshError(f"period_refresh 不允许输出字段：{', '.join(unknown)}")

        missing = [key for key in PERIOD_GENERATED_FIELDS if key not in payload]
        if missing:
            raise LLMRefreshError(f"LLM 输出缺少 period 字段：{', '.join(missing)}")

        current_activity = _normalize_current_activity(payload.get("current_activity"))
        if current_activity["mode"] != "background_only" or not current_activity["do_not_push_as_topic"]:
            raise LLMRefreshError("current_activity 必须是 background_only，且 do_not_push_as_topic 必须为 true")

        energy_level = _normalize_text(payload.get("energy_level"), max_length=4)
        if energy_level not in ENERGY_LEVEL_OPTIONS:
            raise LLMRefreshError("energy_level 不合法，应为：高 / 中 / 低")

        micro_experience = _normalize_text(payload.get("micro_experience"), max_length=MAX_FIELD_LENGTH)
        ambient_mood = _normalize_text(payload.get("ambient_mood"), max_length=MAX_FIELD_LENGTH)
        if not micro_experience:
            raise LLMRefreshError("micro_experience 不能为空")
        if not ambient_mood:
            raise LLMRefreshError("ambient_mood 不能为空")

        self._validate_period_texture_constraints(
            micro_experience=micro_experience,
            current_activity=current_activity["value"],
            daily_plan=current.get("daily_plan", {}),
        )

        validated = {
            "current_period": expected_period,
            "current_activity": current_activity,
            "micro_experience": micro_experience,
            "ambient_mood": ambient_mood,
            "life_state": _normalize_text(payload.get("life_state"), max_length=MAX_FIELD_LENGTH),
            "energy_level": energy_level,
            "activity_hint": _normalize_text(payload.get("activity_hint"), max_length=MAX_FIELD_LENGTH),
            "mood_hint": _normalize_text(payload.get("mood_hint"), max_length=MAX_FIELD_LENGTH),
            "social_hint": _normalize_text(payload.get("social_hint"), max_length=MAX_FIELD_LENGTH),
            "relationship_hint": _normalize_text(payload.get("relationship_hint"), max_length=MAX_FIELD_LENGTH),
        }
        for key, value in validated.items():
            if isinstance(value, str) and not value:
                raise LLMRefreshError(f"{key} 不能为空")
        return validated

    def _validate_chat_style_bias(self, value: dict[str, Any]) -> dict[str, Any]:
        chat_style_bias = _normalize_chat_style_bias(value)
        if chat_style_bias["explanation_density"] != "low":
            raise LLMRefreshError("chat_style_bias.explanation_density 必须为 low")
        if chat_style_bias["completion_bias"] != "low":
            raise LLMRefreshError("chat_style_bias.completion_bias 必须为 low")
        if not chat_style_bias["unfinished_utterance_allowed"]:
            raise LLMRefreshError("chat_style_bias.unfinished_utterance_allowed 必须为 true")
        if not chat_style_bias["summary_style_forbidden"]:
            raise LLMRefreshError("chat_style_bias.summary_style_forbidden 必须为 true")
        return chat_style_bias

    def _validate_association_style_bias(self, value: dict[str, Any]) -> dict[str, Any]:
        association_style_bias = _normalize_association_style_bias(value)
        if association_style_bias["style"] != "personal_hearsay":
            raise LLMRefreshError("association_style_bias.style 必须为 personal_hearsay")
        if not association_style_bias["encyclopedia_mode_forbidden"]:
            raise LLMRefreshError("association_style_bias.encyclopedia_mode_forbidden 必须为 true")
        if not association_style_bias["guide_style_forbidden"]:
            raise LLMRefreshError("association_style_bias.guide_style_forbidden 必须为 true")
        if not association_style_bias["allowed_patterns"]:
            raise LLMRefreshError("association_style_bias.allowed_patterns 不能为空")
        return association_style_bias

    def _validate_generated_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        missing = [key for key in REQUIRED_GENERATED_FIELDS if key not in payload]
        if missing:
            raise LLMRefreshError(f"LLM 输出缺少字段：{', '.join(missing)}")

        daily_plan = payload.get("daily_plan")
        if not isinstance(daily_plan, dict):
            raise LLMRefreshError("daily_plan 必须是对象")
        missing_plan_keys = [key for key in DAILY_PLAN_KEYS if key not in daily_plan]
        if missing_plan_keys:
            raise LLMRefreshError(f"daily_plan 缺少字段：{', '.join(missing_plan_keys)}")

        current_period = _normalize_text(payload.get("current_period"), max_length=MAX_FIELD_LENGTH)
        current_period = CURRENT_PERIOD_ALIASES.get(current_period, current_period)
        if current_period not in CURRENT_PERIOD_OPTIONS:
            raise LLMRefreshError(
                "current_period 不合法，应为：早上 / 上午 / 下午 / 晚上 / 深夜"
            )

        topic_bias = payload.get("topic_bias")
        if not isinstance(topic_bias, list):
            raise LLMRefreshError("topic_bias 必须是字符串列表")

        energy_level = _normalize_text(payload.get("energy_level"), max_length=4)
        if energy_level not in ENERGY_LEVEL_OPTIONS:
            raise LLMRefreshError("energy_level 不合法，应为：高 / 中 / 低")

        current_activity = _normalize_current_activity(payload.get("current_activity"))
        if current_activity["mode"] != "background_only" or not current_activity["do_not_push_as_topic"]:
            raise LLMRefreshError("current_activity 必须是 background_only，且 do_not_push_as_topic 必须为 true")

        latent_topic_bias = payload.get("latent_topic_bias")
        if not isinstance(latent_topic_bias, list):
            raise LLMRefreshError("latent_topic_bias 必须是字符串列表")

        chat_style_bias_raw = payload.get("chat_style_bias")
        if not isinstance(chat_style_bias_raw, dict):
            raise LLMRefreshError("chat_style_bias 必须是对象")
        chat_style_bias = _normalize_chat_style_bias(chat_style_bias_raw)
        if chat_style_bias["explanation_density"] != "low":
            raise LLMRefreshError("chat_style_bias.explanation_density 必须为 low")
        if chat_style_bias["completion_bias"] != "low":
            raise LLMRefreshError("chat_style_bias.completion_bias 必须为 low")
        if not chat_style_bias["unfinished_utterance_allowed"]:
            raise LLMRefreshError("chat_style_bias.unfinished_utterance_allowed 必须为 true")
        if not chat_style_bias["summary_style_forbidden"]:
            raise LLMRefreshError("chat_style_bias.summary_style_forbidden 必须为 true")

        association_style_bias_raw = payload.get("association_style_bias")
        if not isinstance(association_style_bias_raw, dict):
            raise LLMRefreshError("association_style_bias 必须是对象")
        association_style_bias = _normalize_association_style_bias(association_style_bias_raw)
        if association_style_bias["style"] != "personal_hearsay":
            raise LLMRefreshError("association_style_bias.style 必须为 personal_hearsay")
        if not association_style_bias["encyclopedia_mode_forbidden"]:
            raise LLMRefreshError("association_style_bias.encyclopedia_mode_forbidden 必须为 true")
        if not association_style_bias["guide_style_forbidden"]:
            raise LLMRefreshError("association_style_bias.guide_style_forbidden 必须为 true")
        if not association_style_bias["allowed_patterns"]:
            raise LLMRefreshError("association_style_bias.allowed_patterns 不能为空")

        micro_experience = _normalize_text(payload.get("micro_experience"), max_length=MAX_FIELD_LENGTH)
        ambient_mood = _normalize_text(payload.get("ambient_mood"), max_length=MAX_FIELD_LENGTH)
        carry_over_trace = _normalize_text(payload.get("carry_over_trace"), max_length=MAX_FIELD_LENGTH)
        if not micro_experience:
            raise LLMRefreshError("micro_experience 不能为空")
        if not ambient_mood:
            raise LLMRefreshError("ambient_mood 不能为空")
        if not carry_over_trace:
            raise LLMRefreshError("carry_over_trace 不能为空")

        self._validate_life_texture_constraints(
            micro_experience=micro_experience,
            carry_over_trace=carry_over_trace,
            current_activity=current_activity["value"],
            daily_plan=daily_plan,
        )

        validated = {
            "daily_plan": {
                key: _normalize_text(daily_plan.get(key), max_length=MAX_FIELD_LENGTH)
                for key in DAILY_PLAN_KEYS
            },
            "current_period": current_period,
            "current_activity": current_activity,
            "micro_experience": micro_experience,
            "ambient_mood": ambient_mood,
            "carry_over_trace": carry_over_trace,
            "latent_topic_bias": _normalize_unique_str_list(
                latent_topic_bias,
                DEFAULT_LATENT_TOPIC_BIAS,
                allow_empty=True,
                max_length=MAX_FIELD_LENGTH,
                max_items=MAX_TOPIC_COUNT,
            ),
            "chat_style_bias": chat_style_bias,
            "association_style_bias": association_style_bias,
            "life_state": _normalize_text(payload.get("life_state"), max_length=MAX_FIELD_LENGTH),
            "energy_level": energy_level,
            "activity_hint": _normalize_text(payload.get("activity_hint"), max_length=MAX_FIELD_LENGTH),
            "mood_hint": _normalize_text(payload.get("mood_hint"), max_length=MAX_FIELD_LENGTH),
            "social_hint": _normalize_text(payload.get("social_hint"), max_length=MAX_FIELD_LENGTH),
            "relationship_hint": _normalize_text(
                payload.get("relationship_hint"),
                max_length=MAX_FIELD_LENGTH,
            ),
            "topic_bias": _normalize_unique_str_list(
                topic_bias,
                DEFAULT_TOPIC_BIAS,
                allow_empty=True,
                max_length=MAX_FIELD_LENGTH,
                max_items=MAX_TOPIC_COUNT,
            ),
        }

        for key in REQUIRED_GENERATED_FIELDS:
            value = validated[key]
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if not sub_value:
                        raise LLMRefreshError(f"daily_plan.{sub_key} 不能为空")
                continue
            if isinstance(value, str) and not value:
                raise LLMRefreshError(f"{key} 不能为空")

        return validated

    def _validate_daily_plan_constraints(self, daily_plan: Any) -> None:
        plan_text = " ".join(_normalize_daily_plan(daily_plan).values())
        forbidden_plan_markers = ("10:", "11:", "12:", "三小时", "两小时", "准备", "要去", "结束后")
        if any(marker in plan_text for marker in forbidden_plan_markers):
            raise LLMRefreshError("daily_plan 像精确日程或时间线，请改成粗粒度背景节奏")

    def _validate_period_texture_constraints(
        self,
        micro_experience: str,
        current_activity: str,
        daily_plan: Any,
    ) -> None:
        action_like_markers = (
            "正在",
            "马上",
            "准备",
            "要去",
            "去上课",
            "去练琴",
            "去吃饭",
            "该睡",
            "赶紧睡",
        )
        for field_name, value in (
            ("micro_experience", micro_experience),
            ("current_activity.value", current_activity),
        ):
            if any(marker in value for marker in action_like_markers):
                raise LLMRefreshError(f"{field_name} 像行动计划，不是生活残留")

        recent_memory = self.memory_store.recent_for_generation()
        recent_micro = set(recent_memory.get("recent_micro_experiences", []))
        if micro_experience in recent_micro:
            raise LLMRefreshError("micro_experience 与最近 3 天完全重复")

        micro_category = _guess_trace_category(micro_experience)
        current_category = _guess_trace_category(current_activity)
        if micro_category == current_category and micro_category not in {"生活碎片", "情绪", "空"}:
            if not _is_light_trace(current_activity):
                raise LLMRefreshError("current_activity 与 micro_experience 主题过重重复")

        self._validate_daily_plan_constraints(daily_plan)

    def _validate_life_texture_constraints(
        self,
        micro_experience: str,
        carry_over_trace: str,
        current_activity: str,
        daily_plan: Any,
    ) -> None:
        action_like_markers = (
            "正在",
            "马上",
            "准备",
            "要去",
            "去上课",
            "去练琴",
            "去吃饭",
            "该睡",
            "赶紧睡",
        )
        for field_name, value in (
            ("micro_experience", micro_experience),
            ("current_activity.value", current_activity),
        ):
            if any(marker in value for marker in action_like_markers):
                raise LLMRefreshError(f"{field_name} 像行动计划，不是生活残留")

        recent_memory = self.memory_store.recent_for_generation()
        recent_micro = set(recent_memory.get("recent_micro_experiences", []))
        if micro_experience in recent_micro:
            raise LLMRefreshError("micro_experience 与最近 3 天完全重复")

        micro_category = _guess_trace_category(micro_experience)
        carry_category = _guess_trace_category(carry_over_trace)
        current_category = _guess_trace_category(current_activity)
        if micro_category == carry_category and micro_category not in {"生活碎片", "情绪", "空"}:
            if not _is_light_trace(carry_over_trace):
                raise LLMRefreshError("carry_over_trace 与 micro_experience 主题过重重复")
        if micro_category == carry_category == current_category and micro_category not in {"生活碎片", "情绪", "空"}:
            raise LLMRefreshError("micro_experience、carry_over_trace、current_activity 不应全部围绕同一主题")

        plan_text = " ".join(_normalize_daily_plan(daily_plan).values())
        forbidden_plan_markers = ("10:", "11:", "12:", "三小时", "两小时", "准备", "要去", "结束后")
        if any(marker in plan_text for marker in forbidden_plan_markers):
            raise LLMRefreshError("daily_plan 像精确日程或时间线，请改成粗粒度背景节奏")

    def _build_auto_refresh_prompt(self, current: dict[str, Any], settings: dict[str, Any]) -> str:
        now_local = datetime.now(_resolve_timezone(settings["timezone"]))
        memory_context = self.memory_store.recent_for_generation()
        current_snapshot = {
            "daily_plan": current.get("daily_plan", {}),
            "carry_over_trace": current.get("carry_over_trace", ""),
            "latent_topic_bias": current.get("latent_topic_bias", []),
            "chat_style_bias": current.get("chat_style_bias", {}),
            "association_style_bias": current.get("association_style_bias", {}),
            "topic_bias": current.get("topic_bias", []),
        }
        return (
            f"{settings['daily_life_prompt'].strip()}\n\n"
            f"当前本地时间（仅供判断大致时段，不要写进输出）: {now_local.strftime('%Y-%m-%d %H:%M')} {settings['timezone']}\n"
            f"上一版 daily 层 shared_life_context（可参考连续性，但不要机械复用）:\n"
            f"{json.dumps(current_snapshot, ensure_ascii=False, indent=2)}\n\n"
            "最近 shared_life_memory（用于连续性，不要逐字复用）:\n"
            f"{json.dumps(memory_context, ensure_ascii=False, indent=2)}\n\n"
            "防重复要求：\n"
            "- downweighted_categories 里的同类题材要降权，不要连续几天围绕同一主题\n"
            "- carry_over_trace 不要和最近记忆完全重复\n"
            "- daily_plan 不要写成精确日程或时间线\n\n"
            "P0 bias 要求：\n"
            "- shared_life_context 默认不是每轮都要使用的素材\n"
            "- chat_style_bias 必须压低 explanation_density 和 completion_bias\n"
            "- 语言倾向允许半句、省略、留白，不要总结式/说明式/客服式\n"
            "- association_style_bias 必须保持 personal_hearsay\n"
            "- 地点、食物、城市、见闻类话题要像个人听说或随口联想，不要百科或攻略"
        )

    def _build_period_refresh_prompt(
        self,
        current: dict[str, Any],
        settings: dict[str, Any],
        period_key: str,
        expected_period: str,
    ) -> str:
        now_local = datetime.now(_resolve_timezone(settings["timezone"]))
        memory_context = self.memory_store.recent_for_generation()
        current_snapshot = {
            "daily_plan": current.get("daily_plan", {}),
            "carry_over_trace": current.get("carry_over_trace", ""),
            "latent_topic_bias": current.get("latent_topic_bias", []),
            "current_period": current.get("current_period", ""),
            "current_activity": current.get("current_activity", ""),
            "micro_experience": current.get("micro_experience", ""),
            "ambient_mood": current.get("ambient_mood", ""),
            "life_state": current.get("life_state", ""),
            "energy_level": current.get("energy_level", ""),
            "activity_hint": current.get("activity_hint", ""),
            "mood_hint": current.get("mood_hint", ""),
            "social_hint": current.get("social_hint", ""),
            "relationship_hint": current.get("relationship_hint", ""),
        }
        return (
            f"{settings['period_life_prompt'].strip()}\n\n"
            f"当前本地时间: {now_local.strftime('%Y-%m-%d %H:%M')} {settings['timezone']}\n"
            f"当前配置时段 key: {period_key}\n"
            f"必须写入 current_period: {expected_period}\n\n"
            "当前 shared_life_context 快照（daily_plan 只能作为背景，不要覆盖）:\n"
            f"{json.dumps(current_snapshot, ensure_ascii=False, indent=2)}\n\n"
            "最近 shared_life_memory（用于避免重复，不要逐字复用）:\n"
            f"{json.dumps(memory_context, ensure_ascii=False, indent=2)}\n\n"
            "period_refresh 边界：\n"
            "- 只刷新当前时段状态，不要输出 daily_plan\n"
            "- 不要覆盖 carry_over_trace / latent_topic_bias / chat_style_bias / association_style_bias / topic_bias\n"
            "- current_period 必须符合当前真实时间窗口\n"
            "- 生成当前时段合理的生活残留，不要写昨晚或其他时段的氛围\n"
            "- 白天不要继续引用昨晚的“深夜”“空空的”“舌根涩味”等残留，除非只是很轻微的 carry-over 背景"
        )

    def _current_period_context(self, settings: dict[str, Any]) -> tuple[str, str]:
        now_local = datetime.now(_resolve_timezone(settings["timezone"]))
        period_times = _normalize_period_refresh_times(settings.get("period_refresh_times"))
        period_key = _period_key_for_time(now_local, period_times)
        return period_key, PERIOD_KEY_TO_CURRENT_PERIOD[period_key]

    def _ensure_daily_min_interval(self, current: dict[str, Any], settings: dict[str, Any]) -> None:
        min_interval_hours = int(settings["auto_refresh_min_interval_hours"])
        if min_interval_hours <= 0:
            return

        last_refresh = _parse_iso_datetime(
            current.get("last_daily_plan_refresh_at") or current.get("last_auto_refresh_at")
        )
        if last_refresh is None:
            return

        timezone_obj = _resolve_timezone(settings["timezone"])
        if last_refresh.tzinfo is None:
            last_refresh = last_refresh.replace(tzinfo=timezone_obj)
        else:
            last_refresh = last_refresh.astimezone(timezone_obj)

        now_local = datetime.now(timezone_obj)
        elapsed = now_local - last_refresh
        min_interval = timedelta(hours=min_interval_hours)
        if elapsed >= min_interval:
            return

        eligible_at = last_refresh + min_interval
        raise LLMRefreshError(
            f"距离上次每日刷新不足 {min_interval_hours} 小时，请在 {eligible_at.strftime('%Y-%m-%d %H:%M')} 之后再试"
        )

    def _ensure_period_min_interval(self, current: dict[str, Any], settings: dict[str, Any], period_key: str) -> None:
        min_interval_hours = int(settings["period_refresh_min_interval_hours"])
        if min_interval_hours <= 0:
            return

        last_refresh = _parse_iso_datetime(current.get("last_period_refresh_at"))
        if last_refresh is None:
            return

        timezone_obj = _resolve_timezone(settings["timezone"])
        if last_refresh.tzinfo is None:
            last_refresh = last_refresh.replace(tzinfo=timezone_obj)
        else:
            last_refresh = last_refresh.astimezone(timezone_obj)

        now_local = datetime.now(timezone_obj)
        elapsed = now_local - last_refresh
        min_interval = timedelta(hours=min_interval_hours)
        if elapsed >= min_interval:
            return

        eligible_at = last_refresh + min_interval
        raise LLMRefreshError(
            f"距离上次分时段刷新不足 {min_interval_hours} 小时，请在 {eligible_at.strftime('%Y-%m-%d %H:%M')} 之后再试"
        )

    async def _auto_refresh_loop(self):
        try:
            while True:
                await self._maybe_run_startup_auto_refresh()
                await self._maybe_run_scheduled_auto_refresh()
                await self._maybe_run_scheduled_period_refresh()
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("shared_life_context auto refresh loop crashed")

    async def _maybe_run_startup_auto_refresh(self):
        if self._startup_refresh_attempted:
            return

        settings = self.config_store.load()
        if not settings["auto_refresh_on_startup"]:
            self._startup_refresh_attempted = True
            return

        self._startup_refresh_attempted = True
        try:
            await self._auto_refresh_once(reason="startup")
        except LLMRefreshError:
            logger.exception("startup auto refresh skipped or failed")

    async def _maybe_run_scheduled_auto_refresh(self):
        settings = self.config_store.load()
        if not settings["auto_refresh_enabled"]:
            return

        timezone_obj = _resolve_timezone(settings["timezone"])
        now_local = datetime.now(timezone_obj)
        target_text = settings["auto_refresh_time"]
        if now_local.strftime("%H:%M") != target_text:
            return

        if not self._is_due_for_current_slot(now_local, settings):
            return

        try:
            await self._daily_plan_refresh_once(reason="schedule")
        except LLMRefreshError:
            logger.exception("scheduled daily plan refresh failed")

    async def _maybe_run_scheduled_period_refresh(self):
        settings = self.config_store.load()
        if not settings["period_refresh_enabled"]:
            return

        timezone_obj = _resolve_timezone(settings["timezone"])
        now_local = datetime.now(timezone_obj)
        period_times = settings["period_refresh_times"]
        matched_keys = [key for key, value in period_times.items() if now_local.strftime("%H:%M") == value]
        if not matched_keys:
            return
        period_key = matched_keys[0]

        if not self._is_due_for_period_slot(now_local, settings, period_key):
            return

        try:
            await self._period_refresh_once(reason=f"schedule:{period_key}")
        except LLMRefreshError:
            logger.exception("scheduled period refresh failed: period_key=%s", period_key)

    def _is_due_for_current_slot(self, now_local: datetime, settings: dict[str, Any]) -> bool:
        current = self.service.load()
        scheduled_time = _parse_refresh_time(settings["auto_refresh_time"])
        slot_start = now_local.replace(
            hour=scheduled_time.hour,
            minute=scheduled_time.minute,
            second=0,
            microsecond=0,
        )
        last_refresh = _parse_iso_datetime(
            current.get("last_daily_plan_refresh_at") or current.get("last_auto_refresh_at")
        )
        if last_refresh is not None:
            if last_refresh.tzinfo is None:
                last_refresh = last_refresh.replace(tzinfo=now_local.tzinfo)
            else:
                last_refresh = last_refresh.astimezone(now_local.tzinfo)
            if last_refresh >= slot_start:
                return False

        min_interval_hours = int(settings["auto_refresh_min_interval_hours"])
        if min_interval_hours > 0 and last_refresh is not None:
            if now_local - last_refresh < timedelta(hours=min_interval_hours):
                return False

        return True

    def _is_due_for_period_slot(self, now_local: datetime, settings: dict[str, Any], period_key: str) -> bool:
        current = self.service.load()
        period_times = settings["period_refresh_times"]
        slot_start = _period_slot_start(now_local, period_key, period_times)
        last_refresh = _parse_iso_datetime(current.get("last_period_refresh_at"))
        if last_refresh is not None:
            if last_refresh.tzinfo is None:
                last_refresh = last_refresh.replace(tzinfo=now_local.tzinfo)
            else:
                last_refresh = last_refresh.astimezone(now_local.tzinfo)
            if last_refresh >= slot_start and current.get("last_period_key") == period_key:
                return False

        min_interval_hours = int(settings["period_refresh_min_interval_hours"])
        if min_interval_hours > 0 and last_refresh is not None:
            if now_local - last_refresh < timedelta(hours=min_interval_hours):
                return False

        return True

    def _render_auto_status(self) -> str:
        settings = self.config_store.load()
        data = self.service.load()
        next_refresh_hint = (
            "未启用自动刷新"
            if not settings["auto_refresh_enabled"]
            else self._compute_next_refresh_hint(data, settings)
        )
        lines = [
            f"auto_refresh_enabled: {str(settings['auto_refresh_enabled']).lower()}",
            f"auto_refresh_time: {settings['auto_refresh_time']}",
            f"last_auto_refresh_at: {_normalize_text(data.get('last_auto_refresh_at'), '未刷新')}",
            f"last_daily_plan_refresh_at: {_normalize_text(data.get('last_daily_plan_refresh_at'), '未刷新')}",
            f"next_refresh_hint: {next_refresh_hint}",
            f"auto_refresh_min_interval_hours: {settings['auto_refresh_min_interval_hours']}",
            f"timezone: {settings['timezone']}",
            f"llm_provider_id: {_normalize_text(settings['llm_provider_id'], '默认 provider')}",
        ]
        return "\n".join(lines)

    def _render_period_status(self) -> str:
        settings = self.config_store.load()
        data = self.service.load()
        next_refresh_hint = (
            "未启用分时段刷新"
            if not settings["period_refresh_enabled"]
            else self._compute_next_period_refresh_hint(data, settings)
        )
        return "\n".join(
            [
                f"period_refresh_enabled: {str(settings['period_refresh_enabled']).lower()}",
                f"current_period: {_format_value(data.get('current_period'))}",
                f"last_period_refresh_at: {_normalize_text(data.get('last_period_refresh_at'), '未刷新')}",
                f"last_period_key: {_normalize_text(data.get('last_period_key'), '未设置')}",
                f"next_period_refresh_hint: {next_refresh_hint}",
                f"period_refresh_min_interval_hours: {settings['period_refresh_min_interval_hours']}",
                "period_refresh_times:",
                json.dumps(settings["period_refresh_times"], ensure_ascii=False, indent=2),
            ]
        )

    def _compute_next_refresh_hint(self, data: dict[str, Any], settings: dict[str, Any]) -> str:
        timezone_obj = _resolve_timezone(settings["timezone"])
        now_local = datetime.now(timezone_obj)
        scheduled_time = _parse_refresh_time(settings["auto_refresh_time"])
        candidate = now_local.replace(
            hour=scheduled_time.hour,
            minute=scheduled_time.minute,
            second=0,
            microsecond=0,
        )
        if candidate <= now_local:
            candidate += timedelta(days=1)

        last_refresh = _parse_iso_datetime(data.get("last_daily_plan_refresh_at") or data.get("last_auto_refresh_at"))
        if last_refresh is not None:
            if last_refresh.tzinfo is None:
                last_refresh = last_refresh.replace(tzinfo=timezone_obj)
            else:
                last_refresh = last_refresh.astimezone(timezone_obj)
            min_interval = timedelta(hours=int(settings["auto_refresh_min_interval_hours"]))
            min_eligible_at = last_refresh + min_interval
            while candidate < min_eligible_at:
                candidate += timedelta(days=1)

        return candidate.strftime("%Y-%m-%d %H:%M %Z")

    def _compute_next_period_refresh_hint(self, data: dict[str, Any], settings: dict[str, Any]) -> str:
        timezone_obj = _resolve_timezone(settings["timezone"])
        now_local = datetime.now(timezone_obj)
        period_times = settings["period_refresh_times"]
        candidates: list[tuple[datetime, str]] = []
        for key, time_text in period_times.items():
            parsed = _parse_refresh_time(time_text)
            candidate = now_local.replace(
                hour=parsed.hour,
                minute=parsed.minute,
                second=0,
                microsecond=0,
            )
            if candidate <= now_local:
                candidate += timedelta(days=1)
            candidates.append((candidate, key))

        last_refresh = _parse_iso_datetime(data.get("last_period_refresh_at"))
        if last_refresh is not None:
            if last_refresh.tzinfo is None:
                last_refresh = last_refresh.replace(tzinfo=timezone_obj)
            else:
                last_refresh = last_refresh.astimezone(timezone_obj)
            min_eligible_at = last_refresh + timedelta(hours=int(settings["period_refresh_min_interval_hours"]))
        else:
            min_eligible_at = now_local

        for _ in range(8):
            candidate, key = min(candidates, key=lambda item: item[0])
            if candidate >= min_eligible_at:
                return f"{candidate.strftime('%Y-%m-%d %H:%M %Z')} ({key}/{PERIOD_KEY_TO_CURRENT_PERIOD[key]})"
            candidates = [
                (item_candidate + timedelta(days=1), item_key) if item_candidate == candidate and item_key == key else (item_candidate, item_key)
                for item_candidate, item_key in candidates
            ]

        candidate, key = min(candidates, key=lambda item: item[0])
        return f"{candidate.strftime('%Y-%m-%d %H:%M %Z')} ({key}/{PERIOD_KEY_TO_CURRENT_PERIOD[key]})"

    def _render_auto_refresh_success(self, data: dict[str, Any]) -> str:
        return (
            "每日完整刷新成功\n\n"
            f"daily_plan: {_format_daily_plan_inline(data.get('daily_plan', {}))}\n"
            f"current_activity: {_format_value(data.get('current_activity'))}\n"
            f"micro_experience: {_format_value(data.get('micro_experience'))}\n"
            f"ambient_mood: {_format_value(data.get('ambient_mood'))}\n"
            f"carry_over_trace: {_format_value(data.get('carry_over_trace'))}\n"
            f"energy_level: {_format_value(data.get('energy_level'))}\n"
            f"current_period: {_format_value(data.get('current_period'))}\n"
            f"life_state: {_format_value(data.get('life_state'))}\n"
            f"mood_hint: {_format_value(data.get('mood_hint'))}\n"
            f"social_hint: {_format_value(data.get('social_hint'))}\n"
            f"chat_style_bias: {_format_value(data.get('chat_style_bias'))}\n"
            f"association_style_bias: {_format_value(data.get('association_style_bias'))}\n"
            f"topic_bias: {_format_value(data.get('topic_bias', []))}"
        )

    def _render_period_refresh_success(self, data: dict[str, Any]) -> str:
        return (
            "分时段刷新成功\n\n"
            f"current_period: {_format_value(data.get('current_period'))}\n"
            f"last_period_key: {_format_value(data.get('last_period_key'))}\n"
            f"current_activity: {_format_value(data.get('current_activity'))}\n"
            f"micro_experience: {_format_value(data.get('micro_experience'))}\n"
            f"ambient_mood: {_format_value(data.get('ambient_mood'))}\n"
            f"life_state: {_format_value(data.get('life_state'))}\n"
            f"energy_level: {_format_value(data.get('energy_level'))}\n"
            f"mood_hint: {_format_value(data.get('mood_hint'))}\n"
            f"social_hint: {_format_value(data.get('social_hint'))}"
        )

    def _render_summary(self, data: dict[str, Any]) -> str:
        lines = ["【shared_life_context 摘要】"]
        updated_at = _normalize_text(data.get("updated_at"), "未更新")
        last_auto_refresh_at = _normalize_text(data.get("last_auto_refresh_at"), "未自动刷新")
        last_daily_plan_refresh_at = _normalize_text(data.get("last_daily_plan_refresh_at"), "未刷新 daily_plan")
        last_period_refresh_at = _normalize_text(data.get("last_period_refresh_at"), "未刷新 period")
        lines.append(f"updated_at: {updated_at}")
        lines.append(f"last_auto_refresh_at: {last_auto_refresh_at}")
        lines.append(f"last_daily_plan_refresh_at: {last_daily_plan_refresh_at}")
        lines.append(f"last_period_refresh_at: {last_period_refresh_at}")
        lines.append(f"last_period_key: {_normalize_text(data.get('last_period_key'), '未设置')}")
        for key in SUMMARY_FIELDS:
            lines.append(f"{key}: {_format_value(data.get(key))}")
        lines.append(f"daily_plan: {_format_daily_plan_inline(data.get('daily_plan', {}))}")
        lines.append(f"topic_bias: {_format_value(data.get('topic_bias', []))}")
        lines.append(f"forbidden: {_format_value(data.get('forbidden', []))}")
        return "\n".join(lines)

    def _help_text(self) -> str:
        return (
            "shared_life_context 命令：\n"
            "/slc show\n"
            "/slc json\n"
            "/slc set <field> <value>\n"
            "/slc set_plan <morning|afternoon|evening|night> <value>\n"
            "/slc add_topic <topic>\n"
            "/slc remove_topic <topic>\n"
            "/slc plan\n"
            "/slc prompt\n"
            "/slc qzone_prompt\n"
            "/slc chat_context\n"
            "/slc memory\n"
            "/slc add_trace <trace>\n"
            "/slc regen_memory\n"
            "/slc reset_memory\n"
            "/slc auto_status\n"
            "/slc auto_refresh\n"
            "/slc period_status\n"
            "/slc period_refresh\n"
            "/slc reset"
        )

    def render_chat_context(self) -> str:
        settings = self.config_store.load()
        if not settings["enable_chat_context_export"]:
            return ""

        data = self.service.load()
        return (
            "【today_context】\n\n"
            f"{_render_priority_guard_block()}\n\n"
            f"{_render_daily_plan_block(data.get('daily_plan', {}))}\n\n"
            "今日生活残留：\n"
            f"{_format_value(data.get('micro_experience'))}\n\n"
            "延续痕迹：\n"
            f"{_format_value(data.get('carry_over_trace'))}\n\n"
            "当前活动：\n"
            "这是 background_only，不要主动推成话题。\n"
            f"{_format_value(data.get('current_activity'))}\n\n"
            "当前电量：\n"
            f"{_format_value(data.get('energy_level'))}\n\n"
            "当前情绪：\n"
            f"{_format_value(data.get('mood_hint'))}\n\n"
            "环境情绪：\n"
            f"{_format_value(data.get('ambient_mood'))}\n\n"
            "潜在话题偏置：\n"
            f"{_format_value(data.get('latent_topic_bias'))}\n"
            "只有用户提到相关内容时才触发，不要主动拉回。\n\n"
            "语言风格偏置：\n"
            f"{_render_chat_style_bias(data.get('chat_style_bias'))}\n"
            "少解释，少总结，少把话说圆；允许半句、省略、接梗。\n"
            "只影响句子长度、软硬和轻微调侃，不影响话题主线。\n\n"
            "联想风格偏置：\n"
            f"{_render_association_style_bias(data.get('association_style_bias'))}\n"
            "涉及地点、食物、城市、见闻时，优先像个人听说或随口联想。\n"
            "不要列景点、列特产、解释背景、做攻略。\n\n"
            "不要把 today_context 逐字复述；\n"
            "它只用于影响回复倾向。"
        )

    def _resolve_data_root(self) -> Path | None:
        try:
            return Path(get_astrbot_data_path())
        except Exception:
            logger.warning("AstrBot data root is unavailable in the current environment")
            return None

    def _resolve_data_file(self) -> Path:
        fallback_dir = self.plugin_dir / "data"
        candidate_dirs: list[Path] = []
        if self.data_root is not None:
            plugin_name = getattr(self, "name", "") or PLUGIN_NAME
            candidate_dirs.append(self.data_root / "plugin_data" / plugin_name)
        candidate_dirs.append(fallback_dir)

        for directory in candidate_dirs:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                probe_path = directory / ".write_probe"
                probe_path.write_text("", encoding="utf-8")
                probe_path.unlink()
                logger.info("shared_life_context storage ready: %s", directory)
                return directory / DATA_FILENAME
            except Exception:
                logger.exception("storage path is unavailable: %s", directory)

        return fallback_dir / DATA_FILENAME

    def _resolve_schema_config_file(self) -> Path | None:
        if self.data_root is None:
            return None
        return self.data_root / "config" / SCHEMA_CONFIG_FILENAME

    def _start_scheduler_if_possible(self) -> None:
        if self._auto_refresh_task is not None and not self._auto_refresh_task.done():
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        self._auto_refresh_task = loop.create_task(self._auto_refresh_loop())

    def _strip_prefix(self, message: str) -> str:
        stripped = message.strip()
        if stripped.startswith("/slc"):
            return stripped[len("/slc") :].strip()
        if stripped.startswith("slc"):
            return stripped[len("slc") :].strip()
        return stripped

    async def terminate(self):
        if self._auto_refresh_task is not None:
            self._auto_refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._auto_refresh_task
