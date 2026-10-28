# -*- coding: utf-8 -*-
"""节日与时令：算出今天是什么日子，并给出一份「当日素材」。

设计要点：
- 用白名单，不用 cnlunar 的全部节日（它会把孟婆尊神诞、弥勒佛圣诞也算进来）
- 小年按南方口径覆盖成腊月二十四（cnlunar 默认腊月二十三）
- 习俗可被 config.json 的 REGION / CUSTOMS 覆盖，改一行就能换口径
- 每天只说一次，状态落盘，重启不重复
"""
import datetime
import json
import os

try:
    import cnlunar
except Exception:
    cnlunar = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE_PATH = os.path.join(BASE_DIR, "state", "greet_state.json")

# 传统大节 / 有分量的节气 = 3，普通公历节 = 2，普通节气 = 1
FESTIVALS = {
    "春节": (3, ["农历新年第一天", "辞旧岁、贴春联、年夜饭", "长辈会给红包", "拜年要说吉利话"]),
    "除夕": (3, ["一年的最后一晚", "守岁到零点", "年夜饭是一年里最丰盛的一顿", "把旧的一年送走"]),
    "元宵节": (3, ["正月十五，年就算过完了", "看灯、猜灯谜", "月亮是一年里第一次圆"]),
    "端午节": (3, ["五月初五", "吃粽子", "赛龙舟", "门口挂艾草"]),
    "七夕节": (3, ["七月初七", "牛郎织女一年见一次", "古时候女孩会在这天乞巧"]),
    "中秋节": (3, ["八月十五", "月亮最圆", "吃月饼", "团圆的日子"]),
    "重阳节": (3, ["九月初九", "登高", "赏菊", "敬老的日子"]),
    "腊八节": (3, ["腊月初八", "熬腊八粥", "过了腊八，年味就开始了"]),
    "小年": (3, ["祭灶、扫尘", "从这天开始算进入年关"]),
    "元旦": (2, ["公历新年的第一天", "跨年", "新的一年重新开始"]),
    "情人节": (2, ["二月十四", "到处都在卖花"]),
    "儿童节": (2, ["六月一日", "小孩子放假玩耍的日子"]),
    "教师节": (2, ["九月十日"]),
    "国庆节": (2, ["十月一日", "有长假", "到处都挂国旗"]),
    "平安夜": (2, ["十二月二十四", "晚上"]),
    "圣诞节": (2, ["十二月二十五"]),
    "冬至": (3, ["一年里白天最短、夜最长的一天", "从这天开始数九", "阴极阳生，光在一点点回来"]),
    "清明": (3, ["天气转暖、雨水多", "踏青", "扫墓祭祖"]),
}

SOLAR_TERMS = {
    "立春": ["春天的第一个节气", "但真正回暖还要等一阵"],
    "雨水": ["雨水开始多起来"],
    "惊蛰": ["春雷响，虫子都醒了"],
    "春分": ["昼夜一样长"],
    "谷雨": ["雨水养谷", "春天最后一个节气"],
    "立夏": ["夏天开始", "白天变长"],
    "小满": ["麦子开始灌浆", "但还没熟"],
    "芒种": ["最忙的农时", "又收又种"],
    "夏至": ["一年里白天最长的一天"],
    "小暑": ["开始热，但还不是最热"],
    "大暑": ["一年里最热的一段"],
    "立秋": ["秋天名义上开始了", "但南方还有秋老虎，热还要持续一阵"],
    "处暑": ["暑气到此为止", "早晚开始凉"],
    "白露": ["早上会有露水", "昼夜温差变大"],
    "秋分": ["昼夜又一样长"],
    "寒露": ["露水变凉", "该添衣了"],
    "霜降": ["秋天最后一个节气", "开始下霜"],
    "立冬": ["冬天开始", "该进补了"],
    "小雪": ["开始飘雪，但雪还小"],
    "大雪": ["雪开始积得住"],
    "冬至": ["一年里白天最短、夜最长的一天", "从这天开始数九", "阴极阳生，光在一点点回来"],
    "小寒": ["进入一年里最冷的时段"],
    "大寒": ["最后一个节气", "冷到极点之后就要回暖"],
}

_DEFAULT_CUSTOMS = {
    "冬至": "饺子",
    "元宵": "汤圆",
    "小年": "腊月二十四",
}


def _load_cfg():
    try:
        with open(os.path.join(BASE_DIR, "config.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def customs():
    """习俗配置：个人设置覆盖地域默认"""
    cfg = _load_cfg()
    merged = dict(_DEFAULT_CUSTOMS)
    for k, v in (cfg.get("CUSTOMS") or {}).items():
        merged[k] = v
    return merged


def _is_south():
    return (_load_cfg().get("REGION") or "south") != "north"


def _festival_facts(name):
    """取节日素材，并按习俗补充具体食物/日期"""
    c = customs()
    facts = list(FESTIVALS.get(name, (0, []))[1])
    if name == "冬至":
        food = c.get("冬至") or "热乎的东西"
        facts.append(f"这边今天吃{food}")
    elif name == "元宵节":
        food = c.get("元宵") or "汤圆"
        facts.append(f"吃{food}")
    elif name == "小年":
        date = c.get("小年") or ("腊月二十四" if _is_south() else "腊月二十三")
        facts.append(f"这边的小年是{date}")
    elif name == "清明":
        if _is_south():
            facts.append("南方这时已经回暖得比较明显")
    return facts


def _lunar_of(when):
    if cnlunar is None:
        return None
    try:
        return cnlunar.Lunar(when, godType="8char")
    except Exception:
        return None


def _is_chuxi(lunar, when):
    """除夕：腊月最后一天（cnlunar 没有直接字段，用第二天是不是正月初一判断）"""
    if not lunar or lunar.lunarMonth != 12:
        return False
    nxt = _lunar_of(when + datetime.timedelta(days=1))
    return bool(nxt and nxt.lunarMonth == 1 and nxt.lunarDay == 1)


def today_festival(when=None):
    """返回今天最该说的那个节日/节气；没有则返回 None"""
    when = when or datetime.datetime.now()
    lunar = _lunar_of(when)
    if lunar is None:
        return None
    found = []  # (优先级, 名称)

    legal = lunar.get_legalHolidays() or []
    other = lunar.get_otherHolidays() or []
    lunar_f = lunar.get_otherLunarHolidays() or []
    term = lunar.todaySolarTerms or "无"

    for name in ("春节", "元宵节", "端午节", "中秋节", "国庆节", "元旦", "清明节"):
        if name in legal or name in other:
            key = "清明" if name == "清明节" else name
            pri = 3 if name in ("春节", "元宵节", "端午节", "中秋节", "清明节") else 2
            found.append((key, pri))

    if _is_chuxi(lunar, when):
        found.append(("除夕", 3))
    for raw in lunar_f:
        if raw.startswith("七夕"):
            found.append(("七夕节", 3))
        elif raw.startswith("重阳节"):
            found.append(("重阳节", 3))
        elif raw.startswith("腊八节"):
            found.append(("腊八节", 3))
        elif raw.startswith("元宵节"):
            found.append(("元宵节", 3))

    # 小年：按配置口径判断，并屏蔽 cnlunar 自带的北方小年
    xiao_nian = customs().get("小年") or ("腊月二十四" if _is_south() else "腊月二十三")
    day = 24 if "二十四" in xiao_nian else 23
    if lunar.lunarMonth == 12 and lunar.lunarDay == day:
        found.append(("小年", 3))

    # 公历固定节日
    fixed = {(1, 1): "元旦", (2, 14): "情人节", (6, 1): "儿童节",
             (9, 10): "教师节", (10, 1): "国庆节", (12, 24): "平安夜", (12, 25): "圣诞节"}
    name = fixed.get((when.month, when.day))
    if name:
        found.append((name, 2))

    if term in SOLAR_TERMS:
        found.append((term, 3 if term in ("冬至", "清明") else 1))

    if not found:
        return None
    # 同一天撞多个：优先级高的优先，同级取先出现的
    found.sort(key=lambda x: -x[1])
    key = found[0][0]
    if key in SOLAR_TERMS and key not in FESTIVALS:
        facts = list(SOLAR_TERMS[key])
    else:
        facts = _festival_facts(key)
    return {
        "key": key,
        "name": key,
        "priority": found[0][1],
        "facts": facts,
        "date": when.strftime("%Y-%m-%d"),
    }


def _load_state():
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def pending_greeting(when=None):
    """今天还没问候过、且今天确实是个日子，就返回节日信息；否则 None"""
    info = today_festival(when)
    if not info:
        return None
    st = _load_state()
    if st.get("date") == info["date"] and st.get("keys"):
        return None  # 今天已经说过一次
    return info


def mark_greeted(info):
    """记录今天已问候，避免当天第二次、第三次重复说"""
    st = _load_state()
    if st.get("date") != info["date"]:
        st = {"date": info["date"], "keys": []}
    st.setdefault("keys", []).append(info["key"])
    try:
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
    except Exception as e:
        print(f"【节日】状态写入失败：{e}")


if __name__ == "__main__":
    for d in [(2026, 12, 22), (2027, 2, 5), (2027, 2, 6), (2026, 9, 25),
              (2027, 1, 31), (2027, 1, 30), (2027, 8, 8), (2027, 4, 5), (2026, 10, 22)]:
        w = datetime.datetime(*d)
        info = today_festival(w)
        print(w.strftime("%Y-%m-%d"), "→", info and (info["key"], info["priority"], info["facts"]))
