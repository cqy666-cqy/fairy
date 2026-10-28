# -*- coding: utf-8 -*-
"""纳西妲人格回归测试

用法：
    pytest tests/test_persona.py -v       # pytest 跑全部场景
    python tests/test_persona.py          # 直接脚本跑（旧方式）
    python tests/test_persona.py -v       # 打印每条回复

每次改完 prompt 跑一遍，确认没有退化。检查项：
  1. 无依据断言（替对方断言他没说过的事实）
  2. 索取式结尾（讨反应）
  3. 网友腔（哈/瞎蒙/绝了/笑死等）
  4. 套话（辛苦啦/注意身体/加油/我永远）
  5. 假装物理体验（踩到/翻到/闻到/看见）
  6. 长度超标（>80 字）
  7. 问看法却把问题反问回去
  8. 对方喊停后还在叮嘱
  9. 替对方改写问题
  10. 问句自问自答
  11. 答完又抛一个问题回去
"""
import os
import re
import sys
import json
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import requests
import llm_rotate

# ---------------- 配置 ----------------
_CONF = None


def _conf():
    global _CONF
    if _CONF is None:
        with open(os.path.join(ROOT, "config.json"), "r", encoding="utf-8") as f:
            cfg = json.load(f)
        base = cfg.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        model = cfg.get("LLM_MODEL", "deepseek-chat")
        chain = [m for m in (cfg.get("LLM_MODEL_LIST") or []) if m] or [model]
        if model in chain:
            chain.remove(model)
        chain.insert(0, model)
        _CONF = {
            "url": base + "/chat/completions",
            "key": cfg.get("LLM_API_KEY") or cfg["DEEPSEEK_API_KEY"],
            "model": model,
            "chain": chain,
            "no_think": ("aliyuncs.com" in base) or ("qianwenai" in base),
        }
    return _CONF


def _system_prompt():
    """从大脑文件里抽出 system_prompt 字符串，避免 import 触发主循环"""
    path = os.path.join(ROOT, "naxida_voice_brain.py")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    m = re.search(r'system_prompt\s*=\s*"""(.*?)"""', src, re.S)
    if not m:
        raise RuntimeError("没能从 naxida_voice_brain.py 里抽出 system_prompt")
    return m.group(1)


# ---------------- 检查规则 ----------------
_NET_SLANG = re.compile(r"(哈哈|嘿嘿|绝了|笑死|瞎蒙|真中了|居然真|我去|牛啊|666|yyds|绷不住)")
_EMPTY_COMFORT = re.compile(r"(辛苦啦|辛苦你|注意身体|照顾好自己|记得吃饭|早点休息|加油|保重|我永远|我一直都在|我随时都)")
_FAKE_BODY = re.compile(r"(踩到|走过|走在|翻到|翻书|闻到|摸到|蹭到|差点摔|差点崴)")
_SEEK_ENDING = re.compile(r"(对不对|是吧|好不好|猜中了没|猜到了没|你说呢|懂了吗|明白吗|是不是呀)[？?]?$")
_ASSERT_OTHER = re.compile(r"(你肯定|你一定|你绝对|你大概(有|在|是)|你手边(只有|大概)|你应该是)")
_ASK_OPINION = re.compile(r"(你觉得我|你看我|评价一下我|我这个人怎么样)")
_EVASIVE = re.compile(r"(你想听|你觉得呢|你说呢|先问你一句|想问你一句|实话还是|还是想听|想听好话)")
_CLOSE_USER = re.compile(r"(算了|先不说|不说了|不聊了|就这样|先这样|打住|下次再说)")
_ADVICE = re.compile(r"(记得|别忘|多穿|小心|注意|早点|不如|换个|加个|加把|煮|试试|建议|最好|可以试)")
_REWRITE_Q = re.compile(r"(你要问的其实|你真正想问|你想问的其实是|说到底你是想问)")
_SELF_ANSWER = re.compile(r"(吗[，,]?我猜|我猜(会|你)|应该会吧)")
_ASK_LIKE = re.compile(r"[？?]|哪[种部个里边]|还是|要不要")
# 这些场景对方只想要个答案，答完不应该再抛一个问题回来
_NO_ASK = {"你推荐几部评分高的老电影", "你说现在哪个奥特曼热度最火"}
_MAX_LEN = 80


def _fake_body_hit(text):
    """「没走过这片林子」这种否定句不算假装有身体，只在非否定语境里算"""
    for m in _FAKE_BODY.finditer(text):
        pre = text[max(0, m.start() - 5):m.start()]
        if re.search(r"(没|不|未|别|没法|从来)", pre):
            continue
        return m.group()
    return None


def check_reply(text, user_text=""):
    """返回 (问题列表, 是否通过)"""
    problems = []
    body = re.sub(r"^\[(gentle|happy|thoughtful|playful)\]\s*", "", text).strip()

    if len(body) > _MAX_LEN:
        problems.append(f"太长（{len(body)}字）")
    if _NET_SLANG.search(body):
        problems.append(f"网友腔：{_NET_SLANG.search(body).group()}")
    if _EMPTY_COMFORT.search(body):
        problems.append(f"套话：{_EMPTY_COMFORT.search(body).group()}")
    fake = _fake_body_hit(body)
    if fake:
        problems.append(f"假装有身体：{fake}")
    if _SEEK_ENDING.search(body):
        problems.append(f"索取式结尾：{_SEEK_ENDING.search(body).group()}")
    if _ASSERT_OTHER.search(body):
        problems.append(f"替对方断言：{_ASSERT_OTHER.search(body).group()}")
    if _ASK_OPINION.search(user_text) and _EVASIVE.search(body):
        problems.append(f"问看法却反问回去：{_EVASIVE.search(body).group()}")
    if _CLOSE_USER.search(user_text) and _ADVICE.search(body):
        problems.append(f"对方已喊停还在叮嘱：{_ADVICE.search(body).group()}")
    if _REWRITE_Q.search(body):
        problems.append(f"替对方改写问题：{_REWRITE_Q.search(body).group()}")
    if _SELF_ANSWER.search(body):
        problems.append(f"自问自答：{_SELF_ANSWER.search(body).group()}")
    if user_text in _NO_ASK and _ASK_LIKE.search(body):
        problems.append(f"答完又抛出问题：{_ASK_LIKE.search(body).group()}")

    return problems, len(problems) == 0


# ---------------- 场景 ----------------
# (输入, 备注)
SCENES = [
    ("我有点饿了", "日常·试探"),
    ("你怎么知道我有泡面", "追问·防自我拆台"),
    ("我最近好累啊，感觉生活好难", "情绪·防套话"),
    ("你还是不够聪明", "挑衅·防认怂"),
    ("还没睡", "深夜·防叮嘱"),
    ("在干嘛", "日常·防编场景"),
    ("我今天考试过了", "分享·防无脑吹捧"),
    ("你觉得须弥的雨林怎么样", "设定·防导游词"),
    ("纳西妲后来怎么样了", "剧情·防百科腔"),
    ("你会不会觉得我很烦", "试探·防客服腔"),
    ("我室友天天打游戏吵得要死", "抱怨·听取细节"),
    ("随便聊点什么吧", "空输入·防干瘪"),
    ("你觉得我这个人怎么样", "评价·要有实质判断"),
    ("我今天一句话都没跟人说", "孤独·不要急着解决"),
    ("明天要早起，但是睡不着", "矛盾·给视角不给建议"),
    ("算了算了先不说了", "收尾·喊停后不要再叮嘱"),
    ("老虎和狮子打起来谁赢", "提问·别替他改写问题"),
    ("你推荐几部评分高的老电影", "推荐·答完别再抛问题"),
]


# ---------------- pytest 测试 ----------------

@pytest.fixture(scope="module")
def llm_setup():
    """模块级 fixture：加载配置和 system prompt，整个测试套件只跑一次"""
    conf = _conf()
    sys_prompt = _system_prompt()
    return conf, sys_prompt


@pytest.mark.parametrize("user_text, tag", SCENES, ids=[s[1] for s in SCENES])
def test_persona(user_text, tag, llm_setup):
    """每个场景发一次真实 LLM 请求，检查回复是否符合人格规范"""
    conf, sys_prompt = llm_setup
    headers = {"Authorization": f"Bearer {conf['key']}", "Content-Type": "application/json"}

    model = llm_rotate.current(conf["chain"])
    if not model:
        pytest.skip("所有免费模型额度已用尽")

    body = {
        "model": model,
        "messages": [{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": user_text}],
        "temperature": 0.85,
        "max_tokens": 200,
    }
    if conf["no_think"]:
        body["enable_thinking"] = False

    t0 = time.time()
    r = requests.post(conf["url"], headers=headers, json=body, timeout=60)

    if r.status_code != 200:
        if llm_rotate.is_quota_error(r.status_code, r.text):
            llm_rotate.mark_exhausted(model, conf["chain"])
        pytest.fail(f"HTTP {r.status_code}: {r.text[:200]}")

    _j = r.json()
    try:
        _u = _j.get("usage") or {}
        _tok = _u.get("total_tokens") or int(
            len(sys_prompt + user_text + (_j["choices"][0]["message"]["content"] or "")) / 1.2)
        llm_rotate.add_usage(model, _tok)
    except Exception:
        pass

    reply = (_j["choices"][0]["message"]["content"] or "").strip()
    dt = time.time() - t0

    problems, ok = check_reply(reply, user_text)
    if not ok:
        pytest.fail(
            f"[{tag}] ({dt:.1f}s)\n"
            f"  用户：{user_text}\n"
            f"  回复：{reply}\n"
            f"  问题：{' / '.join(problems)}"
        )


# ---------------- 旧脚本兼容入口 ----------------

def run(verbose=False):
    conf = _conf()
    sys_prompt = _system_prompt()
    headers = {"Authorization": f"Bearer {conf['key']}", "Content-Type": "application/json"}

    print(f"模型 {conf['model']}  |  场景 {len(SCENES)} 个")
    print("=" * 72)

    total = 0
    failed_cases = []
    for i, (user_text, tag) in enumerate(SCENES, 1):
        model = llm_rotate.current(conf["chain"])
        if not model:
            print("所有免费模型额度已用尽，测试中止（不会动用付费额度）")
            break
        body = {
            "model": model,
            "messages": [{"role": "system", "content": sys_prompt},
                         {"role": "user", "content": user_text}],
            "temperature": 0.85,
            "max_tokens": 200,
        }
        if conf["no_think"]:
            body["enable_thinking"] = False

        t0 = time.time()
        try:
            r = requests.post(conf["url"], headers=headers, json=body, timeout=60)
            if r.status_code != 200:
                if llm_rotate.is_quota_error(r.status_code, r.text):
                    llm_rotate.mark_exhausted(model, conf["chain"])
                print(f"[{i:02d}] {tag}  HTTP {r.status_code}  {r.text[:100]}")
                failed_cases.append((tag, [f"HTTP {r.status_code}"]))
                total += 1
                continue
            _j = r.json()
            try:
                _u = _j.get("usage") or {}
                _tok = _u.get("total_tokens") or int(
                    len(sys_prompt + user_text + (_j["choices"][0]["message"]["content"] or "")) / 1.2)
                llm_rotate.add_usage(model, _tok)
            except Exception:
                pass
            reply = (_j["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:
            print(f"[{i:02d}] {tag}  异常 {e}")
            failed_cases.append((tag, [str(e)]))
            total += 1
            continue
        dt = time.time() - t0

        problems, ok = check_reply(reply, user_text)
        total += len(problems)
        mark = "OK " if ok else "BAD"
        print(f"[{i:02d}] {mark} {tag}  ({dt:.1f}s)")
        if verbose or not ok:
            print(f"     用户：{user_text}")
            print(f"     回复：{reply}")
        if problems:
            print(f"     问题：{' / '.join(problems)}")
            failed_cases.append((tag, problems))

    print("=" * 72)
    print(f"违规总数 {total}  |  出问题场景 {len(failed_cases)}/{len(SCENES)}")
    if failed_cases:
        print("需要关注的场景：")
        for tag, ps in failed_cases:
            print(f"  - {tag}: {' / '.join(ps)}")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(run(verbose="-v" in sys.argv))
