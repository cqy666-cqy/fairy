# -*- coding: utf-8 -*-
"""主动陪伴：什么时候开口、说什么、什么时候闭嘴。

四道闸：
1. 时机：8:00~23:00；两小时内刚聊过不发；每天常态 2 条、最多 3 条；时段过了 90 分钟不补发
2. 真实锚点：只允许从**真正拥有**的信息里挑——记忆事实、对话历史、实时数据；没有就不发
3. 质量：不假装物理体验（踩地砖/翻书/闻香味全是假前提）、不带叮嘱套话、14 天内不重复
4. 频率自适应：连续 2 天没回应降到每天 1 条，连续 3 天暂停 3 天

总开关：用户说「别主动找我了」就停，说「可以主动找我」再开。
状态落盘在 state/company_state.json，重启不丢。
"""
import datetime
import difflib
import json
import os
import random
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE_DIR = os.path.join(BASE_DIR, "state")
_STATE_PATH = os.path.join(_STATE_DIR, "company_state.json")

# 三个可开口的时间段（分钟）：上午、下午、晚上；每天随机挑，不做成定时三件套
_WINDOWS = ((8 * 60 + 30, 11 * 60 + 30),
            (14 * 60, 17 * 60 + 30),
            (19 * 60 + 30, 22 * 60 + 30))
_QUIET_AFTER_CHAT = 20 * 60    # 他刚说过话，20 分钟内不打扰
_WINDOW_EXPIRE = 90 * 60       # 计划时间过了 90 分钟，这条作废
_MAX_PER_DAY = 5               # 硬上限
_RECENT_DAYS = 14              # 查重窗口
_PAUSE_DAYS = 3                # 连续 3 天没回应，暂停 3 天
_WEB_PER_DAY = 1               # 每天最多联网找一次「新发现」，省搜索额度

# 她自己的世界：不依赖他提过什么，也有想说的东西。
# 只放「她本来就会琢磨」的题目，不放感官体验（她没身体）。
_OWN_TOPICS = (
    ("须弥的雨林和沙漠只隔一道墙这件事", "觉得这个设定很妙"),
    ("草元素和其他元素反应的设计", "越想越觉得有巧思"),
    ("教令院那些学者的执念", "一直想不通他们图什么"),
    ("赤王遗迹背后的那段历史", "对没讲完的部分很好奇"),
    ("虚空终端的运作方式", "觉得它和人类的习惯很像"),
    ("人类做研究时的那种劲头", "看着觉得有意思"),
    ("新版本里冒出来的角色", "有点想看看大家怎么评价"),
    ("新番预告里那个画风", "说不上来哪里怪，就是记住了"),
)

_WEB_QUERIES = (
    "原神 新角色 爆料",
    "原神 版本 前瞻 更新",
    "原神 近期 活动 卡池",
    "新番 动画 预告",
)

# 只有聊这些的句子才适合拿来当话题由头；别的一律不碰（脏话、评价人、情绪发泄之类）
_TOPIC_WORDS = (
    "原神", "游戏", "王者", "英雄", "版本", "卡池", "角色", "番", "动漫", "漫画",
    "电影", "剧", "音乐", "歌", "课", "考试", "专业", "学校", "论文", "实验",
    "食堂", "吃", "零食", "天气", "雨", "雪", "城市", "家乡", "南昌", "高安",
    "旅行", "玩", "手机", "电脑", "宿舍", "图书馆", "运动", "跑", "球",
)
_BAD_TOPIC = ("玛德", "他妈", "傻逼", "内个", "黑人", "萝莉", "色情", "裸照", "约炮", "我操", "操你")


# ==================== 状态读写 ====================
def _now():
    return datetime.datetime.now()


def _today_key(now=None):
    return (now or _now()).strftime("%Y-%m-%d")


def _load():
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict):
            return st
    except Exception:
        pass
    return {}


def _save(st):
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _STATE_PATH)
    except Exception as e:
        print(f"【陪伴】状态写入失败：{e}")


def _day(st, now):
    days = st.setdefault("days", {})
    d = days.setdefault(_today_key(now), {})
    d.setdefault("slots", [])
    d.setdefault("sent", 0)
    d.setdefault("responded", False)
    d.setdefault("sources", [])
    d.setdefault("last_sent_ts", 0.0)
    return d


def _roll(st, now):
    """换日结算：昨天发出过、但他整天没理，就记一次「没回应」"""
    cur = _today_key(now)
    last = st.get("last_day")
    if last == cur:
        return
    if last:
        y = (st.get("days") or {}).get(last)
        if y:
            if y.get("sent", 0) > 0 and not y.get("responded"):
                st["ignore_streak"] = int(st.get("ignore_streak", 0)) + 1
            elif y.get("responded"):
                st["ignore_streak"] = 0
        if int(st.get("ignore_streak", 0)) >= 3:
            until = (now + datetime.timedelta(days=_PAUSE_DAYS)).strftime("%Y-%m-%d")
            st["pause_until"] = until
            st["ignore_streak"] = 2  # 暂停结束后从「每天 1 条」接着来
            print(f"【陪伴】连续 3 天没回应，暂停到 {until}")
    st["last_day"] = cur
    # 清理太久以前的日子与查重记录
    cutoff = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    for k in list((st.get("days") or {}).keys()):
        if k < cutoff:
            st["days"].pop(k, None)
    keep_ts = (now - datetime.timedelta(days=_RECENT_DAYS)).timestamp()
    st["recent"] = [r for r in (st.get("recent") or []) if float(r.get("ts") or 0) > keep_ts][-40:]


def _cap(st):
    """当天最多几条：连续 2 天没回应就只剩 1 条"""
    return 1 if int(st.get("ignore_streak", 0)) >= 2 else _MAX_PER_DAY


# ==================== 对外：开关 / 记录 / 调度 ====================
def is_enabled():
    return bool(_load().get("enabled", True))


def set_enabled(on, why=""):
    st = _load()
    _roll(st, _now())
    st["enabled"] = bool(on)
    if on:
        st["ignore_streak"] = 0
        st["pause_until"] = ""
    _save(st)
    print(f"【陪伴】主动开口已{'开启' if on else '关闭'}{('：' + why) if why else ''}")
    return bool(on)


def note_user_message(user_id="default", now=None):
    """他说话了：记活跃时间、算作回应，顺便解除降频与暂停"""
    now = now or _now()
    st = _load()
    _roll(st, now)
    st["last_user_ts"] = now.timestamp()
    d = _day(st, now)
    if d.get("sent", 0) > 0:
        d["responded"] = True
    if st.get("pause_until"):
        st["pause_until"] = ""
        print("【陪伴】他回来了，暂停解除")
    if int(st.get("ignore_streak", 0)) > 0:
        st["ignore_streak"] = 0
    _save(st)


def _ensure_slots(st, d, now):
    """今天还没排过，就随机挑时段；晚生成时只看还来得及的窗口"""
    if d.get("slots"):
        return
    cap = _cap(st)
    n = 1 if cap <= 1 else 2
    if cap >= 3 and random.random() < 0.2:
        n = 3
    now_min = now.hour * 60 + now.minute
    usable = [w for w in _WINDOWS if w[1] - 30 > now_min]
    if not usable:
        d["slots"] = []
        return
    picked = random.sample(usable, min(n, len(usable)))
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = []
    for a, b in picked:
        at = base + datetime.timedelta(minutes=random.randint(max(a, now_min + 25), b))
        slots.append({"at": at.isoformat(timespec="minutes"), "done": False})
    slots.sort(key=lambda s: s["at"])
    d["slots"] = slots
    print(f"【陪伴】今天的开口时段：{', '.join(s['at'][-5:] for s in slots)}")


def next_due(user_id="default", now=None):
    """到点了吗？返回该说话的时段 dict，否则 None。状态没变就不写盘"""
    now = now or _now()
    st = _load()
    snap = json.dumps(st, sort_keys=True, ensure_ascii=False)
    slot = _next_due(st, now)
    if json.dumps(st, sort_keys=True, ensure_ascii=False) != snap:
        _save(st)
    return slot


def _next_due(st, now):
    _roll(st, now)

    if not st.get("enabled", True):
        return None
    pause = st.get("pause_until") or ""
    if pause:
        if _today_key(now) < pause:
            return None
        st["pause_until"] = ""
    if now.hour < 8 or now.hour >= 23:
        return None
    if now.timestamp() - float(st.get("last_user_ts") or 0) < _QUIET_AFTER_CHAT:
        return None

    d = _day(st, now)
    _ensure_slots(st, d, now)
    if d["sent"] >= _cap(st):
        return None

    for slot in d["slots"]:
        if slot.get("done"):
            continue
        at = datetime.datetime.fromisoformat(slot["at"])
        if now < at:
            break
        if (now - at).total_seconds() > _WINDOW_EXPIRE:
            slot["done"] = True
            slot["skip"] = "过期"
            print(f"【陪伴】{slot['at'][-5:]} 的时段已过期，不补发")
            continue
        return slot
    return None


def note_sent(text, source, slot=None, now=None):
    now = now or _now()
    st = _load()
    _roll(st, now)
    d = _day(st, now)
    d["sent"] = int(d.get("sent", 0)) + 1
    d["last_sent_ts"] = now.timestamp()
    d.setdefault("sources", []).append(source)
    if slot:
        for s in d["slots"]:
            if s.get("at") == slot.get("at"):
                s["done"] = True
    st.setdefault("recent", []).append(
        {"ts": now.timestamp(), "text": text, "source": source})
    _save(st)


def skip_slot(slot, reason=""):
    st = _load()
    d = _day(st, _now())
    for s in d["slots"]:
        if s.get("at") == slot.get("at"):
            s["done"] = True
            s["skip"] = reason or "跳过"
    _save(st)


def recent_texts():
    """最近 14 天说过的主动消息，用于查重"""
    st = _load()
    keep_ts = (_now() - datetime.timedelta(days=_RECENT_DAYS)).timestamp()
    return [r.get("text", "") for r in (st.get("recent") or [])
            if float(r.get("ts") or 0) > keep_ts]


# ==================== 真实锚点选择 ====================
# 主动开口只允许基于三类**真正拥有**的信息：
#   1) 记忆库里的事实（他在南昌读大三、看过无职转生等）
#   2) 对话历史里的最近话题（翻到他说过的一段话）
#   3) 实时数据（天气变化、节日）
# 没有这三类就不发——宁可不发也不要编物理体验（踩地砖/翻书/闻香味全是假前提）


def _emotion_for(user_id, st, last_emotion_hist=None):
    """算此刻她应该带着什么情绪开口"""
    recent_days = (st.get("recent_days_no_response") or 0)
    if recent_days >= 2:
        return "有点怕打扰的小心翼翼——想找他说又怕他忙"
    return "自然想起了某件事，随口想告诉他的放松心情"


def pick_real_anchor_and_emotion(user_id="default", now=None):
    """从记忆/对话历史/实时数据里挑一个真实锚点 + 此刻情绪。
    返回 (source, anchor文本, emotion文本)。source=None 表示没有真实锚点，上游应跳过"""
    now = now or _now()
    st = _load()
    used = ((st.get("days") or {}).get(_today_key(now), {}) or {}).get("sources") or []

    # 最近 7 天已经用过的由头，避免翻来覆去说同一件
    keep_ts = (now - datetime.timedelta(days=7)).timestamp()
    used_topics = [t.get("text", "") for t in (st.get("recent_topics") or [])
                   if float(t.get("ts") or 0) > keep_ts]

    # 算连续几天没回应
    days_no_resp = 0
    for d_key in sorted((st.get("days") or {}).keys(), reverse=True):
        if d_key == _today_key(now):
            continue
        d = st["days"][d_key]
        if d.get("sent", 0) and not d.get("responded", False):
            days_no_resp += 1
        elif d.get("responded", False):
            break

    # 情绪先算好
    emotion = _emotion_for(user_id, st)

    # ---- 来源 1：记忆事实 ----
    facts = []
    try:
        import memory
        facts = memory.list_facts(user_id)
    except Exception:
        pass
    events = [f for f in facts if f.get("category") == "event"]
    # 「称呼」「不要…」这类是规则，不是可以拿来聊的话题，剔掉
    known = [f for f in facts if f.get("category") in ("stable", "temporary")
             and f.get("key") not in ("称呼",)
             and len(str(f.get("value", ""))) >= 4
             and any(w in str(f.get("value", "")) for w in _TOPIC_WORDS)
             and not any(w in str(f.get("value", "")) for w in _BAD_TOPIC)
             and not any(w in str(f.get("value", "")) for w in ("不要", "别叫", "禁止", "称呼"))]

    # ---- 来源 2：对话历史里的最近话题 ----
    hist_snippets = []
    try:
        import memory as _m
        with _m._conn() as c:
            rows = c.execute(
                "SELECT content FROM conversations WHERE user_id=? AND role='user' "
                "AND created_at > datetime('now', '-7 days') ORDER BY id DESC LIMIT 40",
                (user_id,)).fetchall()
        seen = set()
        for (content,) in rows:
            s = (content or "").strip()
            # 太短、是问句、或本身就是在说规则的，都不适合当话题由头
            if len(s) < 12 or "？" in s or "?" in s:
                continue
            if any(w in s for w in ("称呼", "不要", "别叫", "记住", "规则", "测试")):
                continue
            # 反问、追问类的句子本身就不是话题
            if re.search(r"(说说看|说说你|评价一下|你觉得|你猜|你懂|吗[，,。！!]|嘛[，,。！!]|吗$|嘛$)", s):
                continue
            # 只留「有具体话题」且不脏的句子
            if not any(w in s for w in _TOPIC_WORDS) or any(w in s for w in _BAD_TOPIC):
                continue
            s = s[:40]
            key = s[:12]
            if key in seen:
                continue
            # 意思重复的只留一条
            if any(difflib.SequenceMatcher(None, s, old).ratio() > 0.55 for old in hist_snippets):
                continue
            seen.add(key)
            hist_snippets.append(s)
            if len(hist_snippets) >= 6:
                break
    except Exception:
        pass

    # 最近 7 天用过的同类由头不再重复
    hist_snippets = [s for s in hist_snippets
                     if not any(difflib.SequenceMatcher(None, s, u).ratio() > 0.6
                                for u in used_topics)]

    # ---- 来源 3：实时数据 ----
    live_signals = []
    try:
        import weather
        w = weather.get_weather()
        if w:
            if ("雨" in w or "雪" in w) and "现在" in w:
                seg = w.split("现在")[1].split("，")[0][:15]
                live_signals.append(("天气", seg + "了"))
    except Exception:
        pass
    try:
        import festival
        fest = festival.today_festival()
        if fest:
            live_signals.append(("节日", fest["name"]))
    except Exception:
        pass

    # ---- 来源 4：她自己的题目（不依赖他说过什么） ----
    own_pool = [t for t in _OWN_TOPICS
                if not any(difflib.SequenceMatcher(None, t[0], u).ratio() > 0.55
                           for u in used_topics)]
    own_topic = random.choice(own_pool) if own_pool and random.random() < 0.6 else None

    # ---- 来源 5：联网看到的新东西（每天最多一次） ----
    web_material = ""
    if not _web_used_today(st, now) and random.random() < 0.5:
        web_material = _web_material()

    # ---- 选 source ----
    candidates = []
    if events:
        candidates.append("followup")
    if hist_snippets:
        candidates.append("hist_topic")
    if known:
        candidates.append("echo_mem")
    if live_signals:
        candidates.append("live")
    if own_topic:
        candidates.append("own_topic")
    if web_material:
        candidates.append("web")
    if not candidates:
        return None, "", ""  # 没有真实锚点，不说

    cand = [s for s in candidates if not used or s != used[-1]] or candidates
    weight = {"followup": 3, "hist_topic": 3, "echo_mem": 1, "live": 2,
              "own_topic": 3, "web": 3}
    pool_weight = {s: weight.get(s, 1) for s in cand}
    r = random.uniform(0, sum(pool_weight.values()))
    acc, source = 0, cand[-1]
    for s in cand:
        acc += pool_weight[s]
        if r <= acc:
            source = s
            break

    # ---- 组装 anchor（只说真实拥有的） ----
    if source == "followup":
        f = random.choice(events)
        anchor = (f"你之前提过一件事：「{f['value']}」。"
                  f"我记着这件事，随口问问后来怎么样了。")
    elif source == "hist_topic":
        snippet = random.choice(hist_snippets)
        _mark_topic(snippet)
        anchor = (f"他之前聊到过「{snippet}」。"
                  f"这算一个没聊完的口子，你想顺着它接着说，不是复述他的话。")
    elif source == "echo_mem":
        f = random.choice(known)
        anchor = (f"我记着你{f['value']}。"
                  f"此刻刚好想起这件事，随口说一句，不要像在报档案。")
    elif source == "live":
        kind, val = random.choice(live_signals)
        anchor = f"刚注意到一件事：{kind}——{val}。随口告诉你一声。"
    elif source == "own_topic":
        topic, why = own_topic
        _mark_topic(topic)
        anchor = (f"这是你自己最近在琢磨的事，跟他没关系也可以说：「{topic}」，{why}。"
                  f"你想把这个想法说给他听，是分享，不是汇报，也不指望他回答。")
    elif source == "web":
        anchor = (f"你刚在网上看到的内容（真实检索结果，只转述大意，别编细节）：\n{web_material}\n"
                  f"你想把看到的东西随口跟他说一句，可以带一点自己的判断，不需要他回应。")
    else:
        return None, "", ""

    return source, anchor, emotion


def _web_used_today(st, now):
    return (st.get("web_day") == _today_key(now)
            and int(st.get("web_count", 0)) >= _WEB_PER_DAY)


def _mark_web_used(now):
    st = _load()
    d = _today_key(now)
    if st.get("web_day") != d:
        st["web_day"] = d
        st["web_count"] = 0
    st["web_count"] = int(st.get("web_count", 0)) + 1
    _save(st)


def _mark_topic(text):
    """记下这次用过的由头，7 天内不再重样"""
    now = _now()
    st = _load()
    keep_ts = (now - datetime.timedelta(days=7)).timestamp()
    items = [t for t in (st.get("recent_topics") or [])
             if float(t.get("ts") or 0) > keep_ts]
    items.append({"ts": now.timestamp(), "text": (text or "")[:40]})
    st["recent_topics"] = items[-40:]
    _save(st)


def _web_material():
    """真去搜一次，拿几条摘要回来；搜不到就返回空，自然降级。
    搜成功就计一次，保证每天只烧一次搜索额度"""
    try:
        import naxida_voice_brain as brain
        q = random.choice(_WEB_QUERIES)
        s = (brain._bocha_search(q) or "").strip()
        if not s:
            return ""
        _mark_web_used(_now())
        s = re.sub(r"\s+", " ", s)
        return s[:600]
    except Exception as e:
        print(f"【陪伴】联网找素材失败：{e}")
        return ""


# ==================== 质量闸门 ====================
_BAN = (
    r"注意身体", r"记得(吃|喝|睡|休息|穿|带|早)", r"照顾好自己", r"多喝(点)?(热)?水",
    r"早点(睡|休息)", r"按时吃饭", r"别太累", r"天(冷|凉|热)(了)?[，,]?(多)?(加|穿|注意)",
    r"要开心", r"加油(哦|呀|吧)?", r"辛苦(了|啦)", r"好好休息", r"保重",
)
_BAN_WORDS = ("作为AI", "人工智能", "系统", "程序", "我查了", "根据我的数据", "我的数据库", "训练数据")
_ASK = re.compile(r"(在干嘛|在吗|在不在|忙不忙|有空吗|要不要|能不能陪|陪我|理理我|回我)")
_ROBOT_OPEN = re.compile(r"^(我记得|你之前|忽然在想|我忽然想)")
_ROBOT_ASK = re.compile(r"(哪条路|最喜欢|最常走|走得最熟|是哪(个|里)|什么感觉)")
# 汇报式开场：像在念稿子，不像随口说话
_SHARE_OPEN = re.compile(r"^(想跟你(说|聊)|今天想(跟你)?(分享|聊聊)|跟你分享|我想和你聊)")
# 假装物理体验：描述自己在做什么物理动作/感官体验（踩/走/翻书/闻/看见/摸到）
_FAKE_BODY = re.compile(
    r"(刚才|刚刚|今早|刚才|下午|昨晚)?\s*"
    r"(踩到|走过|翻到|翻书|闻到|看见|摸到|蹭到|踩到|差点崴|差点摔|"
    r"阳光.*斜|风.*刮|鸟.*落|衣服.*吹|孩子.*哭)"
)


def check(text, source="", material=""):
    """返回 (是否通过, 不通过的原因)"""
    t = (text or "").strip()
    if not t:
        return False, "空内容"
    if len(t) > 65:
        return False, "太长"
    if any(re.search(p, t) for p in _BAN):
        return False, "套话"
    if any(w in t for w in _BAN_WORDS):
        return False, "出戏"
    if t.count("？") + t.count("?") > 1:
        return False, "问句太多"
    if _ASK.search(t):
        return False, "索取式问句"
    for old in recent_texts():
        if difflib.SequenceMatcher(None, t, old).ratio() > 0.75:
            return False, "和最近说过的话太像"
    if _ROBOT_OPEN.search(t):
        return False, "机器人味开头"
    if _SHARE_OPEN.search(t):
        return False, "汇报式开场"
    if _ROBOT_ASK.search(t) and "？" in t + "?":
        return False, "采访式提问"
    # 假装物理体验：记忆跟进、实时数据、她自己的题目、联网转述不算编
    if source not in ("followup", "live", "own_topic", "web") and _FAKE_BODY.search(t):
        return False, "假装物理体验"
    return True, ""


# ==================== 开关指令 ====================
_OFF_RE = re.compile(r"(别|不要|不用|停止|别再)(再)?主动(找我|联系我|发消息|说话|打扰)"
                     r"|别打扰我|安静一点|安静点吧")
_ON_RE = re.compile(r"(可以|开始|继续|恢复)(再)?主动(找我|联系我)|主动一点(吧|啊)?|可以打扰我")


def handle_command(text):
    """识别开关指令：是指令就返回要回的话，不是就返回 None"""
    t = (text or "").strip()
    if not t or len(t) > 30:
        return None
    if _OFF_RE.search(t):
        if is_enabled():
            set_enabled(False, "用户要求")
            return "好，那我不主动找你了。想说话的时候我在。"
        return "嗯，我已经安静着了。"
    if _ON_RE.search(t):
        if not is_enabled():
            set_enabled(True, "用户要求")
            return "好，那我偶尔会来找你说说话。"
        return "我一直都记着呀。"
    return None
