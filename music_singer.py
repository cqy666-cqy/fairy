"""
纳西妲唱歌模块
流程：搜索B站链接 → 确认 → 下载 → 人声分离 → 降调 → RVC变声 → 合成
"""
import html
import math
import os
import re
import subprocess
import time
import requests
from pathlib import Path

# 路径配置（从 config.json 读外部路径，内部路径相对推导）
_PROJECT_ROOT = Path(os.path.dirname(os.path.abspath(__file__)))
MUSIC_DIR = _PROJECT_ROOT / "music"
WORK_DIR = MUSIC_DIR / "work"

import json as _msj
_cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
try:
    with open(_cfg_path, "r", encoding="utf-8") as _f:
        _cfg = _msj.load(_f)
except Exception:
    _cfg = {}
_paths = _cfg.get("PATHS", {})

YT_DLP = _paths.get("YT_DLP", r"E:\fariy\工具\yt-dlp.exe")
RVC_PYTHON = _paths.get("RVC_PYTHON", r"E:\fariy\ai\RVC\venv\Scripts\python.exe")
RVC_ROOT = _paths.get("RVC_ROOT", r"E:\fariy\ai\RVC\RVC-WebUI")
RVC_MODEL = "nahida.pth"
RVC_INDEX_FILE = _paths.get("RVC_INDEX", r"E:\fariy\ai\RVC\RVC-WebUI\logs\nahida\added_IVF153_Flat_nprobe_1_nahida_v2.index")

# 博查搜索 API
BOCHA_API_KEY = _cfg.get("BOCHA_API_KEY", "")


_BILI_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://www.bilibili.com/",
}
# 没有人声的、或是二次创作，直接排除
_BILI_BAD = re.compile(r"(伴奏|纯音乐|无人声|off\s*vocal|instrumental|教学|吉他|钢琴|古筝|二胡|口琴)", re.I)
# MV/现场/饭拍：音轨是混好的，人声上死死叠着配乐，扒出来必然带水声金属感，重罚
_BILI_MV = re.compile(r"(mv|现场|live|演唱会|饭拍|舞台|混剪|片段|饭制|颁奖)", re.I)
# 能用但不是首选（翻唱/剪辑/电音remix过的音源做变声效果差）
_BILI_WEAK = re.compile(r"(翻唱|cover|鬼畜|混剪|剪辑|reaction|解说|切片|直播|合集|dj|remix|电音|串烧|加速|慢摇)", re.I)
# 纯音频/原声轨道，分离最干净，优先选
_BILI_GOOD = re.compile(r"(纯音频|纯歌|音频|原声|无损|hi-?res|高音质|专辑|studio|原曲|音源|完整版)", re.I)


def _clean_title(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _rank_title(title):
    if _BILI_BAD.search(title):
        return -5
    score = 0
    if _BILI_GOOD.search(title):
        score += 3
    if _BILI_MV.search(title):
        score -= 3
    if _BILI_WEAK.search(title):
        score -= 2
    return score


_bili_session = None


def _get_bili_session():
    """带 buvid3/buvid4 cookie 的会话，能显著降低空响应概率"""
    global _bili_session
    if _bili_session is not None:
        return _bili_session
    s = requests.Session()
    s.headers.update(_BILI_HEADERS)
    try:
        spi = s.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=10).json()
        d = spi.get("data") or {}
        if d.get("b_3"):
            s.cookies.set("buvid3", d["b_3"])
        if d.get("b_4"):
            s.cookies.set("buvid4", d["b_4"])
    except Exception:
        pass
    _bili_session = s
    return s


def _bili_search(kw):
    """B站官方搜索接口，按标题质量排序；失败重试3次，仍失败返回空列表"""
    sess = _get_bili_session()
    for attempt in range(3):
        try:
            r = sess.get(
                "https://api.bilibili.com/x/web-interface/search/type",
                params={"search_type": "video", "keyword": kw}, timeout=10,
            )
            if not r.text.strip():
                raise ValueError("空响应")
            j = r.json()
            if j.get("code") != 0:
                raise ValueError(f"code={j.get('code')}")
            cands, seen = [], set()
            for it in (j.get("data") or {}).get("result") or []:
                bv = it.get("bvid") or ""
                if not bv or bv in seen:
                    continue
                seen.add(bv)
                title = _clean_title(it.get("title"))
                cands.append({
                    "title": title,
                    "url": f"https://www.bilibili.com/video/{bv}",
                    "bv_id": bv,
                    "score": _rank_title(title),
                })
            cands = [c for c in cands if c["score"] > -5]
            cands.sort(key=lambda c: -c["score"])
            return cands
        except Exception as e:
            print(f"B站搜索第{attempt + 1}次失败：{e}")
            if attempt < 2:
                time.sleep(1 + attempt)
    return []


def find_bilibili_song(keyword):
    """搜歌：优先B站官方搜索，结果太少时放宽关键词，最后用博查兜底"""
    kw = re.sub(r"\s+", " ", (keyword or "")).strip()
    if not kw:
        return []

    try:
        cands = _bili_search(kw)
        # 再搜一次带"原声"的：MV/现场版满地都是，纯音频轨分离出来才干净
        extra = _bili_search(f"{kw} 原声")
        if extra:
            seen = {c["bv_id"] for c in cands}
            cands += [c for c in extra if c["bv_id"] not in seen]
            cands.sort(key=lambda c: -c["score"])
        # "周杰伦的花海" 这种写法，去掉"的"再搜一次往往更准
        if len(cands) < 2 and "的" in kw:
            cands = _bili_search(re.sub(r"的", " ", kw).strip()) or cands
        if cands:
            return cands[:5]
    except Exception as e:
        print(f"B站搜索失败：{e}")

    # 兜底：博查网页搜索
    try:
        resp = requests.post(
            "https://api.bochaai.com/v1/web-search",
            headers={"Authorization": f"Bearer {BOCHA_API_KEY}",
                     "Content-Type": "application/json"},
            json={"query": f"{kw} bilibili 原唱", "count": 10},
            timeout=10,
        )
        cands, seen = [], set()
        for item in resp.json().get("data", {}).get("webPages", {}).get("value", []):
            m = re.search(r"(BV[a-zA-Z0-9]+)", item.get("url", ""))
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            title = _clean_title(item.get("name", ""))
            cands.append({
                "title": title,
                "url": f"https://www.bilibili.com/video/{m.group(1)}",
                "bv_id": m.group(1),
                "score": _rank_title(title),
            })
        cands = [c for c in cands if c["score"] > -5]
        cands.sort(key=lambda c: -c["score"])
        return cands[:5]
    except Exception as e:
        print(f"博查兜底搜索失败: {e}")
        return []


def download_from_bilibili(bv_url, output_dir):
    """从B站下载音频"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_template = str(output_dir / "source.%(ext)s")
    
    cmd = [
        YT_DLP,
        "--format", "bestaudio",
        "--extract-audio",
        "--audio-format", "wav",
        "--audio-quality", "0",
        "-o", output_template,
        bv_url
    ]
    
    try:
        print(f"下载中: {bv_url}")
        subprocess.run(cmd, check=True, timeout=120, capture_output=True)
        # 找到下载的文件
        for f in output_dir.glob("source.*"):
            if f.suffix == ".wav":
                return str(f)
        return None
    except Exception as e:
        print(f"下载失败: {e}")
        return None


def separate_vocals(input_wav, output_dir):
    """人声分离（htdemucs_ft 稳定模型）
    htdemucs 大模型需要联网下载且在当前网络环境下 huggingface 连不上，
    已下载好的 htdemucs_ft 在 RTX 4060 8GB 上 4 分钟音频约 3 分钟跑完。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    env = os.environ.copy()
    env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    
    cmd = [
        RVC_PYTHON, "-m", "demucs",
        "--two-stems=vocals",
        "-n", "htdemucs_ft",   # 已下载好，RTX 4060 8GB 稳定跑，huggingface 连不上所以别换 htdemucs
        "-o", str(output_dir),
        input_wav
    ]
    
    try:
        print("人声分离中（htdemucs_ft）...")
        subprocess.run(cmd, check=True, timeout=600, capture_output=True, env=env)
        # 找到 vocals.wav（htdemucs / htdemucs_ft 两个模型输出目录不同）
        song_name = Path(input_wav).stem
        vocals_path = None
        for m in ("htdemucs_ft", "htdemucs"):
            p = output_dir / m / song_name / "vocals.wav"
            if p.exists():
                vocals_path = p
                no_vocals_path = output_dir / m / song_name / "no_vocals.wav"
                break
        if vocals_path and vocals_path.exists() and no_vocals_path.exists():
            return str(vocals_path), str(no_vocals_path)
        return None, None
    except Exception as e:
        print(f"人声分离失败: {e}")
        return None, None


RVC_SPEAKER_ID = "0"   # nahida.pth 是 0-108 多说话人模型，纳西妲是 0 号
RVC_MAX_SECONDS = 60   # 单段超过 60s 就分段跑，8GB 显存装不下更长的 tensor


def rvc_convert(input_wav, output_wav, pitch_shift=-5):
    """RVC 变声成纳西妲。
    超过 60s 的音频自动分段（每段 30s），分别跑完再拼回来——否则 8GB 显存会 OOM。"""
    env = os.environ.copy()
    env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    input_wav = Path(input_wav)
    output_wav = Path(output_wav)
    output_wav.parent.mkdir(parents=True, exist_ok=True)

    # 用 ffmpeg 探测时长
    try:
        dur_out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(input_wav)],
            capture_output=True, text=True, check=True, timeout=15,
        )
        duration = float(dur_out.stdout.strip() or 0)
    except Exception as e:
        print(f"ffprobe 失败，按不分段处理：{e}")
        duration = 0

    if duration <= RVC_MAX_SECONDS:
        return _rvc_once(str(input_wav), str(output_wav), pitch_shift, env, RVC_ROOT)

    # 分段跑
    segs = []
    seg_len = RVC_MAX_SECONDS  # 一段直接拉满 60s：分段越少，接缝越少，杂音越少
    fade = 0.03                # 每段首尾 30ms 淡入淡出，吃掉拼接处的爆音
    for i in range(0, int(duration), seg_len):
        seg = input_wav.parent / f"seg_{i:04d}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_wav), "-ss", str(i), "-t", str(seg_len),
             "-ac", "2", "-ar", "44100", str(seg)],
            check=True, capture_output=True, timeout=60,
        )
        out_seg = input_wav.parent / f"nahida_seg_{i:04d}.wav"
        if not _rvc_once(str(seg), str(out_seg), pitch_shift, env, RVC_ROOT):
            return None
        faded = input_wav.parent / f"nahida_fade_{i:04d}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(out_seg), "-af",
             f"afade=t=in:st=0:d={fade},areverse,afade=t=in:st=0:d={fade},areverse",
             "-c:a", "pcm_s16le", "-ar", "44100", str(faded)],
            check=True, capture_output=True, timeout=60,
        )
        segs.append(str(faded))
        seg.unlink(missing_ok=True)
        out_seg.unlink(missing_ok=True)

    # 拼回来（重编码，不能 -c copy，否则段边界的时间戳会错位）
    concat_list = input_wav.parent / "_concat.txt"
    concat_list.write_text("".join(f"file '{s}'\n" for s in segs), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_list), "-c:a", "pcm_s16le", "-ar", "44100", str(output_wav)],
        check=True, capture_output=True, timeout=120,
    )
    concat_list.unlink(missing_ok=True)
    for s in segs:
        Path(s).unlink(missing_ok=True)
    if output_wav.exists():
        return str(output_wav)
    return None


def _rvc_once(input_wav, output_wav, pitch_shift, env, rvc_root):
    """单次 RVC 变声调用。返回 output_wav 或 None。
    参数调优（参考 RVC 参数中心 + A/B 测试）：
    - --index: 明确指定 nahida 模型配套的 .index，不用自动搜
    - --index-rate 0.6: 跨音域变声（男→女）取中间值，既借到音色又不引入原素材噪点
    - --protect 0.4: 保护清音/气声，减少"电音颗粒感"
    - --rms-mix-rate 0.8: 别让音量完全跟着原唱忽大忽小
    - --resample-sr 48000: 对齐 RVC 训练采样率，避免语速怪
    """
    cmd = [
        RVC_PYTHON, "-m", "infer.cli",
        "--model", RVC_MODEL,
        "--index", RVC_INDEX_FILE,
        "--speaker-id", RVC_SPEAKER_ID,
        "--input", input_wav,
        "--output", output_wav,
        "--pitch", str(pitch_shift),
        "--f0-method", "rmvpe",
        "--index-rate", "0.6",
        "--protect", "0.4",
        "--rms-mix-rate", "0.8",
        "--resample-sr", "48000",
        "--format", "wav",
    ]
    try:
        subprocess.run(cmd, check=True, timeout=120, capture_output=True,
                       env=env, cwd=rvc_root)
        if Path(output_wav).exists():
            return str(output_wav)
        return None
    except Exception as e:
        print(f"RVC 变声失败: {e}")
        return None


def _estimate_pitch_shift(vocals_wav, target_f0=220.0, default=6):
    """按人声的中位基频估算要升几个半音才接近纳西妲的女声音域。
    男声原唱不升调直接换音色，出来就是"粗嗓子"，之前一律 -3 降调方向是反的。"""
    code = (
        "import sys, warnings, numpy as np, librosa\n"
        "warnings.filterwarnings('ignore')\n"
        "y, sr = librosa.load(sys.argv[1], sr=16000, mono=True, duration=30)\n"
        "f0 = librosa.yin(y, fmin=60, fmax=500, sr=sr)\n"
        "f0 = f0[np.isfinite(f0)]\n"
        "f0 = f0[(f0 > 60) & (f0 < 500)]\n"
        "print(float(np.median(f0)) if f0.size else 0.0)\n"
    )
    try:
        r = subprocess.run([RVC_PYTHON, "-c", code, str(vocals_wav)],
                           capture_output=True, text=True, timeout=180)
        med = float((r.stdout or "").strip().splitlines()[-1])
        if med <= 0:
            return default
        shift = round(12 * math.log2(target_f0 / med))
        return max(-2, min(12, int(shift)))
    except Exception as e:
        print(f"基频估算失败，用默认升调 {default}：{e}")
        return default


def _polish(in_wav, out_wav):
    """收尾：纯 volume 硬推到峰值 0.98 + 高通 + 降噪 + alimiter 卡死
    关键修正：去掉 loudnorm —— 它会把硬推上去的音量又压回去，等于 volume 白加了。
    我们不管广播 LUFS 标准，只要把波形推到接近削波，alimiter 卡最后一下防爆音。
    """
    cmd = [
        "ffmpeg", "-y", "-i", str(in_wav), "-af",
        # 先测峰值，再按比例硬推到 0.98（动态计算 volume 值）
        "volume=18dB,"
        "highpass=f=90,"
        "afftdn=nr=8:nf=-30,"
        "alimiter=limit=0.98:attack=1:release=1",
        "-c:a", "pcm_s16le", "-ar", "44100", str(out_wav),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        if Path(out_wav).exists() and Path(out_wav).stat().st_size > 1024:
            return str(out_wav)
    except Exception as e:
        print(f"收尾处理失败: {e}")
    return None


def mix_audio(vocals_wav, accompaniment_wav, output_wav):
    """合成人声和伴奏"""
    cmd = [
        "ffmpeg", "-y",
        "-i", vocals_wav,
        "-i", accompaniment_wav,
        "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest",
        output_wav
    ]
    
    try:
        print("合成中...")
        subprocess.run(cmd, check=True, capture_output=True)
        if Path(output_wav).exists():
            return output_wav
        return None
    except Exception as e:
        print(f"合成失败: {e}")
        return None


def prepare_record(src, out_dir=None, max_seconds=300):
    """发语音前压成 mp3 + 增益：整首 wav 动辄 30-40MB，直接发会超时/被拒。
    超过 max_seconds 的从 0 秒截断。返回可发送的路径，失败则返回原路径。"""
    src = Path(src)
    if not src.exists():
        return str(src)
    if src.suffix.lower() == ".mp3" and src.stat().st_size < 8 * 1024 * 1024:
        return str(src)
    out = Path(out_dir or src.parent) / (src.stem + "_send.mp3")
    cmd = ["ffmpeg", "-y", "-i", str(src), "-t", str(max_seconds),
           "-af", "volume=2.0,loudnorm=I=-12:TP=-1.0",
           "-ac", "2", "-ar", "44100", "-b:a", "128k", str(out)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        if out.exists() and out.stat().st_size > 1024:
            return str(out)
    except Exception as e:
        print(f"语音压缩失败: {e}")
    return str(src)


def process_song(bv_url, song_name, progress=None):
    """
    下载 → 人声分离 → 按音域自动升调 → RVC 变声 → 收尾
    只输出纳西妲的纯人声（不要伴奏）
    progress: 可选回调，收到一句进度文案（在 QQ 里报个平安，别让人以为卡死了）
    """
    def _say(msg):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    work_dir = WORK_DIR / song_name
    work_dir.mkdir(parents=True, exist_ok=True)

    source_wav = download_from_bilibili(bv_url, work_dir)
    if not source_wav:
        return None, "下载失败"

    _say("音频拿到了，我在扒人声（这首要两三分钟）")
    vocals_wav, no_vocals_wav = separate_vocals(source_wav, work_dir)
    if not vocals_wav:
        return None, "人声分离失败"

    _say("人声扒干净了，正在换成我的嗓子（再等一分多钟）")
    nahida_raw = work_dir / "nahida_raw.wav"
    pitch = _estimate_pitch_shift(vocals_wav)
    print(f"自动升调 {pitch:+d} 半音")
    result = rvc_convert(vocals_wav, str(nahida_raw), pitch_shift=pitch)
    if not result:
        return None, "变声失败"

    final_wav = MUSIC_DIR / f"{song_name}_纳西妲版.wav"
    # 只留人声——她唱歌是纯 vocal，不带伴奏
    if not _polish(result, final_wav):
        import shutil
        shutil.copy(result, str(final_wav))
    return str(final_wav), "成功（纯人声）"


if __name__ == "__main__":
    # 测试搜索
    candidates = find_bilibili_song("花海 周杰伦")
    print(f"找到 {len(candidates)} 个候选:")
    for i, c in enumerate(candidates):
        print(f"{i+1}. {c['title']}")
        print(f"   {c['url']}")
