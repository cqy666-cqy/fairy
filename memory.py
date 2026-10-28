"""记忆模块：SQLite 对话持久化 + 后台用户画像提炼

设计原则：
- 零新依赖（sqlite3 是 Python 内置）
- 写入 <1ms，启动恢复毫秒级，不拖慢任何交互
- 画像提炼走后台线程，绝不阻塞对话
"""
import os
import sqlite3
import threading
import time
import datetime

# 模型额度轮换（与大脑共用同一份耗尽记录）
import llm_rotate

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory")
DB_PATH = os.path.join(DB_DIR, "naxida_memory.db")

# 画像提炼触发间隔（每 N 轮对话触发一次）
_PROFILE_INTERVAL = 5
_turn_counter = 0
_profile_lock = threading.Lock()
_profile_thread = None
_pending_notice = None  # 待显示的系统提示（避免和流式输出抢屏）

_llm_conf_cache = None


def _get_llm_conf():
    """读取对话/摘要用的模型配置，返回 (url, key, 模型链, 是否需关思考模式)。
    读 config.json 的 LLM_*，缺省回退到 DeepSeek 官方 deepseek-chat。"""
    global _llm_conf_cache
    if _llm_conf_cache is None:
        import json as _j
        _cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        with open(_cfg_path, "r", encoding="utf-8") as _f:
            cfg = _j.load(_f)
        base = cfg.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        model = cfg.get("LLM_MODEL", "deepseek-chat")
        chain = [m for m in (cfg.get("LLM_MODEL_LIST") or []) if m] or [model]
        if model in chain:
            chain.remove(model)
        chain.insert(0, model)
        _llm_conf_cache = (
            base + "/chat/completions",
            cfg.get("LLM_API_KEY") or cfg["DEEPSEEK_API_KEY"],
            chain,
            ("aliyuncs.com" in base) or ("qianwenai" in base),
        )
    return _llm_conf_cache


def _post_llm(url, key, chain, body, timeout=20):
    """发摘要请求；模型到额度上限时自动换下一个。全用尽返回 None（一个请求都不发）"""
    import requests
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    resp = None
    for _ in range(len(chain)):
        model = llm_rotate.current(chain)
        if not model:
            print("[模型] 所有免费模型额度已用尽，摘要/画像提炼已跳过（不调用付费额度）")
            return None
        body["model"] = model
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        if resp.status_code == 200:
            try:
                _u = resp.json().get("usage") or {}
                _tok = _u.get("total_tokens") or 0
                if not _tok:
                    _chars = sum(len(str(m.get("content") or "")) for m in body.get("messages") or [])
                    _tok = int(_chars / 1.2)
                llm_rotate.add_usage(model, _tok)
            except Exception:
                pass
            return resp
        if llm_rotate.is_quota_error(resp.status_code, resp.text):
            llm_rotate.mark_exhausted(model, chain)
            continue
        return resp
    return resp


def pop_notice():
    """主循环在打印输入提示符前调用，取走缓冲的系统提示"""
    global _pending_notice
    n = _pending_notice
    _pending_notice = None
    return n


def _conn():
    os.makedirs(DB_DIR, exist_ok=True)
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    """初始化表结构，程序启动时调用一次"""
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL DEFAULT 'default',
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                emotion TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 旧库迁移：补上 user_id 列（已有数据自动归到 default 会话）
        cols = [r[1] for r in c.execute("PRAGMA table_info(conversations)").fetchall()]
        if "user_id" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'")
            print("【记忆】已升级数据库：conversations 表新增 user_id 列")
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversation_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL DEFAULT 'default',
                summary TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 旧库迁移：摘要补上 user_id，已有摘要归到主人的会话
        scols = [r[1] for r in c.execute("PRAGMA table_info(conversation_summary)").fetchall()]
        if "user_id" not in scols:
            c.execute("ALTER TABLE conversation_summary ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'")
        # 结构化记忆：按用户隔离，分 stable/temporary/event/taboo 四层，可带过期时间
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'stable',
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                confidence INTEGER NOT NULL DEFAULT 0,
                expires_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, key)
            )
        """)
        c.commit()
    _migrate_profile()
    _auto_cleanup()


def _owner_user_id():
    """读取配置里的主人 QQ，旧画像迁移时归到主人名下，避免泄露给其他人"""
    try:
        import json as _j
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        with open(path, "r", encoding="utf-8") as f:
            q = str(_j.load(f).get("OWNER_QQ") or "")
        return f"qq_{q}" if q else "default"
    except Exception:
        return "default"


def _related_ids(user_id):
    """终端/网页的默认会话属于主人，主人的 QQ 会话可以共享 default 的长期记忆；其他人只看自己的"""
    owner = _owner_user_id()
    if user_id in ("default", owner):
        return tuple({user_id, "default", owner})
    return (user_id,)


def _migrate_profile():
    """把旧的无归属 user_profile 迁进结构化 user_facts，旧表改名留作备份。只执行一次"""
    try:
        with _conn() as c:
            tables = [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if "user_profile" not in tables:
                return
            rows = c.execute("SELECT key, value FROM user_profile").fetchall()
            uid = _owner_user_id()
            for k, v in rows:
                c.execute("""INSERT OR IGNORE INTO user_facts (user_id, category, key, value, confidence)
                             VALUES (?, 'stable', ?, ?, 1)""", (uid, k, v))
            c.commit()
            if rows:
                c.execute("ALTER TABLE user_profile RENAME TO user_profile_legacy")
                c.commit()
                print(f"【记忆】旧画像已迁入结构化记忆（{len(rows)} 条）")
    except Exception as e:
        print(f"【记忆】画像迁移失败：{e}")


def _auto_cleanup():
    """启动时自动清理：对话超过90天删掉，总数超过500轮只留最近500轮"""
    if not os.path.exists(DB_PATH):
        return
    try:
        with _conn() as c:
            # 删90天前的老对话
            c.execute("DELETE FROM conversations WHERE created_at < datetime('now', '-90 days')")
            # 超过500轮（1000条）只留最近500轮
            c.execute("""
                DELETE FROM conversations WHERE id NOT IN (
                    SELECT id FROM conversations ORDER BY id DESC LIMIT 1000
                )
            """)
            c.commit()
    except Exception:
        pass


def save_turn(user_text, reply, emotion, user_id="default"):
    """保存一轮对话（user + assistant），<1ms。user_id 用于隔离不同会话"""
    with _conn() as c:
        c.execute("INSERT INTO conversations (user_id, role, content) VALUES (?, ?, ?)",
                  (user_id, "user", user_text))
        c.execute("INSERT INTO conversations (user_id, role, content, emotion) VALUES (?, ?, ?, ?)",
                  (user_id, "assistant", reply, emotion))
        c.commit()


def last_turn_age_seconds(user_id="default"):
    """距离该会话最后一句话过去了多少秒；没有记录返回 None。
    用 SQLite 自己的 now 计算，避免 created_at（UTC）和本地时间混算。"""
    if not os.path.exists(DB_PATH):
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT CAST((julianday('now') - julianday(MAX(created_at))) * 86400 AS INTEGER) "
            "FROM conversations WHERE user_id = ?",
            (user_id,)
        ).fetchone()
    if not row or row[0] is None:
        return None
    return max(0, int(row[0]))


def load_recent(limit=20, user_id="default"):
    """启动时恢复指定会话最近 N 条对话到 messages 列表"""
    if not os.path.exists(DB_PATH):
        return []
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content FROM conversations WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    # 数据库是倒序取的，翻回正序
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


_CATEGORY_ORDER = {"stable": 0, "taboo": 1, "event": 2, "temporary": 3}
_CATEGORY_CN = {"stable": "长期信息", "taboo": "不要主动提起的事", "event": "近期的事", "temporary": "最近状态"}


def set_fact(user_id, key, value, category="stable", confidence=0, ttl_days=None):
    """写一条结构化记忆。confidence=1 表示用户明确确认过，之后不会被自动提炼覆盖"""
    exp = None
    if ttl_days:
        exp = (datetime.datetime.now() + datetime.timedelta(days=ttl_days)).strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        c.execute("""
            INSERT INTO user_facts (user_id, category, key, value, confidence, expires_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, key) DO UPDATE SET
                category=excluded.category, value=excluded.value, confidence=excluded.confidence,
                expires_at=excluded.expires_at, updated_at=CURRENT_TIMESTAMP
            WHERE user_facts.confidence = 0 OR excluded.confidence = 1
        """, (user_id, category, key, value, confidence, exp))
        c.commit()


def get_fact(user_id, key):
    """取一条记忆的原始值，没有或已过期返回空字符串"""
    if not os.path.exists(DB_PATH):
        return ""
    with _conn() as c:
        row = c.execute(
            "SELECT value FROM user_facts WHERE user_id=? AND key=? "
            "AND (expires_at IS NULL OR expires_at > datetime('now'))",
            (user_id, key)).fetchone()
    return row[0] if row else ""


def delete_fact(user_id, key):
    with _conn() as c:
        c.execute("DELETE FROM user_facts WHERE user_id=? AND key=?", (user_id, key))
        c.commit()


def get_profile_text(user_id="default"):
    """按用户取结构化记忆，过滤过期项后注入 prompt。无记忆时返回空字符串"""
    if not os.path.exists(DB_PATH):
        return ""
    ids = _related_ids(user_id)
    marks = ",".join("?" * len(ids))
    with _conn() as c:
        rows = c.execute(f"""
            SELECT category, key, value FROM user_facts
            WHERE user_id IN ({marks}) AND (expires_at IS NULL OR expires_at > datetime('now'))
        """, ids).fetchall()
    if not rows:
        return ""
    rows.sort(key=lambda r: _CATEGORY_ORDER.get(r[0], 9))
    lines = []
    last_cat = None
    for cat, k, v in rows:
        if cat != last_cat:
            lines.append(f"· {_CATEGORY_CN.get(cat, cat)}：")
            last_cat = cat
        lines.append(f"  - {k}：{v}")
    return ("\n\n【你记得关于对方的事。合适的时候自然用上，不要每句都说「我记得」；"
            "标为「不要主动提起的事」的内容绝不能主动说起】：\n" + "\n".join(lines))


def list_facts(user_id="default"):
    """取结构化记忆的原始行（过滤过期项），供主动陪伴挑素材用"""
    if not os.path.exists(DB_PATH):
        return []
    ids = _related_ids(user_id)
    marks = ",".join("?" * len(ids))
    with _conn() as c:
        rows = c.execute(f"""
            SELECT category, key, value FROM user_facts
            WHERE user_id IN ({marks}) AND (expires_at IS NULL OR expires_at > datetime('now'))
        """, ids).fetchall()
    return [{"category": r[0], "key": r[1], "value": r[2]} for r in rows]


# ===== 长期记忆：旧对话摘要压缩 =====
_SUMMARY_MAX = 5           # 最多保留最近 5 条摘要
_SUMMARY_MAX_CHARS = 6000  # 注入 prompt 的摘要总长度上限
_summary_thread = None


def _summarize_text(conv_text):
    """调 DeepSeek 把一段对话压缩成简明摘要，失败返回空串"""
    try:
        import requests, json as _j
        url, key, chain, no_think = _get_llm_conf()
        prompt = f"""请把下面这段对话压缩成一小段摘要，保留关键事实（聊过的话题、用户的状况/喜好、纳西妲答应过的事等），去掉寒暄和废话。
用第三人称陈述，200字以内，直接输出摘要正文。

对话记录：
{conv_text}"""
        body = {"model": "",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3, "max_tokens": 300}
        if no_think:
            body["enable_thinking"] = False
        resp = _post_llm(url, key, chain, body, timeout=20)
        if resp is None or resp.status_code != 200:
            return ""
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def _summarize_worker(msgs, user_id):
    """后台线程：把旧对话压缩成摘要并入库。
    只取 user 消息——assistant 的内容是推断/脑补，不能当事实来源"""
    try:
        conv_text = "\n".join(
            f"用户：{m.get('content', '')}" for m in msgs if m.get('role') == 'user'
        )
        summary = _summarize_text(conv_text)
        if not summary:
            return
        with _conn() as c:
            c.execute("INSERT INTO conversation_summary (user_id, summary) VALUES (?, ?)",
                      (user_id, summary))
            c.execute("""DELETE FROM conversation_summary WHERE user_id = ? AND id NOT IN (
                SELECT id FROM conversation_summary WHERE user_id = ? ORDER BY id DESC LIMIT ?)""",
                      (user_id, user_id, _SUMMARY_MAX))
            c.commit()
    except Exception:
        pass


def summarize_messages(msgs, user_id="default"):
    """把即将被丢弃的旧对话交给后台线程压缩成摘要（非阻塞）"""
    global _summary_thread
    if not msgs:
        return
    if _summary_thread and _summary_thread.is_alive():
        return
    _summary_thread = threading.Thread(
        target=_summarize_worker, args=(list(msgs), user_id), daemon=True)
    _summary_thread.start()


def get_summary_text(user_id="default"):
    """返回该用户的对话摘要文本，注入 prompt。无摘要时返回空字符串"""
    if not os.path.exists(DB_PATH):
        return ""
    ids = _related_ids(user_id)
    marks = ",".join("?" * len(ids))
    with _conn() as c:
        rows = c.execute(
            f"SELECT summary FROM conversation_summary WHERE user_id IN ({marks}) "
            "ORDER BY id DESC LIMIT ?", (*ids, _SUMMARY_MAX)
        ).fetchall()
    if not rows:
        return ""
    text = "\n".join(r[0] for r in reversed(rows))
    if len(text) > _SUMMARY_MAX_CHARS:
        text = text[-_SUMMARY_MAX_CHARS:]
    return "\n\n【以下是你们更早之前的对话摘要，供你回忆上下文，请自然地参考，不要刻意提起】：\n" + text


def _extract_profile(user_id="default"):
    """后台线程：让 DeepSeek 对照已有记忆修订——新增/更新/删除三操作，不再盲目堆积。
    只从 user 消息里提炼事实，assistant 的回复里的推断/脑补一律忽略"""
    import json
    # 取最近 10 条 user 消息作为上下文
    recent = load_recent(20, user_id=user_id)  # 多取几条，筛选后至少有 4 条 user
    recent_user = [r for r in recent if r.get('role') == 'user'][-10:]
    if len(recent_user) < 4:
        return

    conv_text = "\n".join([f"用户：{r['content']}" for r in recent_user])
    current = list_facts(user_id)
    current_text = json.dumps(current, ensure_ascii=False) if current else "（空）"

    prompt = f"""请对照「已有记忆」，根据最近对话修订关于「对方」的记忆，只输出 JSON，不要任何解释。

⚠️ 重要：下面的对话记录全是【用户】亲口说的话，纳西妲的回复完全没有出现（因为那些是她的推断，不能当事实）。所有事实必须来自用户明确说过的内容，不许编造。

已有记忆：
{current_text}

输出格式：
{{"add": {{"stable": {{"城市": "南昌"}}, "temporary": {{}}, "event": {{}}, "taboo": {{}}}}, "update": {{"stable": {{}}, "temporary": {{}}, "event": {{}}, "taboo": {{}}}}, "delete": ["要删的key"]}}

规则：
- add：对话里出现的、已有记忆里没有的事实
- update：已有记忆里有、但对话里说法变了（用相同的 key 覆盖）
- delete：已有记忆里被这轮对话否定、或明显已过时的 key
- 四层含义：stable 长期事实（城市/学校/长期喜好）、temporary 短期状态、event 有明确时间的事（值写日期或"待定"）、taboo 对方明确表示不想聊的话题
- 玩笑、反讽、自嘲、吹牛、随口一说的话，一律不算事实，不要写
- 与 taboo 冲突的内容不要写成事实（他不想聊的话题不该出现在他的喜好和安排里）
- 同一含义优先复用已有 key，不要造近义词新 key
- 没把握就不要写；不要照抄原句；不要编造；没有要删的 delete 留空数组

对话记录：
{conv_text}"""

    try:
        import requests, json, re
        llm_url, llm_key, llm_chain, llm_no_think = _get_llm_conf()
        body = {
            "model": "",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 400,
        }
        if llm_no_think:
            body["enable_thinking"] = False
        resp = _post_llm(llm_url, llm_key, llm_chain, body, timeout=20)

        if resp is None or resp.status_code != 200:
            return

        text = resp.json()["choices"][0]["message"]["content"].strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            return
        data = json.loads(text[i:j + 1])
        if not isinstance(data, dict):
            return

        # 新增与更新：短期状态 14 天过期，事件 60 天过期，稳定信息和禁忌不过期
        changed = False
        for op in ("add", "update"):
            block_all = data.get(op)
            if not isinstance(block_all, dict):
                continue
            for category, ttl in (("stable", None), ("temporary", 14), ("event", 60), ("taboo", None)):
                block = block_all.get(category)
                if not isinstance(block, dict):
                    continue
                for k, v in list(block.items())[:7]:
                    k, v = str(k).strip(), str(v).strip()
                    if k and v:
                        set_fact(user_id, k[:20], v[:60], category, 0, ttl)
                        changed = True

        # 删除：用户明确确认过的（confidence=1）不许自动删
        dels = data.get("delete")
        if isinstance(dels, list):
            for k in dels[:10]:
                k = str(k).strip()
                if not k:
                    continue
                with _conn() as c:
                    row = c.execute(
                        "SELECT confidence FROM user_facts WHERE user_id=? AND key=?",
                        (user_id, k)).fetchone()
                if row and int(row[0]) == 0:
                    delete_fact(user_id, k)
                    changed = True

        if changed:
            global _pending_notice
            _pending_notice = "【记忆：已更新】"
    except Exception:
        # 后台提炼失败不影响主流程，静默处理
        pass


def maybe_update_profile(user_text, reply, user_id="default"):
    """每轮对话后调用，达到间隔则后台异步提炼记忆"""
    global _turn_counter, _profile_thread
    _turn_counter += 1

    if _turn_counter < _PROFILE_INTERVAL:
        return
    _turn_counter = 0

    # 如果上一次提炼还在跑，跳过
    if _profile_thread and _profile_thread.is_alive():
        return

    _profile_thread = threading.Thread(
        target=_extract_profile, args=(user_id,), daemon=True
    )
    _profile_thread.start()
