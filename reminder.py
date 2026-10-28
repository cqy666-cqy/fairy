# -*- coding: utf-8 -*-
"""定时提醒模块：从用户话里解析时间，存入 reminders.json，到点后由前端轮询触发播报。

设计原则：
- 零新依赖（标准库 + JSON 文件）
- 解析失败/没命中关键词一律返回 None，交给原有流程，绝不干扰普通聊天
- 到点判定由 due_reminders() 完成，谁调用谁处理（网页版走 HTTP 轮询）
"""
import os
import re
import json
import time
import threading
import datetime

_STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reminders.json")
_lock = threading.Lock()

# 触发提醒意图的关键词（必须同时命中时间表达式才算提醒）
_REMIND_KW = ("提醒", "记得", "别忘了", "别忘", "喊我", "叫我起床", "叫我")

_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "俩": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0}

_UNIT_SEC = {"秒": 1, "分": 60, "分钟": 60, "小时": 3600, "钟头": 3600, "个小时": 3600}


def _cn2num(s):
    """把 '3'/'三'/'十'/'十五'/'二十三' 这类转成整数，失败返回 None"""
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s == "半":
        return 0
    # 中文数字（支持到 99）
    if "十" in s:
        parts = s.split("十")
        tens = _CN_NUM.get(parts[0], 1) if parts[0] else 1
        ones = _CN_NUM.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    if len(s) == 1 and s in _CN_NUM:
        return _CN_NUM[s]
    return None


_NUM = r"(\d{1,3}|[零一二两俩三四五六七八九十]{1,3})"

# 相对时间：X分钟/小时/秒后
_REL = re.compile(_NUM + r"\s*(秒钟|秒|分钟|分|个小时|小时|钟头)\s*(?:之?后|以后|后|内)")
# 绝对时间：可选「日期 + 时段 + X点[半|一刻|X分]」
_DAY = r"(今天|明天|后天|今晚|今早|明晚|明早|今晨)"
_PERIOD = r"(凌晨|清晨|早上|早晨|上午|中午|下午|傍晚|晚上|晚间|夜里)"
_CLOCK = re.compile(
    r"(?:" + _DAY + r"\s*)?(?:" + _PERIOD + r"\s*)?" +
    _NUM + r"\s*[点:：]\s*(\d{1,2}|半|一刻|[零一二两俩三四五六七八九十]{1,2})?"
)


def _parse_due(text):
    """从文本解析出 (截止时间戳, 人话时间描述)。无法解析返回 (None, None)"""
    now = datetime.datetime.now()

    m = _REL.search(text)
    if m:
        n = _cn2num(m.group(1))
        if n is not None:
            sec = n * _UNIT_SEC.get(m.group(2), 60)
            if sec <= 0:
                sec = 60
            due = time.time() + sec
            unit_name = m.group(2).replace("个", "")
            desc = f"{n}{unit_name}后"
            return due, desc

    m = _CLOCK.search(text)
    if m:
        day, period = m.group(1), m.group(2)
        hh = _cn2num(m.group(3))
        if hh is not None and 0 <= hh <= 23:
            hh_disp = hh
            mm_raw = m.group(4)
            if mm_raw in (None, ""):
                mm = 0
            elif mm_raw == "半":
                mm = 30
            elif mm_raw == "一刻":
                mm = 15
            else:
                mm = _cn2num(mm_raw)
                if mm is None:
                    mm = 0
            if not (0 <= mm <= 59):
                mm = 0
            # 下午/傍晚/晚上 的 1~11 点按 12 小时制补成 24 小时制
            if period in ("下午", "傍晚", "晚上", "晚间", "夜里") and hh < 12:
                hh += 12
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if day in ("明天", "明晚", "明早", "明晨"):
                target += datetime.timedelta(days=1)
            elif day == "后天":
                target += datetime.timedelta(days=2)
            elif target <= now:
                target += datetime.timedelta(days=1)
            due = target.timestamp()
            desc = f"{day or ''}{period or ''}{hh_disp}点{('%02d' % mm) if mm else ''}"
            return due, desc

    return None, None


def _extract_task(text, matched_span):
    """去掉时间片段和引导词，尽量抽出'要提醒的事'"""
    task = text.replace(matched_span, " ")
    task = re.sub(r"(请|帮我|帮忙|一定|要|记得|别忘了|别忘|提醒我|提醒|叫我|喊我)", " ", task)
    task = re.sub(r"^\s*在\s*", "", task)
    task = task.strip(" ，。,、！？!?~")
    return task or "这件事"


def _load():
    if not os.path.exists(_STORE):
        return []
    try:
        with open(_STORE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items):
    try:
        with open(_STORE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def try_handle(user_text):
    """如果这句话是'设提醒'请求，就落库并返回 (确认回复, 情绪)；否则返回 None。
    调用方在正常对话流程最前面调用它。"""
    if not user_text or not any(k in user_text for k in _REMIND_KW):
        return None
    due, desc = _parse_due(user_text)
    if due is None:
        return None
    # 抽出被匹配到的时间片段原文
    span = ""
    for rx in (_REL, _CLOCK):
        m = rx.search(user_text)
        if m:
            span = m.group(0)
            break
    task = _extract_task(user_text, span)
    item = {
        "task": task,
        "due": due,
        "desc": desc,
        "created": time.time(),
        "delivered": False,
    }
    with _lock:
        items = _load()
        items.append(item)
        _save(items)
    return f"好呀，{desc}我会提醒你{task}哦，到时候我会喊你的。", "gentle"


def due_reminders():
    """返回所有已到点且未播报的提醒（并标记为已播报），每项含 task + sentence"""
    now = time.time()
    out = []
    with _lock:
        items = _load()
        changed = False
        for it in items:
            if it.get("delivered"):
                continue
            if now >= it.get("due", 0):
                it["delivered"] = True
                changed = True
                out.append({
                    "task": it.get("task", ""),
                    "sentence": f"提醒时间到啦——{it.get('task', '')}，该行动咯。",
                })
        if changed:
            _save(items)
    return out


def list_reminders():
    """未播报的提醒列表（供调试/展示）"""
    with _lock:
        items = _load()
    return [{"task": it.get("task"), "due": it.get("due"), "desc": it.get("desc"),
             "delivered": it.get("delivered", False)} for it in items]
