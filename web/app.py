# -*- coding: utf-8 -*-
# 纳西妲网页版后端
import os, sys, time, re, json, uuid, tempfile, wave, threading
os.environ["MODELSCOPE_CACHE"] = r"E:\fariy\ai\naxida_ai\models\modelscope_cache"
os.chdir(r"E:\fariy\GPT-SoVITS\GPT-SoVITS-v2pro-20261006")
sys.path.insert(0, os.getcwd())
sys.path.insert(0, r"E:\fariy\ai\naxida_ai")

from flask import Flask, request, jsonify, send_from_directory, render_template, Response
from flask_cors import CORS

# 复用主脚本的大脑和 TTS
import importlib.util, unittest.mock
spec = importlib.util.spec_from_file_location("brain", r"E:\fariy\ai\naxida_ai\naxida_voice_brain.py")
brain = importlib.util.module_from_spec(spec)
with unittest.mock.patch("builtins.input", side_effect=KeyboardInterrupt):
    try: spec.loader.exec_module(brain)
    except KeyboardInterrupt: pass

# 启动时恢复最近对话（和终端版一致）
try:
    import memory
    recent = memory.load_recent(10)
    if recent:
        print(f"【记忆：已恢复最近 {len(recent)//2} 轮对话】")
except Exception as e:
    print(f"【记忆恢复失败：{e}】")

# 复用终端版的热词、纠错规则、情绪映射（只取数据，不会触发模型加载）
import mic_input

# ASR 模型懒加载（首次点麦克风时才加载，省 1G 显存；加锁防并发重复加载）
_asr = None
_asr_lock = threading.Lock()
def get_asr():
    global _asr
    if _asr is None:
        with _asr_lock:
            if _asr is None:  # 双重检查
                print("【ASR】首次调用，正在加载语音识别模型（约15秒）...")
                from funasr import AutoModel
                _asr = AutoModel(model="iic/SenseVoiceSmall", device="cuda", disable_update=True)
                print("【ASR】模型加载完成，之后秒识别")
    return _asr

app = Flask(
    __name__,
    template_folder=r"E:\fariy\ai\naxida_ai\web\templates",
    static_folder=r"E:\fariy\ai\naxida_ai\web\static",
)
CORS(app)

AUDIO_DIR = r"E:\fariy\ai\naxida_ai\web\static\audio"
os.makedirs(AUDIO_DIR, exist_ok=True)

AUDIO_LOG_DIR = r"E:\fariy\ai\naxida_ai\audio_log"

# 歌曲库（纳西妲已学会的歌）
MUSIC_DIR = r"E:\fariy\ai\naxida_ai\music"
# 记忆上次点的歌（用于"再唱一遍"）
_last_sung = {"name": None, "mode": None}


def scan_songs():
    """扫描 music 目录下的纳西妲成品歌曲，返回 {歌名: 文件路径}"""
    songs = {}
    try:
        for f in os.listdir(MUSIC_DIR):
            if not f.endswith(".wav"):
                continue
            # 匹配 *_纳西妲版.wav（完整）和 *_副歌_纳西妲版.wav（副歌段）
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


def match_song(text):
    """从用户文本中匹配点歌意图，返回 (歌名, 模式) 或 None。
    模式: 'full' 整首 / 'chorus' 副歌一段
    默认播副歌（最想听的部分），说"完整/整首"才播全曲"""
    songs = scan_songs()
    if not songs:
        return None
    # 点歌关键词
    sing_kw = r"(唱|来一首|来首|放一首|点一首|点歌|唱一下|唱首|来一段|唱一段|给我|给咱)"
    chorus_kw = r"(副歌|一段|片段|高潮|最火|那段|部分)"
    full_kw = r"(完整|全首|整首|全部|从头到尾)"
    # 长歌名优先匹配（避免"雨爱"抢先匹配掉"雨爱无伴奏"）
    names = sorted(songs.keys(), key=len, reverse=True)
    for name in names:
        if name not in text:
            continue
        has_sing = bool(re.search(sing_kw, text))
        is_short = len(text.strip()) <= len(name) + 5
        if not (has_sing or is_short):
            continue
        # 默认副歌；明确说"完整"才播全曲；明确说"副歌"强制副歌
        if re.search(full_kw, text):
            mode = "full"
        elif re.search(chorus_kw, text):
            mode = "chorus"
        else:
            mode = "chorus" if "chorus" in songs[name] else "full"
        # 降级保护
        if mode == "chorus" and "chorus" not in songs[name]:
            mode = "full"
        if mode == "full" and "full" not in songs[name]:
            mode = "chorus"
        return name, mode

    # 没匹配到歌名：检查是否是"再唱一遍"类意图
    repeat_kw = r"(再唱|再来|再放|再听|一遍|一次|还唱|继续唱|再给我|重唱)"
    if re.search(repeat_kw, text) and _last_sung["name"]:
        name = _last_sung["name"]
        if name in songs:
            mode = _last_sung["mode"] or ("chorus" if "chorus" in songs[name] else "full")
            if mode == "chorus" and "chorus" not in songs[name]:
                mode = "full"
            return name, mode
    return None


def _cleanup_audio_cache():
    """清理网页版音频缓存：删7天前的文件，且总数超过200个时删最旧的"""
    try:
        now = time.time()
        files = []
        for f in os.listdir(AUDIO_DIR):
            if not f.startswith("cache_"):
                continue
            fp = os.path.join(AUDIO_DIR, f)
            if os.path.isfile(fp):
                if now - os.path.getmtime(fp) > 7 * 86400:
                    os.remove(fp)
                else:
                    files.append((os.path.getmtime(fp), fp))
        # 总数上限 200：按修改时间排序，删最旧的
        if len(files) > 200:
            files.sort()
            for _, fp in files[:-200]:
                try:
                    os.remove(fp)
                except OSError:
                    pass
    except Exception:
        pass


def _cleanup_audio_log():
    """删30天前音频归档子目录；若总大小仍超限，按日期从旧到新删。"""
    import shutil
    MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2GB 上限
    try:
        now = time.time()
        dirs = []
        for d in os.listdir(AUDIO_LOG_DIR):
            dp = os.path.join(AUDIO_LOG_DIR, d)
            if not os.path.isdir(dp):
                continue
            if now - os.path.getmtime(dp) > 30 * 86400:
                shutil.rmtree(dp, ignore_errors=True)
            else:
                dirs.append(dp)
        # 统计剩余总大小，超限则从最旧目录开始删
        def _dir_size(p):
            return sum(os.path.getsize(os.path.join(r, f))
                       for r, _, fs in os.walk(p) for f in fs)
        dirs.sort(key=os.path.getmtime)  # 最旧在前
        total = sum(_dir_size(d) for d in dirs)
        for dp in dirs:
            if total <= MAX_BYTES:
                break
            sz = _dir_size(dp)
            shutil.rmtree(dp, ignore_errors=True)
            total -= sz
    except Exception:
        pass


def synthesize(text, emotion):
    """调 TTS，返回音频 URL。带缓存：相同文本+情绪直接返回旧文件。"""
    import hashlib
    cache_key = hashlib.md5(f"{text}|{emotion}".encode()).hexdigest()
    cache_fn = f"cache_{cache_key}.wav"
    cache_fp = os.path.join(AUDIO_DIR, cache_fn)
    if os.path.exists(cache_fp):
        return f"/static/audio/{cache_fn}"

    t0 = time.time()

    # 复用 brain 的清洗和切句
    clean = brain.clean_text_for_tts(text)
    sentences = re.split(r'(?<=[。！？])', clean)
    sentences = [s.strip().strip("、，。！？!?.,~ ") for s in sentences]
    sentences = [s for s in sentences if re.search(r'[\u4e00-\u9fff a-zA-Z0-9]', s)]

    all_pcm = bytearray()
    params_set = False
    sr = 32000
    for s in sentences:
        path = brain.tts_once(s, emotion)
        if not path:
            continue
        with wave.open(path, 'rb') as wf:
            sr = wf.getframerate()
            all_pcm.extend(wf.readframes(wf.getnframes()))
        params_set = True

    if not params_set:
        return None

    with wave.open(cache_fp, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(all_pcm))

    print(f"[诊断] TTS 合成耗时 {time.time()-t0:.2f}s，文本 {len(text)} 字")
    return f"/static/audio/{cache_fn}"


def synthesize_sentence(sentence, emotion):
    """逐句流式专用：合成单句并缓存，返回音频 URL；失败返回 None。
    与 synthesize() 的区别：不切句不合并，来一句合成一句，首音更快。"""
    import hashlib, shutil
    cache_key = hashlib.md5(f"s|{sentence}|{emotion}".encode()).hexdigest()
    cache_fn = f"cache_{cache_key}.wav"
    cache_fp = os.path.join(AUDIO_DIR, cache_fn)
    if os.path.exists(cache_fp):
        return f"/static/audio/{cache_fn}"
    t0 = time.time()
    try:
        path = brain.tts_once(sentence, emotion)
    except Exception:
        return None
    if not path:
        return None
    try:
        shutil.copyfile(path, cache_fp)
    except Exception:
        return None
    print(f"[诊断] 单句TTS {time.time()-t0:.2f}s：{sentence[:20]}")
    return f"/static/audio/{cache_fn}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/greeting")
def api_greeting():
    """开场白：有历史对话/画像时用老朋友口吻，否则用默认初次见面"""
    try:
        has_history = bool(memory.load_recent(2))
        has_profile = bool(memory.get_profile_text())
        if has_history or has_profile:
            return jsonify({"greeting": "又见面啦。今天过得怎么样，有什么想跟我聊的吗？"})
    except Exception:
        pass
    return jsonify({"greeting": "你好呀，我是纳西妲。有什么想聊的，都可以告诉我哦。"})


@app.route("/api/chat/stream", methods=["POST"])
def api_chat_stream():
    """流式聊天：SSE 逐字推送，完整后合成 TTS"""
    data = request.json
    text = data.get("text", "").strip()
    user_emotion = data.get("user_emotion")  # ASR 传来的用户情绪，用于共情
    if not text:
        return jsonify({"error": "空消息"}), 400

    # 退出主人模式命令（和终端版 /exitmaster 一致）
    if text.lstrip("/") in ("exitmaster", "退出主人"):
        return jsonify({
            "reply": "主人模式是单次生效的，验证一次只管一条回复哦。",
            "emotion": "gentle",
            "audio_url": None,
        })

    if brain.is_hijack_request(text):
        return jsonify({"hijack_required": True, "text": text})

    # 点歌意图：直接返回唱歌音频，不走 LLM
    song = match_song(text)
    if song:
        name, mode = song
        songs = scan_songs()
        fn = songs[name].get("chorus") if mode == "chorus" else songs[name].get("full")
        if fn:
            global _last_sung
            _last_sung = {"name": name, "mode": mode}
            import json as _json
            reply = f"好呀，给你唱《{name}》的副歌段~" if mode == "chorus" else f"好呀，给你唱《{name}》~"
            payload = _json.dumps({"audio_url": f"/music/{fn}", "emotion": "happy", "sing": True, "reply": reply})
            return Response(
                f"event: done\ndata: {payload}\n\n",
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
            )

    def generate():
        full_reply = ""
        emotion = "gentle"
        sent_n = 0            # 成功推送的句子音频数
        stream_ok = True      # 首句合成失败则整体回退到旧的整段合成
        try:
            # 复用终端版验证过的逐句切分（iter_reply_sentences），token 照常透传
            for item in brain.iter_reply_sentences(text, user_emotion=user_emotion):
                if item[0] == "token":
                    full_reply += item[1]
                    yield f"data: {item[1]}\n\n"
                elif item[0] == "emotion":
                    emotion = item[1]
                    yield f"event: emotion\ndata: {emotion}\n\n"
                elif item[0] == "sent":
                    if not stream_ok:
                        continue
                    sent = brain.clean_text_for_tts(item[1]).strip("、，。！？!?.,~ ：:;；")
                    if not re.search(r'[一-鿿 a-zA-Z0-9]', sent):
                        continue
                    url = synthesize_sentence(sent, emotion)
                    if url:
                        sent_n += 1
                        payload = json.dumps({"url": url, "emotion": emotion}, ensure_ascii=False)
                        yield f"event: audio\ndata: {payload}\n\n"
                    elif sent_n == 0:
                        # 首句就失败：放弃逐句，走整段兜底（旧逻辑）
                        stream_ok = False
                    else:
                        yield "event: hint\ndata: 有一句语音合成失败，跳过这句啦\n\n"
                elif item[0] == "done":
                    full_reply = item[1]
                    emotion = item[2]
        except Exception as e:
            yield f"event: error\ndata: {str(e)}\n\n"
            return

        if stream_ok and sent_n > 0:
            # 逐句模式成功：done 不带整段音频，前端靠 audio 事件队列播放
            yield f"event: done\ndata: {json.dumps({'audio_url': None, 'emotion': emotion, 'streamed': True})}\n\n"
        else:
            # 回退路径：整段合成（保持原有行为）
            audio_url = synthesize(full_reply, emotion) if full_reply else None
            yield f"event: done\ndata: {json.dumps({'audio_url': audio_url, 'emotion': emotion})}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/password", methods=["POST"])
def api_password():
    data = request.json
    pw = data.get("password", "")
    text = data.get("text", "")
    if pw != brain.MASTER_PASSWORD:
        return jsonify({"error": "密码不对"}), 403
    reply, emotion = brain.chat(text, force_master=True)
    audio_url = synthesize(reply, emotion) if reply else None
    return jsonify({"reply": reply, "emotion": emotion, "audio_url": audio_url})


@app.route("/api/asr/status")
def api_asr_status():
    """前端查询 ASR 模型是否已加载，用于显示首次加载提示"""
    return jsonify({"loaded": _asr is not None})


@app.route("/api/memory")
def api_memory():
    """记忆面板（只读）：返回用户画像 + 最近对话"""
    import sqlite3
    try:
        if not os.path.exists(memory.DB_PATH):
            return jsonify({"profile": [], "recent": []})
        conn = sqlite3.connect(memory.DB_PATH)
        try:
            rows = conn.execute(
                "SELECT key, value, updated_at FROM user_profile ORDER BY updated_at DESC"
            ).fetchall()
            profile = [{"key": k, "value": v, "updated": t} for k, v, t in rows]
            convs = conn.execute(
                "SELECT role, content, emotion, datetime(created_at, 'localtime') FROM conversations "
                "WHERE user_id = 'default' ORDER BY id DESC LIMIT 40"
            ).fetchall()
            recent = [{"role": r, "content": c, "emotion": e, "time": t}
                      for r, c, e, t in reversed(convs)]
        finally:
            conn.close()
        return jsonify({"profile": profile, "recent": recent})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reminders/due")
def api_reminders_due():
    """前端轮询：返回到点且未播报的提醒（含语音），随即标记为已播报"""
    rr = getattr(brain, "reminder", None)
    if rr is None:
        return jsonify({"reminders": []})
    try:
        due = rr.due_reminders()
    except Exception:
        return jsonify({"reminders": []})
    out = []
    for r in due:
        sent = r.get("sentence", "")
        audio_url = synthesize(sent, "gentle") if sent else None
        out.append({"task": r.get("task", ""), "sentence": sent, "audio_url": audio_url})
    return jsonify({"reminders": out})


@app.route("/music/<path:filename>")
def music_file(filename):
    """提供 music 目录下的音频文件访问"""
    return send_from_directory(MUSIC_DIR, filename)


@app.route("/api/songs")
def api_songs():
    """返回已学会的歌曲列表"""
    songs = scan_songs()
    result = []
    for name, files in songs.items():
        result.append({
            "name": name,
            "has_full": "full" in files,
            "has_chorus": "chorus" in files,
        })
    return jsonify({"songs": result})


@app.route("/api/sing", methods=["POST"])
def api_sing():
    """点歌：接收歌名和模式，返回音频 URL"""
    data = request.json or {}
    name = data.get("name", "").strip()
    mode = data.get("mode", "full")
    if not name:
        return jsonify({"error": "歌名不能为空"}), 400
    songs = scan_songs()
    if name not in songs:
        return jsonify({"error": f"纳西妲还不会唱《{name}》哦"}), 404
    if mode == "chorus" and "chorus" in songs[name]:
        fn = songs[name]["chorus"]
    elif "full" in songs[name]:
        fn = songs[name]["full"]
    else:
        return jsonify({"error": "没有可用的音频"}), 404
    return jsonify({
        "name": name,
        "mode": mode,
        "audio_url": f"/music/{fn}",
        "reply": f"好的，给你唱《{name}》{'的副歌' if mode == 'chorus' else ''}~" if mode == "full" else f"给你唱《{name}》最火的副歌段~",
    })


@app.route("/api/asr", methods=["POST"])
def api_asr():
    if "audio" not in request.files:
        return jsonify({"error": "没有音频"}), 400
    f = request.files["audio"]
    ts = int(time.time())
    webm_path = os.path.join(tempfile.gettempdir(), f"mic_{ts}.webm")
    f.save(webm_path)

    # 转 wav 16k 单声道（用 ffmpeg）
    wav_path = os.path.join(tempfile.gettempdir(), f"mic_{ts}.wav")
    import subprocess
    ffmpeg = r"E:\fariy\GPT-SoVITS\GPT-SoVITS-v2pro-20261006\runtime\ffmpeg.exe"
    subprocess.run([ffmpeg, "-y", "-i", webm_path, "-ar", "16000", "-ac", "1", wav_path],
                   capture_output=True)

    try:
        model = get_asr()
        res = model.generate(input=wav_path, hotword=mic_input.HOTWORDS)
        raw = res[0]["text"]

        # 提取用户情绪标签（SenseVoice 的 <|HAPPY|> 等），用于共情
        user_emotion = None
        for tag in re.findall(r"<\|([A-Z]+)\|>", raw):
            if tag in mic_input._USER_EMOTION_MAP:
                user_emotion = mic_input._USER_EMOTION_MAP[tag]
                break
        text = re.sub(r"<\|[^|]+\|>", "", raw).strip()
        text = mic_input.correct_text(text)
        return jsonify({"text": text, "user_emotion": user_emotion})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        for p in (webm_path, wav_path):
            try:
                os.remove(p)
            except OSError:
                pass


if __name__ == "__main__":
    print("=" * 50)
    print("纳西妲网页版启动中...")
    _cleanup_audio_cache()
    _cleanup_audio_log()
    print("ASR 已改为懒加载：首次点麦克风时才加载模型（省1G显存）")
    print("浏览器打开: http://127.0.0.1:5000")
    print("=" * 50)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
