"""模型额度轮换 + 本地 token 记账

关键点：百炼在免费额度用完后，只要账户还有余额，会直接返回 200 正常出字并
静默扣费，不会报「额度用完」。所以不能等报错再切——必须自己按 token 记账，
用到上限（默认 1M 的 90%）就主动切下一个模型。

全部模型都用尽时 current() 返回空串，调用方必须拒绝发请求，
绝不回落到第一个模型去花付费额度（那 300 元就是这么被扣掉的）。
"""
import json
import os
import shutil
import threading

try:
    import msvcrt  # Windows 跨进程文件锁
except ImportError:
    msvcrt = None

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
_FILE = os.path.join(_DIR, "llm_rotate.json")
_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
_lock = threading.Lock()

_QUOTA_DEFAULT = 1_000_000   # 每个模型自带 100 万免费 token
_RATIO_DEFAULT = 0.9         # 只用 90%，留 10% 缓冲，避免正好踩线被计费


class _FileLock:
    """跨进程文件锁：网页版和 QQ 机器人同时跑时，避免账本互相覆盖丢记录"""

    def __init__(self):
        self._f = None

    def __enter__(self):
        try:
            os.makedirs(_DIR, exist_ok=True)
            f = open(_FILE + ".lock", "a+b")
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write(b"\0")
                f.flush()
            f.seek(0)
            if msvcrt:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            self._f = f
        except Exception:
            # 锁文件不可用时不阻断主流程（单进程内仍有线程锁）
            self._f = None
        return self

    def __exit__(self, *exc):
        if self._f is not None:
            try:
                self._f.seek(0)
                if msvcrt:
                    msvcrt.locking(self._f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._f.close()
            except Exception:
                pass
            self._f = None
        return False


def _quota():
    """从 config.json 读总额度与可用比例，允许用户自己调更保守"""
    try:
        with open(_CFG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        total = int(cfg.get("LLM_FREE_QUOTA_TOKENS") or _QUOTA_DEFAULT)
        ratio = float(cfg.get("LLM_QUOTA_RATIO") or _RATIO_DEFAULT)
        return max(1, total), min(1.0, max(0.1, ratio))
    except Exception:
        return _QUOTA_DEFAULT, _RATIO_DEFAULT


def cap():
    """单个模型允许用到的 token 上限"""
    total, ratio = _quota()
    return int(total * ratio)


def _load():
    """读账本；主文件损坏就退回 .bak，避免一次写坏把已用量清零"""
    for path in (_FILE, _FILE + ".bak"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("exhausted", [])
                data.setdefault("used", {})
                return data
        except Exception:
            continue
    return {"exhausted": [], "used": {}}


def _save(data):
    """先写临时文件再原子替换，并留一份 .bak"""
    try:
        os.makedirs(_DIR, exist_ok=True)
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(_FILE):
            try:
                shutil.copyfile(_FILE, _FILE + ".bak")
            except Exception:
                pass
        os.replace(tmp, _FILE)
    except Exception:
        pass


def current(chain):
    """挑第一个还没用满的模型；全用满了返回空串（调用方必须拒发请求，不能回落）"""
    if not chain:
        return ""
    limit = cap()
    with _lock, _FileLock():
        data = _load()
        done = set(data.get("exhausted") or [])
        used = data.get("used") or {}
        for name in chain:
            if name in done:
                continue
            if int(used.get(name, 0)) >= limit:
                continue  # 到上限的也跳过，但不写盘，留给 add_usage 记录
            return name
        return ""


def mark_exhausted(model, chain):
    """把模型记为停用（额度耗尽/接口明确报额度不足），返回接下来该用的模型"""
    with _lock, _FileLock():
        data = _load()
        done = data.get("exhausted") or []
        if model and model not in done:
            done.append(model)
            data["exhausted"] = done
            _save(data)
    return current(chain)


def add_usage(model, tokens):
    """记一次实际消耗；跨过上限就自动停用该模型。返回是否刚刚停用"""
    try:
        tokens = int(tokens or 0)
    except Exception:
        return False
    if not model or tokens <= 0:
        return False
    limit = cap()
    hit = False
    with _lock, _FileLock():
        data = _load()
        used = data.get("used") or {}
        total = int(used.get(model, 0)) + tokens
        used[model] = total
        data["used"] = used
        done = data.get("exhausted") or []
        if total >= limit and model not in done:
            done.append(model)
            data["exhausted"] = done
            hit = True
        _save(data)
    if hit:
        print(f"[模型] {model} 累计 {total / 10000:.1f} 万 token，"
              f"达到 {limit / 10000:.0f} 万上限，已停用（不会动用付费额度）")
    return hit


def remaining(model):
    """这个模型还剩多少 token 可用"""
    used = int((_load().get("used") or {}).get(model, 0))
    return max(0, cap() - used)


def summary(chain):
    """启动时打印每个模型的用量，方便判断额度状况"""
    data = _load()
    used = data.get("used") or {}
    done = set(data.get("exhausted") or [])
    limit = cap()
    lines = []
    for m in chain:
        u = int(used.get(m, 0))
        state = "已停用" if (m in done or u >= limit) else f"剩 {(limit - u) / 10000:.1f} 万"
        lines.append(f"  {m}: 已用 {u / 10000:.1f} 万 / {limit / 10000:.0f} 万（{state}）")
    return "【模型额度】\n" + "\n".join(lines)


def is_quota_error(status, text):
    """接口明确报「额度/余额不足」时也当作耗尽（作为本地记账之外的兜底）"""
    if status not in (400, 403, 404, 429):
        return False
    t = (text or "").lower()
    raw = text or ""
    if "allocationquota" in t or "freetier" in t:
        return True
    if "额度" in raw or "余额不足" in raw or "欠费" in raw:
        return True
    if "quota" in t and ("exceed" in t or "insufficient" in t):
        return True
    return False
