# -*- coding: utf-8 -*-
# 麦克风录音 + SenseVoice ASR
import os, sys, time, tempfile, wave

# 指定 modelscope 模型缓存在 E 盘（必须在 import funasr 之前设置）
os.environ["MODELSCOPE_CACHE"] = r"E:\fariy\ai\naxida_ai\models\modelscope_cache"

import numpy as np
import sounddevice as sd

# 启动时加载 SenseVoice（复用之前下好的模型）
_asr_model = None
_last_use_time = 0.0
_ASR_IDLE_TIMEOUT = 600  # 空闲 10 分钟自动卸载，释放显存/内存

def get_asr_model():
    global _asr_model, _last_use_time
    if _asr_model is None:
        os.chdir(r"E:\fariy\GPT-SoVITS\GPT-SoVITS-v2pro-20261006")
        sys.path.insert(0, os.getcwd())
        from funasr import AutoModel
        print("【麦克风模块：加载 SenseVoice ASR...】")
        _asr_model = AutoModel(model="iic/SenseVoiceSmall", device="cuda", disable_update=True)
        print("【麦克风模块：ASR 就绪。】")
    _last_use_time = time.time()
    return _asr_model


def maybe_unload_asr():
    """空闲超时则卸载 ASR，把显存/内存还给系统"""
    global _asr_model
    if _asr_model is not None and time.time() - _last_use_time > _ASR_IDLE_TIMEOUT:
        print("【麦克风模块：空闲超时，卸载 ASR 释放资源】")
        import gc
        import torch
        del _asr_model
        _asr_model = None
        gc.collect()
        torch.cuda.empty_cache()


def record_until_silence(sample_rate=16000, silence_sec=1.5, max_sec=15, threshold=0.01):
    """按回车开始录音，检测到静音 silence_sec 秒自动停止。返回 wav 路径。"""
    print("（按回车开始说话，说完停一下自动识别）")
    input()

    frames = []
    started = False
    silence_start = None
    t0 = time.time()

    def callback(indata, frames_count, time_info, status):
        frames.append(indata.copy())

    with sd.InputStream(samplerate=sample_rate, channels=1, dtype='float32',
                        blocksize=int(sample_rate * 0.1), callback=callback):
        print("录音中...（说完等一下）")
        while True:
            time.sleep(0.1)
            if not frames:
                continue
            recent = np.concatenate(frames[-3:]) if len(frames) >= 3 else frames[-1]
            volume = float(np.sqrt(np.mean(recent ** 2)))
            if not started and volume > threshold:
                started = True
            if started:
                if volume < threshold:
                    if silence_start is None:
                        silence_start = time.time()
                    elif time.time() - silence_start > silence_sec:
                        break
                else:
                    silence_start = None
            if time.time() - t0 > max_sec:
                break

    if not frames:
        return None
    audio = np.concatenate(frames).flatten()
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767).astype(np.int16)

    tmp = os.path.join(tempfile.gettempdir(), f"mic_{int(time.time())}.wav")
    with wave.open(tmp, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return tmp


# 热词列表（原神专有名词）
HOTWORDS = "纳西妲 小吉祥草王 布耶尔 大慈树王 须弥 草神 原神 提瓦特 丘丘人 七神 教令院 贤者 净善宫 世界树 虚空 神之心 神之眼 枣椰蜜糖 树莓薄荷饮 艾尔海森 赛诺 提纳里 妮露 散兵 流浪者 珐露珊 卡维 迪希雅 莱依拉 花神 赤王 帕蒂沙兰 劫波莲 禁忌知识 死之执政 冰之女皇 愚人众 旅行者 派蒙 天理 坎瑞亚 深渊 地脉 魔鳞病 花神诞祭 魔神战争 蒙德 璃月 稻妻 枫丹 纳塔 温迪 钟离 雷电影 芙宁娜 玛薇卡 神里绫华 神里绫人 雷电将军 八重神子 枫原万叶 宵宫 胡桃 魈 甘雨 申鹤 夜兰 白术 达达利亚 阿蕾奇诺 仆人 少女 队长 博士 丑角 公鸡 散兵 女士 公子 木偶 富人 潘塔罗涅 桑多涅 卡皮塔诺 多托雷 哥伦比娅 普契涅拉 皮耶罗 阿贝多 可莉 凯亚 迪卢克 琴 芭芭拉 莫娜 菲谢尔 班尼特 诺艾尔 砂糖 罗莎莉亚 优菈 烟绯 辛焱 北斗 凝光 刻晴 七七 香菱 行秋 重云 云堇 瑶瑶 嘉明 闲云 林尼 琳妮特 菲米尼 那维莱特 莱欧斯利 夏洛蒂 夏沃蕾 千织 艾梅莉埃 基尼奇 玛拉妮 卡齐娜 希诺宁 欧洛伦 恰斯卡 伊安珊 瓦雷莎 丝柯克 梦见月瑞希"

# 后处理纠正规则（同音字）
CORRECTIONS = [
    (r"[纳娜那拿]\s*[是希喜西斯些大小达妲cCdDgGsSxXzZ]\s*[大小达妲cCdDgGsSxXzZ]", "纳西妲"),
    (r"[无五武][只职指值纸][转传专][身生神声]", "无职转生"),
    (r"激\s*速", "激素"),
    (r"至[东冬]", "至冬"),
    (r"执[政症]", "执政"),
    (r"[大太]慈[树术竖数]王", "大慈树王"),
    (r"[禁尽近]忌[知芝]士", "禁忌知识"),
    (r"[禁尽近]忌[只之]识", "禁忌知识"),
    (r"死之[执治]症", "死之执政"),
    (r"冰之[女吕][皇黄煌]", "冰之女皇"),
    (r"[愚于余][人仁中众]众?", "愚人众"),
    (r"旅[行刑形]者", "旅行者"),
    (r"派[蒙盟萌]", "派蒙"),
    (r"天理", "天理"),  # 通常识别准确，暂不纠正
    (r"[砍坎][瑞锐]亚", "坎瑞亚"),
    (r"[深申]冤", "深渊"),
    (r"地[卖脉]", "地脉"),
    (r"魔[鳞林]病", "魔鳞病"),
    (r"花神[淡诞]祭", "花神诞祭"),
    # 七国
    (r"[蒙萌]德", "蒙德"),
    (r"[璃黎里][月岳]", "璃月"),
    (r"[稻到][妻凄七]", "稻妻"),
    (r"[枫风封]丹", "枫丹"),
    (r"[纳那]塔", "纳塔"),
    # 主要角色（高频易错）
    (r"[温文][迪笛]", "温迪"),
    (r"[钟中][离黎]", "钟离"),
    (r"雷[电店][影影]", "雷电影"),
    (r"[芙福扶]宁娜", "芙宁娜"),
    (r"[玛马]薇卡", "玛薇卡"),
    (r"神里[绫灵]华", "神里绫华"),
    (r"神里[绫灵]人", "神里绫人"),
    (r"八重[神申]子", "八重神子"),
    (r"枫原[万玩]叶", "枫原万叶"),
    (r"[宵消肖]宫", "宵宫"),
    (r"[胡湖]桃", "胡桃"),
    (r"[甘干]雨", "甘雨"),
    (r"[申身][鹤贺]", "申鹤"),
    (r"夜[兰蓝]", "夜兰"),
    (r"[达达][利力]亚", "达达利亚"),
    (r"阿[蕾雷]奇[诺落]", "阿蕾奇诺"),
    (r"那[维为]莱特", "那维莱特"),
    (r"[莱来]欧[斯斯]利", "莱欧斯利"),
    (r"[希西][诺落]宁", "希诺宁"),
    (r"[恰卡][斯斯]卡", "恰斯卡"),
    # 组织/概念
    (r"[深申]渊[教叫]团", "深渊教团"),
    (r"[冒帽]险家协会", "冒险家协会"),
    (r"神之[眼演]", "神之眼"),
    (r"神之[心新]", "神之心"),
    (r"[元原]素[力立]", "元素力"),
    (r"[梦孟]境", "梦境"),
    (r"小吉[祥羊]草王", "小吉祥草王"),
    (r"须[弥迷]", "须弥"),
    (r"[元原圆袁][神身生申]", "原神"),
    (r"提[沃握瓦特]特?", "提瓦特"),
    (r"[丘秋丘][丘秋丘][人仁]", "丘丘人"),
    (r"叫宁[愿院]", "教令院"),
    (r"净善[公宫]", "净善宫"),
    (r"世界[数树]", "世界树"),
    (r"散[兵冰]", "散兵"),
    (r"艾尔海[深森]", "艾尔海森"),
    (r"赛[诺落]", "赛诺"),
    (r"提[纳那]里", "提纳里"),
    (r"妮[露路]", "妮露"),
    (r"法[露露]珊", "珐露珊"),
    (r"卡[维唯]", "卡维"),
    (r"迪希[雅亚]", "迪希雅"),
    (r"莱依[拉啦]", "莱依拉"),
    (r"帕蒂沙[兰蓝]", "帕蒂沙兰"),
    (r"劫波[莲连]", "劫波莲"),
]


def correct_text(text):
    """后处理纠正专有名词"""
    import re
    for pat, rep in CORRECTIONS:
        text = re.sub(pat, rep, text)
    return text


# SenseVoice 能识别出的说话人情绪 → 中文描述
_USER_EMOTION_MAP = {
    "HAPPY": "开心", "SAD": "难过", "ANGRY": "生气",
    "FEARFUL": "害怕", "DISGUSTED": "厌恶", "SURPRISED": "惊讶",
}


def listen_once():
    """录一段并 ASR，返回 (文字, 用户情绪或None)。失败返回 ("", None)。"""
    maybe_unload_asr()
    wav = record_until_silence()
    if not wav:
        return "", None
    try:
        model = get_asr_model()
        res = model.generate(input=wav, hotword=HOTWORDS)
        raw = res[0]["text"]
        import re
        # 先把情绪标签捡出来，再统一清洗所有 <|...|> 标签
        user_emotion = None
        for tag in re.findall(r"<\|([A-Z]+)\|>", raw):
            if tag in _USER_EMOTION_MAP:
                user_emotion = _USER_EMOTION_MAP[tag]
                break
        text = re.sub(r"<\|[^|]+\|>", "", raw).strip()
        text = correct_text(text)
        return text, user_emotion
    except Exception as e:
        print(f"ASR 失败：{e}")
        return "", None


if __name__ == "__main__":
    while True:
        t, emo = listen_once()
        print(f"识别到：{t}（情绪：{emo}）")
        if not t:
            break
