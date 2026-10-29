"""纳西妲 QQ 机器人启动入口

前置：先启动 NapCatQQ，并在其配置里打开「WebSocket 服务器」（默认 ws://127.0.0.1:3001）
配置：config.json 里的 NAPCAT_WS_URL / NAPCAT_TOKEN / OWNER_QQ / QQ_GROUP_AT_ONLY
"""
import importlib.util
import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import unittest.mock

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qq_bot import OneBotClient, at_me, extract_text, split_text

# 节日/时令（cnlunar，缺依赖时自动跳过问候）
try:
    import festival
except Exception as _e:
    print(f"[QQ] 节日模块加载失败：{_e}")
    festival = None

# 主动陪伴：她自己判断什么时候开口
try:
    import company
except Exception as _e:
    print(f"[QQ] 主动陪伴模块加载失败：{_e}")
    company = None

# ---------- 单实例锁：两个 bot 同时连 NapCat，每条消息会被回复两遍 ----------
import socket

_instance_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    _instance_lock.bind(("127.0.0.1", 39271))
    _instance_lock.listen(1)
except OSError:
    print("[QQ] 已经有一个纳西妲在运行了，请先关掉另一个窗口再启动")
    sys.exit(0)

# ---------- 读取配置 ----------
_cfg_path = os.path.join(BASE_DIR, "config.json")
_cfg = {}
if os.path.exists(_cfg_path):
    with open(_cfg_path, "r", encoding="utf-8") as _f:
        _cfg = json.load(_f)

WS_URL = _cfg.get("NAPCAT_WS_URL", "ws://127.0.0.1:3001")
WS_TOKEN = _cfg.get("NAPCAT_TOKEN", "")
OWNER_QQ = str(_cfg.get("OWNER_QQ", "") or "")
GROUP_AT_ONLY = _cfg.get("QQ_GROUP_AT_ONLY", True)

# ---------- NapCat watchdog ----------
# 从 WS_URL 抠出 host/port，硬编码 NapCat 路径
_NAPCAT_HOST = re.match(r"wss?://([^:/]+):(\d+)", WS_URL)
NAPCAT_HOST = _NAPCAT_HOST.group(1) if _NAPCAT_HOST else "127.0.0.1"
NAPCAT_PORT = int(_NAPCAT_HOST.group(2)) if _NAPCAT_HOST else 3001
_paths = _cfg.get("PATHS", {})
NAPCAT_EXE = _paths.get("NAPCAT_EXE", _cfg.get("NAPCAT_EXE", r"E:\fariy\chatqq\NapCat.Shell.Windows.OneKey\NapCat.52230.Shell\NapCatWinBootMain.exe"))
NAPCAT_DIR = os.path.dirname(NAPCAT_EXE) if os.path.exists(NAPCAT_EXE) else os.path.dirname(NAPCAT_EXE)

# ---------- 用户反馈存储（/赞 /踩） ----------
_FEEDBACK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "feedback.json")
_last_reply = {}  # user_id -> (question, reply)

def _save_feedback(uid, label, question, reply):
    os.makedirs(os.path.dirname(_FEEDBACK_PATH), exist_ok=True)
    data = []
    if os.path.exists(_FEEDBACK_PATH):
        try:
            with open(_FEEDBACK_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data.append({"uid": uid, "label": label, "q": question[:100], "a": reply[:200], "ts": time.strftime("%Y-%m-%d %H:%M")})
    with open(_FEEDBACK_PATH, "w", encoding="utf-8") as f:
        json.dump(data[-200:], f, ensure_ascii=False, indent=2)

# 要黑洞的 QQ 静默热更新 host（改 hosts 防 QQ 后台 patch 二进制，打断 NapCat hook）
_QQ_PATCH_HOSTS = (
    "dlied2me.qq.com", "patch.qq.com", "update.qq.com",
    "dlied2.qq.com", "dlied3.qq.com", "qd.qq.com",
    "update.qq.com.edgesuite.net",
)
_HOSTS_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "drivers", "etc", "hosts")


# ---------- 点歌功能 ----------
MUSIC_DIR = os.path.join(BASE_DIR, "music")

def scan_songs():
    """扫描 music 目录下的纳西妲成品歌曲"""
    songs = {}
    try:
        for f in os.listdir(MUSIC_DIR):
            if not f.endswith(".wav"):
                continue
            m = re.match(r"^(.+?)_副歌_纳西妲版\.wav$", f)
            if m:
                name = m.group(1)
                songs.setdefault(name, {})["chorus"] = f
                continue
            m = re.match(r"^(.+?)_纳西妲版\.wav$", f)
            if m:
                name = m.group(1)
                songs.setdefault(name, {})["full"] = f
    except OSError:
        pass
    return songs


def is_song_request(text):
    """判断这条消息是不是真的要点歌，还是只是提到'唱'这个字"""
    # 排除：评价类（"你唱的还不错"、"唱得好"、"唱得难听"）
    if re.search(r"(你唱的?还?不错|唱得?好|唱得?不错|唱得?难听|你唱得?真|唱歌真好听|唱得?真棒|唱得?好棒)", text):
        return False
    # 排除：问问题类（"你会唱歌吗"、"你上次唱的那首是什么"）
    if re.search(r"(你会唱|你上次唱|你唱的?是|你唱的?那|唱歌吗|会唱吗|怎么唱的)", text):
        return False
    # 排除：主语是"你"的（"你唱一首" → 这个是点歌，但"你唱的..." → 不是）
    # 注意："你唱XX" 是点歌，但"你唱的XX" 不是
    if re.search(r"你唱的?", text) and not re.search(r"你唱(.+)", text):
        return False
    return True


def match_song(text):
    """从用户文本中匹配点歌意图，返回 (歌名, 文件名) 或 None"""
    # 先判断是不是真的点歌请求
    if not is_song_request(text):
        return None
    
    songs = scan_songs()
    if not songs:
        return None
    sing_kw = r"(唱|来一首|来首|放一首|点一首|点歌|唱一下|唱首|来一段|唱一段)"
    chorus_kw = r"(副歌|一段|片段|高潮|最火|那段|部分)"
    full_kw = r"(完整|全首|整首|全部|从头到尾)"
    names = sorted(songs.keys(), key=len, reverse=True)
    for name in names:
        if name not in text:
            continue
        has_sing = bool(re.search(sing_kw, text))
        is_short = len(text.strip()) <= len(name) + 5
        if not (has_sing or is_short):
            continue
        if re.search(full_kw, text) and "full" in songs[name]:
            return name, songs[name]["full"]
        elif re.search(chorus_kw, text) and "chorus" in songs[name]:
            return name, songs[name]["chorus"]
        elif "chorus" in songs[name]:
            return name, songs[name]["chorus"]
        elif "full" in songs[name]:
            return name, songs[name]["full"]
    return None



def _is_qq_update_blocked():
    """hosts 是否已屏蔽 QQ 静默更新（启动时跑一次，没 block 就让用户手动跑管理员命令）"""
    try:
        if not os.path.exists(_HOSTS_PATH):
            return False
        with open(_HOSTS_PATH, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return any(h in content for h in _QQ_PATCH_HOSTS)
    except Exception:
        return False


def _print_hosts_patch_cmd():
    """给用户打印一条管理员 PowerShell 命令，跑一次永久生效"""
    hosts_block = "# QQ 静默热更新屏蔽（NapCat hook 防断裂）\n" + "\n".join(
        f"127.0.0.1 {h}" for h in _QQ_PATCH_HOSTS
    )
    cmd = (
        f'Add-Content -Path "{_HOSTS_PATH}" -Value @"\n{hosts_block}\n"@ -Encoding ASCII;'
        f' ipconfig /flushdns | Out-Null'
    )
    print()
    print("=" * 60)
    print("[QQ] ⚠ hosts 未屏蔽 QQ 静默热更新——这是 NapCat 自动崩的主要原因！")
    print("[QQ]   请以【管理员身份】打开 PowerShell，执行以下命令（跑一次即可永久生效）：")
    print()
    print("    " + cmd)
    print()
    print("=" * 60)


def _napcat_up():
    """3001 端口有没有监听（socket 探测，<1s）"""
    import socket as _sok
    s = _sok.socket()
    s.settimeout(1.5)
    try:
        s.connect((NAPCAT_HOST, NAPCAT_PORT))
        return True
    except OSError:
        return False
    finally:
        try: s.close()
        except Exception: pass


def _napcat_live():
    """尝试用 WebSocket 握手探测是否真在线（NapCat 3001 是 WS-only，HTTP 会返回 Upgrade Required）
    如果握手也不想做，socket 端口通就够了——登录态失效交给 bot 的 WS 重连循环处理"""
    import socket as _sok
    try:
        s = _sok.socket()
        s.settimeout(2)
        s.connect((NAPCAT_HOST, NAPCAT_PORT))
        # 发一个最小的 WebSocket Upgrade 请求，看 NapCat 是否响应 101 Switching Protocols
        # 不发也没关系——socket 通 = NapCat 活着
        s.sendall(
            b"GET / HTTP/1.1\r\n"
            b"Host: " + NAPCAT_HOST.encode() + b"\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        resp = s.recv(512)
        s.close()
        return b"101" in resp or b"200" in resp
    except Exception:
        return False


def _find_napcat_pids():
    """找出 NapCatWinBootMain.exe 及其拉起的 QQ.exe PID，精确杀"""
    import subprocess as _sp
    pids = {"napcat": [], "qq": []}
    try:
        # NapCat 自己的 PID
        r = _sp.run(["tasklist", "/FI", "IMAGENAME eq NapCatWinBootMain.exe", "/FO", "CSV", "/NH"],
                    capture_output=True, timeout=5, text=True)
        for line in (r.stdout or "").strip().splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2:
                pids["napcat"].append(int(parts[1]))
        # QQ 的 PID
        r = _sp.run(["tasklist", "/FI", "IMAGENAME eq QQ.exe", "/FO", "CSV", "/NH"],
                    capture_output=True, timeout=5, text=True)
        for line in (r.stdout or "").strip().splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2:
                pids["qq"].append(int(parts[1]))
    except Exception:
        pass
    return pids


def _restart_napcat():
    """杀掉残留 NapCat/QQ 进程，重新拉起 NapCatWinBootMain.exe，等待端口就绪"""
    try:
        import subprocess as _sp
        pids = _find_napcat_pids()
        # 先杀 NapCat，再杀 QQ（QQ 可能是 NapCat 的子进程）
        for pid in pids["napcat"] + pids["qq"]:
            try:
                _sp.run(["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True, timeout=5)
            except Exception:
                pass
        # 确认都死了
        for _ in range(10):
            alive = _find_napcat_pids()
            if not alive["napcat"] and not alive["qq"]:
                break
            time.sleep(0.5)
        time.sleep(2)
        # 重拉起
        if not os.path.exists(NAPCAT_EXE):
            print(f"[QQ] watchdog: NapCat 可执行文件不存在，跳过重启 → {NAPCAT_EXE}")
            return False
        _sp.Popen([NAPCAT_EXE], cwd=NAPCAT_DIR, shell=False)
        print("[QQ] watchdog: 已重启 NapCat，等待端口就绪...")
        # 等待 3001 端口起来（最多 60 秒）
        deadline = time.time() + 60
        while time.time() < deadline:
            if _napcat_up():
                # 再等 5 秒让登录态恢复
                time.sleep(5)
                live = _napcat_live()
                print(f"[QQ] watchdog: NapCat 端口已就绪，login_info {'正常' if live else '待恢复'}")
                return True
            time.sleep(2)
        print("[QQ] watchdog: NapCat 重启后 60 秒端口仍不通")
        return False
    except Exception as e:
        print(f"[QQ] watchdog: 重启失败 → {e}")
        return False


def _watchdog_loop():
    """每 2 分钟探一次 3001；不通就重启 NapCat。不自动扫码登录（登录态失效需要人）"""
    _last_restart = 0.0
    _cooldown = 120  # 120 秒内不重复重启
    # 冷启动等待：NapCat 可能还没起来，等 30 秒再开始探活
    print("[QQ] watchdog: 冷启动，等 NapCat 自己先起来（30s）...")
    time.sleep(30)
    # 启动时主动探活一次，让用户知道当前状态
    if _napcat_up():
        if _napcat_live():
            print("[QQ] watchdog: NapCat 已在线 ✓")
        else:
            print("[QQ] watchdog: NapCat 端口通但登录态异常，可能需要手动扫码")
    else:
        print("[QQ] watchdog: NapCat 端口不通，尝试首次启动...")
        _restart_napcat()
        _last_restart = time.time()
    while True:
        try:
            time.sleep(_cooldown)
            up = _napcat_up()
            if not up:
                # 端口完全不通 → 直接重启
                now = time.time()
                if now - _last_restart < _cooldown:
                    print("[QQ] watchdog: 刚重启过，跳过")
                    continue
                print("[QQ] watchdog: 3001 端口不通，重启 NapCat")
                if _restart_napcat():
                    _last_restart = now
                    print("[QQ] watchdog: NapCat 已重启，登录态若过期请手动扫码")
                continue
            # 端口通但假在线（登录态失效）→ 给用户发一条提醒到 console
            if not _napcat_live():
                print("[QQ] watchdog: 3001 通但 get_login_info 失败 → 登录态可能失效，请手动扫码")
        except Exception as e:
            print(f"[QQ] watchdog: 探测异常 → {e}")

# 完全不回复的名单：消息直接丢弃，一个字都不发
IGNORE_LIST = {str(x) for x in (_cfg.get("QQ_IGNORE_LIST") or [])}
# 受限名单：不进大模型，只从固定短语里挑一句（约三分之一带钩子），既有脾气又不给任何实质内容
RESTRICTED_LIST = {str(x) for x in (_cfg.get("QQ_RESTRICTED_LIST") or [])}
# 放行名单：能真正聊天，但语气冷淡、每天有固定 token 预算，用完当天退回固定短语
GUARDED_LIST = {str(x) for x in (_cfg.get("QQ_GUARDED_LIST") or [])}
# 所有非主人用户共用的每日 token 上限（主人不受限）
DAILY_PER_USER = int(_cfg.get("QQ_DAILY_TOKENS_PER_USER") or 30000)
_BUDGET_FILE = os.path.join(BASE_DIR, "state", "qq_budget.json")
_budget_lock = threading.Lock()
_RESTRICT_COLD = [
    "这个我不接。",
    "我现在不太想聊这个。",
    "换个话题吧，不然我就不说了。",
    "嗯，听到了。",
]
_RESTRICT_HOOK = [
    "你猜我会不会。",
    "下次吧。",
    "想知道啊。",
    "这个嘛，看你表现。",
]
_RESTRICT_LIMIT = 3      # 10 分钟内最多回几次
_RESTRICT_WINDOW = 600
_restrict_state = {}     # uid -> {"times": [...], "last": "上一句"}
_restrict_lock = threading.Lock()


def _restricted_reply(uid):
    """受限用户的固定回应：钩子和冷淡句随机交替，避免被摸出规律；超频后直接不吭声"""
    now = time.time()
    with _restrict_lock:
        st = _restrict_state.setdefault(uid, {"times": [], "last": ""})
        st["times"] = [t for t in st["times"] if now - t < _RESTRICT_WINDOW]
        if len(st["times"]) >= _RESTRICT_LIMIT:
            return ""
        st["times"].append(now)
        pool = [x for x in (_RESTRICT_HOOK if random.random() < 0.34 else _RESTRICT_COLD)
                if x != st["last"]]
        line = random.choice(pool)
        st["last"] = line
    return line


def _today():
    return time.strftime("%Y-%m-%d")


def _budget_load():
    """按人记账的当日 token 用量，落盘保存，重启不重置"""
    try:
        with open(_BUDGET_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") != _today():
            return {"date": _today(), "used": {}}
        data.setdefault("used", {})
        return data
    except Exception:
        return {"date": _today(), "used": {}}


def _budget_save(data):
    try:
        os.makedirs(os.path.dirname(_BUDGET_FILE), exist_ok=True)
        tmp = _BUDGET_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _BUDGET_FILE)
    except Exception as e:
        print(f"[QQ] 额度记账写入失败：{e}")


def _budget_left(uid):
    with _budget_lock:
        data = _budget_load()
        return DAILY_PER_USER - int(data["used"].get(uid, 0))


def _budget_add(uid, tokens):
    with _budget_lock:
        data = _budget_load()
        data["used"][uid] = int(data["used"].get(uid, 0)) + max(0, int(tokens))
        _budget_save(data)
        return max(0, DAILY_PER_USER - data["used"][uid])

# ---------- 加载大脑（brain 模块自带终端主循环，用 mock input 绕过） ----------
print("[QQ] 正在加载纳西妲大脑...")
_spec = importlib.util.spec_from_file_location(
    "brain", os.path.join(BASE_DIR, "naxida_voice_brain.py")
)
brain = importlib.util.module_from_spec(_spec)
with unittest.mock.patch("builtins.input", side_effect=KeyboardInterrupt):
    try:
        _spec.loader.exec_module(brain)
    except KeyboardInterrupt:
        pass
print("[QQ] 大脑加载完成")

# ---------- 单线程队列：避免多人同时聊天时上下文串台 ----------
_task_queue = queue.Queue()


def _worker():
    while True:
        event = _task_queue.get()
        try:
            _handle(event)
        except Exception as e:
            print(f"[QQ] 回复失败：{e}")
            import traceback
            traceback.print_exc()
        finally:
            _task_queue.task_done()


def _reply_for(event, text, voice_mode=False, cold=False):
    """生成回复文本：只有 OWNER_QQ 能用主人模式
    voice_mode=True 时通知大脑这一条会用声音说出来，避免它生成「只能打字」这类话"""
    user_id = str(event.get("user_id", ""))
    session_id = f"qq_{user_id}"
    is_owner = bool(OWNER_QQ) and user_id == OWNER_QQ

    if brain.is_hijack_request(text):
        if is_owner:
            return brain.chat(text, force_master=True, session_id=session_id,
                              is_owner=True, voice_mode=voice_mode)
        return "这个称呼我只留给一个人，你可不能乱叫哦。想聊天的话，我随时都在。", "gentle"

    return brain.chat(text, session_id=session_id, is_owner=is_owner,
                      voice_mode=voice_mode, cold=cold)


# ---------- 表情：颜文字 ----------
_KAOMOJI = {
    "gentle": ["(｡･ω･｡)", "(´･ω･`)"],
    "happy": ["٩(ˊᗜˋ*)و", "(๑˃̵ᴗ˂̵)و"],
    "playful": ["(￣▽￣)~*", "(≧▽≦)"],
    "thoughtful": ["(￣ω￣;)", "(・_・;)"],
}
_last_kaomoji = ""
_reply_seq = 0


def decorate(reply, emotion):
    """偶尔在句尾加一个颜文字；已有颜文字的回复不再叠加"""
    global _last_kaomoji
    if emotion not in _KAOMOJI or len(reply) > 60:
        return reply
    if random.random() > 0.18 or re.search(r"[（(][^）()]{0,8}[）)]", reply):
        return reply
    pool = [k for k in _KAOMOJI[emotion] if k != _last_kaomoji]
    k = random.choice(pool)
    _last_kaomoji = k
    return reply.rstrip() + " " + k


# ---------- 语音模式：主人私聊可开，状态存内存，重启失效 ----------
_VOICE_MODE_USERS = set()  # 开启了语音模式的 QQ 号（字符串）
# 开启侧严格：只认明确指令，避免聊天中误触发
_VOICE_ON_RE = re.compile(r"^(开启|打开|开一下|开|启动|进入|切换到?)(一下)?语音模式[呀啊哦啦。！!～~]*$")
# 关闭侧放宽：开会/外面时不用想标准措辞
_VOICE_OFF_RE = re.compile(r"关闭语音模式|退出语音模式|别(再)?(发|用)语音|打字吧|用文字|发文字")
_VOICE_ON_TEXT = "好，接下来我都用声音陪你说话啦。"
_VOICE_OFF_TEXT = "好，那我用回文字。"
_VOICE_DENY_TEXT = "语音模式只对他开放哦，我们用文字聊就好。"
# TTS 冷启动（约 20~30 秒）期间先发这条，避免干等没反应
_VOICE_WAKE_TEXT = "稍等，我把声音找回来，马上就好…"


# ---------- 语音回复：仅主人、仅私聊 ----------
_VOICE_RE = re.compile(
    r"(听你(说话|说|讲|声音)|想听你(说|说话|念|唱)|用(声音|语音)回|发.{0,8}(语音|声音)|"
    r"来.{0,4}(语音|声音)|语音回我|念给我听|读给我听|让我听听你的声音|"
    r"再(说|讲|念)(一|几|两)?句|(说|讲|念)(一|几|两)句)"
)
_VOICE_HINT_RE = re.compile(r"(听|说|讲|声音|念|语音)")
_VOICE_ASK_EXEMPT = re.compile(r"(想听|听你|回我|发条|来条|念给|给我发)")


def _deepseek_voice_judge(text):
    """短句里出现相关字眼但没命中规则时，让模型判断一次（max_tokens=5）"""
    try:
        prompt = f'用户说："{text}"\n他是在要求对方用语音/声音回复吗？只回答"是"或"否"。'
        data = {
            "model": brain.LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 5,
        }
        if brain._LLM_NEED_NO_THINK:
            data["enable_thinking"] = False
        resp = brain.llm_post(data, timeout=5)
        if resp.status_code == 200:
            ans = resp.json()["choices"][0]["message"]["content"].strip()
            if "否" in ans or "不是" in ans:
                return False
            return "是" in ans
    except Exception:
        pass
    return False


def wants_voice(text):
    """判断这条消息是不是想让纳西妲用语音回复"""
    t = (text or "").strip()
    if not t or len(t) > 40:
        return False
    # 「你支持语音吗」「能发语音吗」这类功能询问不算请求
    if re.search(r"(吗|么|嘛)[？?]?$", t) and not _VOICE_ASK_EXEMPT.search(t):
        return False
    if _VOICE_RE.search(t):
        return True
    # 没命中规则时，只对很短的、带相关字眼和请求语气的句子兜底问一次
    if len(t) <= 12 and _VOICE_HINT_RE.search(t) and re.search(r"(想|要|能不能|可以|给我|让我)", t):
        return _deepseek_voice_judge(t)
    return False


def split_voice(text, soft=20, max_parts=3):
    """语音分段：≤soft 字一条；超了按完整句子分段（不拦腰切句），最多 max_parts 段"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= soft:
        return [text]
    sents = [s.strip() for s in re.split(r"(?<=[。！？!?～~…\n])", text) if s.strip()]
    if not sents:
        return [text]
    # 贪心合并：每段尽量靠近 soft 字，但保持句子完整
    segs, buf = [], ""
    for s in sents:
        if buf and len(buf) + len(s) > soft and len(segs) < max_parts - 1:
            segs.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        segs.append(buf)
    # 段数超上限：尾部并成一段
    if len(segs) > max_parts:
        segs = segs[: max_parts - 1] + ["".join(segs[max_parts - 1:])]
    # 过短的段（如语气词"嗯————……"）并入后一段，单独发给 TTS 会拖出长怪音
    # 段长按有效字符算（去掉标点），否则"嗯————"这种 6 字符的段会漏判
    def _vlen(s):
        return len(re.sub(r"[^\w一-鿿]", "", s))
    merged = []
    for s in segs:
        if merged and _vlen(merged[-1]) < 4:
            merged[-1] += s
        else:
            merged.append(s)
    if len(merged) > 1 and _vlen(merged[-1]) < 4:
        merged[-2] += merged[-1]
        merged.pop()
    return merged


# ---------- TTS 自动拉起：需要出声时 9880 没在运行就自动启动 GPT-SoVITS ----------
TTS_PORT = 9880
_SOVITS_DIR = _paths.get("SOVITS_DIR", r"E:\fariy\GPT-SoVITS\GPT-SoVITS-v2pro-20261006")
_SOVITS_PY = os.path.join(_SOVITS_DIR, "runtime", "python.exe")
_tts_lock = threading.Lock()


def _tts_alive():
    """毫秒级探测 TTS 端口，服务在跑时零开销"""
    try:
        with socket.create_connection(("127.0.0.1", TTS_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def _ensure_tts(timeout=75):
    """TTS 未运行时自动拉起并等待就绪；超时/失败返回 False，由调用方回退文字"""
    if _tts_alive():
        return True
    with _tts_lock:
        if _tts_alive():  # 拿锁期间别的线程刚好拉起
            return True
        if not os.path.isfile(_SOVITS_PY):
            print(f"[QQ] 自动拉起 TTS 失败：找不到 {_SOVITS_PY}")
            return False
        print("[QQ] TTS 未运行，自动启动 GPT-SoVITS（加载约 20~30 秒）...")
        tmp = os.environ.get("TEMP", "/tmp")
        try:
            log = open(os.path.join(tmp, "naxida_tts.log"), "ab")
            err = open(os.path.join(tmp, "naxida_tts_err.log"), "ab")
            subprocess.Popen(
                [_SOVITS_PY, "api_v2.py"], cwd=_SOVITS_DIR,
                stdout=log, stderr=err,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            log.close()
            err.close()
        except Exception as e:
            print(f"[QQ] TTS 启动失败：{e}")
            return False
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(1)
            if _tts_alive():
                print("[QQ] TTS 已就绪")
                return True
        print("[QQ] TTS 启动超时，本次回退文字")
        return False


def make_voice(reply, emotion):
    """把回复合成语音并做增益处理，输出 wav 交给 NapCat 转 silk（音质远好于 amr），失败返回 None"""
    if not _ensure_tts():
        return None
    text = re.sub(r"[（(][^）)]{0,12}[）)]", "", reply or "").strip()
    text = brain.clean_text_for_tts(text)
    if not text:
        return None
    wav = brain.tts_once(text, emotion)
    if not wav:
        return None
    out = os.path.splitext(wav)[0] + "_q.wav"
    try:
        # TTS 原始电平偏低，增益 3.0 + 响度归一化（-10 LUFS），保留原采样率
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav, "-af", "volume=3.0,loudnorm=I=-10:TP=-1.0", out],
            capture_output=True, timeout=90,
        )
    except Exception as e:
        print(f"[QQ] 语音增益处理失败：{e}")
    try:
        os.remove(wav)
    except OSError:
        pass
    if os.path.exists(out) and os.path.getsize(out) > 20000:
        return out
    return None


def _wrap_long(chunk, limit):
    """超过 limit 的块按逗号/分号再切开，避免一条太长"""
    if len(chunk) <= limit:
        return [chunk]
    parts, buf = [], ""
    for seg in re.split(r"(?<=[，,；;、])", chunk):
        if buf and len(buf) + len(seg) > limit:
            parts.append(buf)
            buf = seg
        else:
            buf += seg
    if buf:
        parts.append(buf)
    return parts


# ---------- 语音消息识别（ASR）----------
ASR_URL = "http://127.0.0.1:9881/asr"
_ASR_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "naxida_asr")
os.makedirs(_ASR_TMP, exist_ok=True)


def _record_file(event):
    """从消息里提取语音文件的本地路径（NapCat 会把收到的语音落到自己缓存里）"""
    msg = event.get("message")
    if not isinstance(msg, list):
        return ""
    for seg in msg:
        if seg.get("type") == "record":
            d = seg.get("data") or {}
            return d.get("file") or d.get("path") or ""
    return ""


def _voice_to_wav(src):
    """QQ 语音转 16k wav：先 ffmpeg 直转；silk 等 ffmpeg 解不了的格式让 NapCat 转 mp3 兜底"""
    dst = os.path.join(_ASR_TMP, f"v{int(time.time() * 1000)}.wav")
    try:
        r = subprocess.run(["ffmpeg", "-y", "-i", src, "-ar", "16000", "-ac", "1", dst],
                           capture_output=True, timeout=60)
    except Exception as e:
        print(f"[QQ] ffmpeg 转码异常：{e}")
        r = None
    if r is not None and r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 1000:
        return dst
    # ffmpeg 直转失败：走 NapCat get_record
    mp3 = client.get_record(src, "mp3")
    if mp3 and os.path.isfile(mp3):
        try:
            r = subprocess.run(["ffmpeg", "-y", "-i", mp3, "-ar", "16000", "-ac", "1", dst],
                               capture_output=True, timeout=60)
        except Exception:
            r = None
        try:
            os.remove(mp3)
        except OSError:
            pass
        if r is not None and r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 1000:
            return dst
    return ""


def _transcribe_qq_voice(event):
    """语音消息 → 文字。链路（取文件→转码→ASR）任一步失败返回空串"""
    src = _record_file(event)
    if not src:
        return ""
    if not os.path.isfile(src):
        # file 字段不是本地路径（url 等）：让 NapCat 落地并转格式
        mp3 = client.get_record(src, "mp3")
        if not (mp3 and os.path.isfile(mp3)):
            return ""
        src = mp3
    wav = _voice_to_wav(src)
    if not wav:
        print(f"[QQ] 语音转码失败：{src}")
        return ""
    try:
        r = requests.post(ASR_URL, json={"path": wav}, timeout=30)
        if r.status_code == 200:
            return (r.json().get("text") or "").strip()
        print(f"[QQ] ASR 返回 {r.status_code}：{r.text[:120]}")
        return ""
    except Exception as e:
        print(f"[QQ] ASR 请求失败：{e}")
        return ""
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


def split_chat(text, max_parts=3, soft_limit=45):
    """按长度自然分段：单条不超过 soft_limit 字，最多 3 条"""
    text = (text or "").strip()
    if not text:
        return []
    # 模型偶尔会拿空行分段，QQ 里会变成气泡中间一大块空白，先压成单换行
    text = re.sub(r"\n\s*\n+", "\n", text)

    sents = [s.strip() for s in re.split(r"(?<=[。！？!?～~…\n])", text)]
    sents = [s for s in sents if s]
    if len(sents) <= 1 or len(text) <= soft_limit:
        # 短回复里有换行就按换行拆成两条，像真人连着打字
        nl = [p.strip() for p in text.split("\n") if p.strip()]
        if 1 < len(nl) <= max_parts:
            return nl
        return split_text(text, 300)

    chunks, buf = [], ""
    for s in sents:
        if buf and len(buf) + len(s) > soft_limit:
            chunks.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        chunks.append(buf)

    # 条数超了就合并尾部，保证不超过上限
    if len(chunks) > max_parts:
        chunks = chunks[: max_parts - 1] + ["".join(chunks[max_parts - 1:])]

    # 长度不算夸张时，有一部分概率整条发，避免每条都整齐拆成两段
    if len(chunks) == 2 and len(text) < 65 and random.random() < 0.4:
        return split_text(text, 300)

    # 把「查到了。」「嗯。」这类碎片并进相邻块
    merged = []
    for c in chunks:
        if merged and len(merged[-1]) < 5:
            merged[-1] += c
        else:
            merged.append(c)
    if len(merged) > 1 and len(merged[-1]) < 5:
        merged[-2] += merged[-1]
        merged.pop()

    out = []
    for c in merged:
        out.extend(_wrap_long(c, soft_limit + 10))
    # 末条只是句反问、或短得没东西，就并回上一条，别让「你玩哪个位置」单独发出去
    if len(out) > 1:
        last = out[-1].strip()
        tail = [s for s in re.split(r"(?<=[。！？!?…])", last) if s.strip()]
        tail = tail[-1] if tail else last
        asks = ("？" in tail or "?" in tail
                or bool(re.search(r"(吗|呢|吧|么)[。！!…]?$", tail))
                or bool(re.search(r"(你|您).{0,8}(哪|什么|怎么|要不要|能不能|会不会|是不是)", tail)))
        if len(last) < 8 or asks:
            out[-2] += last
            out.pop()
    if len(out) > max_parts:
        out = out[: max_parts - 1] + ["".join(out[max_parts - 1:])]
    return out


def _handle(event):
    if event.get("post_type") != "message":
        return
    if event.get("message_type") not in ("private", "group"):
        return

    self_id = event.get("self_id")
    user_id = event.get("user_id")
    if str(user_id) == str(self_id):
        return  # 忽略自己发的

    is_group = event.get("message_type") == "group"
    is_owner = bool(OWNER_QQ) and str(user_id) == OWNER_QQ

    text = extract_text(event)
    if not text:
        # 没有文字：是 QQ 语音时做识别，仅限主人私聊，其他人/群聊直接忽略
        if is_group or not is_owner or not _record_file(event):
            return
        text = _transcribe_qq_voice(event)
        if not text:
            client.send_private(user_id, "这条语音我没听清呢，再发一遍试试？")
            print(f"[QQ] 语音识别失败（{user_id}）")
            return
        print(f"[QQ] 语音识别（{user_id}）：{text}")

    if is_group and GROUP_AT_ONLY and not at_me(event, self_id):
        return  # 群里默认只在被 @ 时回应，避免刷屏

    who = f"群{event.get('group_id')}/{user_id}" if is_group else str(user_id)

    # 忽略名单：不经过大模型，只回一句固定文字，也不落库
    if str(user_id) in IGNORE_LIST:
        if is_group:
            client.send_group(event.get("group_id"), "已忽略你了哟")
        else:
            client.send_private(user_id, "已忽略你了哟")
        print(f"[QQ] 已忽略 {who}：{text[:30]}")
        return

    # 受限名单：固定短语回应，不调模型、不落库、不进记忆，超频就沉默
    if str(user_id) in RESTRICTED_LIST:
        line = _restricted_reply(str(user_id))
        if line:
            if is_group:
                client.send_group(event.get("group_id"), line)
            else:
                client.send_private(user_id, line)
            print(f"[QQ] 受限回应 {who}：{text[:20]} → {line}")
        else:
            print(f"[QQ] 受限用户 {who} 触发频控，本次不回")
        return

    # 放行名单：能正常聊，但语气冷淡；所有非主人用户当天 token 用完就退回固定短语
    guarded = str(user_id) in GUARDED_LIST
    if not is_owner and _budget_left(str(user_id)) <= 0:
        line = _restricted_reply(str(user_id))
        if line:
            if is_group:
                client.send_group(event.get("group_id"), line)
            else:
                client.send_private(user_id, line)
            print(f"[QQ] {who} 今日额度已用完，回固定短语：{line}")
        else:
            print(f"[QQ] {who} 今日额度已用完且触发频控，本次不回")
        return

    print(f"[QQ] 收到 {who}：{text}")

    # /赞 /踩 反馈：对最近一条回复打分
    if text.strip() in ("/赞", "/踩"):
        uid = str(user_id)
        last = _last_reply.get(uid)
        if not last:
            client.send_private(user_id, "还没有可以评价的回复呢")
            return
        q, a = last
        label = "up" if text.strip() == "/赞" else "down"
        _save_feedback(uid, label, q, a)
        client.send_private(user_id, "收到啦，我记住了" if label == "up" else "呜……我会改进的")
        print(f"[QQ] 反馈 {who}: {label} | Q: {q[:30]}")
        return

    # 主动陪伴：他来了就算「有回应」，顺便解除降频与暂停
    if is_owner and not is_group and company:
        company.note_user_message(str(user_id))

    # 语音模式开关：硬指令，拦截后直接回复，不进大模型、不落库
    if not is_group and _VOICE_ON_RE.match(text.strip()):
        if is_owner:
            _VOICE_MODE_USERS.add(str(user_id))
            if not _tts_alive():
                client.send_private(user_id, _VOICE_WAKE_TEXT)
            amr = make_voice(_VOICE_ON_TEXT, "gentle")
            if amr:
                client.send_private_record(user_id, amr)
                print(f"[QQ] {who} 开启语音模式（语音确认）")
            else:
                client.send_private(user_id, _VOICE_ON_TEXT)
                print(f"[QQ] {who} 开启语音模式（合成失败，文字确认）")
        else:
            client.send_private(user_id, _VOICE_DENY_TEXT)
            print(f"[QQ] 拒绝 {who} 开启语音模式")
        return
    if not is_group and str(user_id) in _VOICE_MODE_USERS and _VOICE_OFF_RE.search(text):
        _VOICE_MODE_USERS.discard(str(user_id))
        client.send_private(user_id, _VOICE_OFF_TEXT)
        print(f"[QQ] {who} 关闭语音模式")
        return

    # 点歌功能已关闭（用户反馈不好用）
    if False and is_owner and not is_group:
        uid = str(user_id)
        
        # 现成曲库命中 → 直接发
        song_match = match_song(text)
        if song_match:
            song_name, song_file = song_match
            song_path = os.path.join(MUSIC_DIR, song_file)
            if os.path.exists(song_path):
                print(f"[QQ] 点歌：{song_name}")
                try:
                    from music_singer import prepare_record
                    send_path = prepare_record(song_path)
                except Exception as e:
                    print(f"[QQ] 语音压缩失败，改发原文件：{e}")
                    send_path = song_path
                if client.send_private_record(user_id, send_path):
                    return
                client.send_private(user_id, f"唱{song_name}的语音没发出去，文件可能太大，我换个方式")
                return
        
        # 没命中曲库 → 抽歌名关键词
        song_keyword = None
        sing_kw = r"(唱|来一首|来首|放一首|点一首|点歌|唱一下|唱首|来一段|唱一段)"
        m = re.search(f"{sing_kw}\\s*(.+)", text.strip())
        if m:
            kw = m.group(2).strip()
            kw = re.sub(r"^(唱|唱首|唱一下|来一首|来首|放一首|点一首|点歌|来一段|唱一段)", "", kw).strip()
            if kw and len(kw) >= 2:
                song_keyword = kw
        if song_keyword is None:
            m2 = re.search(r"(?:我想听.*?唱|唱给我听|想听你唱|你唱一首|来一段|唱一段)\s*(.+)", text.strip())
            if m2:
                kw = m2.group(1).strip()
                kw = re.sub(r"(好吗|好不好|行不行|可以吗|呀|呢|吧|哦|啊)$", "", kw).strip()
                if kw and len(kw) >= 2:
                    song_keyword = kw

        if song_keyword:
            try:
                from music_singer import find_bilibili_song
                candidates = find_bilibili_song(song_keyword)
                if not candidates:
                    client.send_private(user_id, f"没搜到《{song_keyword}》，你换个词试试？")
                    return
                # 自动挑分数最高的那一个，用户不用帮着选
                c = candidates[0]
                client.send_private(user_id, f"好，就唱《{c['title']}》，等我几分钟...")
                print(f"[QQ] 点歌：{song_keyword} → 自动挑 {c['title']}")

                def _process(_c=c, _kw=song_keyword, _uid=user_id):
                    try:
                        from music_singer import process_song, prepare_record
                        # 中途报进度，不然几分钟没动静看着像掉线了
                        result, msg = process_song(
                            _c["url"], _kw,
                            progress=lambda m: client.send_private(_uid, m),
                        )
                        if not result:
                            client.send_private(_uid, f"没唱成：{msg}")
                            return
                        client.send_private(_uid, "唱好了，发给你听")
                        send_path = prepare_record(result)
                        if not client.send_private_record(_uid, send_path):
                            client.send_private(_uid, "语音没发出去，我再试一次")
                            client.send_private_record(_uid, send_path)
                    except Exception as e:
                        client.send_private(_uid, f"处理出错了：{e}")
                threading.Thread(target=_process, daemon=True).start()
                return
            except Exception as e:
                print(f"[QQ] 搜索歌曲失败：{e}")
                client.send_private(user_id, "搜索出了点问题，等我修好再唱")
                return

        # 确实只是"我想听你唱"没说歌名 → 问他想听哪首
        if re.search(r"(我想听.*?唱|唱给我听|想听你唱|你唱一首|来一段|唱一段)", text):
            try:
                songs = scan_songs()
                if songs:
                    song_list = "、".join(list(songs.keys())[:5])
                    client.send_private(user_id, f"想听我唱什么？现成我会的有：{song_list}。你说个名字我就唱给你听。")
                    return
                else:
                    client.send_private(user_id, "目前没现成能直接唱的歌。你说个歌名，我去学。")
                    return
            except Exception as e:
                print(f"[QQ] 模糊点歌处理失败：{e}")


    # 主动陪伴的开关：「别主动找我了」「可以主动找我」
    if is_owner and not is_group and company:
        says = company.handle_command(text)
        if says:
            client.send_private(user_id, says)
            print(f"[QQ] {who} 主动开口：{'开' if company.is_enabled() else '关'}")
            return

    # 语音请求要在生成前就判定：大脑需要知道这一条会被念出来
    want_voice = is_owner and not is_group and (
        str(user_id) in _VOICE_MODE_USERS or wants_voice(text)
    )

    # 节日/时令：当天第一次说话时，和回复并行生成一条问候，先发问候再回正文
    greet_job = None
    if is_owner and not is_group:
        notify = festival.pending_greeting() if festival else None
        if notify:
            box = []

            def _gen_greet(_info=notify, _uid=str(user_id), _short=want_voice):
                try:
                    box.append(brain.festival_greeting(_info, user_id=_uid, short=_short))
                except Exception as e:
                    print(f"[QQ] 节日问候生成异常：{e}")
                    box.append("")

            th = threading.Thread(target=_gen_greet, daemon=True)
            th.start()
            greet_job = (notify, box, th)

    # 发"正在输入"状态，让用户知道在回复
    if not is_group:
        try:
            client.call_api("set_input_status", {"user_id": int(user_id), "bust_duration": 5000})
        except Exception:
            pass

    try:
        reply, emotion = _reply_for(event, text, voice_mode=want_voice, cold=guarded)
        if not is_owner:
            _tok = brain.LAST_USAGE.pop(f"qq_{user_id}", 0)
            _left = _budget_add(str(user_id), _tok)
            print(f"[QQ] {who} 本轮 {_tok} token，今日还剩 {_left}")
    except Exception as e:
        print(f"[QQ] 生成回复失败：{e}")
        import traceback
        traceback.print_exc()
        reply, emotion = "嗯……我刚才走神了，你等一下再跟我说一次好吗？", "gentle"

    # 先发节日问候（等它生成完，最多 8 秒），再发正文
    if greet_job:
        notify, box, th = greet_job
        th.join(timeout=8)
        gtext = (box[0] if box else "").strip()
        if gtext:
            sent = False
            if want_voice:
                gv = make_voice(gtext, "gentle")
                if gv:
                    client.send_private_record(user_id, gv)
                    sent = True
            if not sent:
                client.send_private(user_id, gtext)
            festival.mark_greeted(notify)
            print(f"[QQ] 节日问候（{notify['key']}）：{gtext[:40]}")
            time.sleep(random.uniform(0.4, 0.8))
        else:
            print(f"[QQ] 节日问候（{notify['key']}）生成失败，跳过")

    # 语音回复：只对主人、只在私聊；按 20 字分段逐条合成发送
    if want_voice:
        if not _tts_alive():
            client.send_private(user_id, _VOICE_WAKE_TEXT)
        # 详细日志：原始回复 + 分段 + clean，方便排查"嗯——"类问题
        print(f"[QQ] 语音回复-原始回复[{len(reply)}字]: {repr(reply)}")
        segs = split_voice(reply)
        print(f"[QQ] 语音回复，分 {len(segs)} 段合成...")
        for i, seg in enumerate(segs):
            clean = brain.clean_text_for_tts(seg)
            print(f"  段{i+1}: [{len(seg)}字] {repr(seg[:30])} → clean[{len(clean)}字] {repr(clean[:30])}")
        amrs = [make_voice(seg, emotion) for seg in segs]
        if all(amrs) and amrs:
            for i, amr in enumerate(amrs):
                client.send_private_record(user_id, amr)
                if i < len(amrs) - 1:
                    time.sleep(random.uniform(0.5, 0.9))
            print(f"[QQ] 已发语音 x{len(amrs)}")
            _last_reply[str(user_id)] = (text, reply)
            print(f"[QQ] 已回复 {who}")
            return
        print("[QQ] 语音合成/转码失败，回退文字")

    global _reply_seq
    _reply_seq += 1
    if not guarded:  # 放行用户保持冷淡，不撒娇不加颜文字
        reply = decorate(reply, emotion)
    print(f"[QQ] 回复预览：{reply[:60]}")

    if is_group:
        # 群里保持一条，避免刷屏
        for chunk in split_text(reply):
            client.send_group(event.get("group_id"), chunk)
    else:
        # 私聊拆成几条短消息，逐条发，像真人打字
        parts = split_chat(reply)
        for i, chunk in enumerate(parts):
            client.send_private(user_id, chunk)
            if i < len(parts) - 1:
                time.sleep(random.uniform(0.4, 0.8))
    _last_reply[str(user_id)] = (text, reply)  # 记录供 /赞 /踩 使用
    print(f"[QQ] 已回复 {who}")


client = OneBotClient(WS_URL, WS_TOKEN, on_message=_task_queue.put)


# ---------- 主动陪伴：后台调度，到点了才开口 ----------
def _try_proactive():
    uid = f"qq_{OWNER_QQ}"
    slot = company.next_due(uid)
    if not slot:
        return
    source, anchor, emotion = company.pick_real_anchor_and_emotion(uid)
    # 没有真实锚点就不发——宁可不发也不要编
    if not source or not anchor:
        company.skip_slot(slot, "无真实锚点")
        print(f"[QQ] 没有真实锚点可说，这条跳过")
        return
    texts = company.recent_texts()
    text = brain.proactive_message(anchor, emotion, source=source, user_id=uid, last_texts=texts)
    ok, why = company.check(text, source=source)
    if not ok:
        # 不合格重写一次，还不行就不发
        text = brain.proactive_message(anchor, emotion, source=source, user_id=uid,
                                       last_texts=texts, fix=why)
        ok, why = company.check(text, source=source)
    if not ok:
        company.skip_slot(slot, why)
        print(f"[QQ] 主动消息没过闸门（{why}），这条不发")
        return

    sent = False
    if OWNER_QQ in _VOICE_MODE_USERS and _tts_alive():
        v = make_voice(text, "gentle")
        if v:
            client.send_private_record(OWNER_QQ, v)
            sent = True
    if not sent:
        client.send_private(OWNER_QQ, re.sub(r"\n\s*\n+", "\n", text).strip())
    company.note_sent(text, source, slot)
    brain.remember_said(text, session_id=f"qq_{OWNER_QQ}")
    print(f"[QQ] 主动开口（{source}）：{text[:40]}")


def _company_loop():
    """每两分钟看一次：到点、他没在聊、有由头，才开口"""
    while True:
        try:
            if company and OWNER_QQ and client.is_connected():
                _try_proactive()
        except Exception as e:
            print(f"[QQ] 主动陪伴异常：{e}")
            import traceback
            traceback.print_exc()
        time.sleep(120)


def main():
    # 1) 启动前提醒 hosts patch（改一次永久生效）
    if not _is_qq_update_blocked():
        _print_hosts_patch_cmd()

    # 2) 守护线程：worker / 主动陪伴 / NapCat watchdog
    threading.Thread(target=_worker, daemon=True).start()
    if company and OWNER_QQ:
        threading.Thread(target=_company_loop, daemon=True).start()
    threading.Thread(target=_watchdog_loop, daemon=True).start()

    print(f"[QQ] 纳西妲已就绪，等待消息（WS: {WS_URL}）")
    if not OWNER_QQ:
        print("[QQ] 提示：config.json 里 OWNER_QQ 未填写，主人模式在 QQ 端不可用")
    client.run_forever()


if __name__ == "__main__":
    main()
