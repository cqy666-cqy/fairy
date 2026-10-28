# -*- coding: utf-8 -*-
import sounddevice as sd
import numpy as np

# 设备9是 WASAPI，查它的默认采样率
info = sd.query_devices(9)
print(f"设备9: {info['name']}")
print(f"默认采样率: {info['default_samplerate']}")
sr = int(info['default_samplerate'])

try:
    data = sd.rec(int(sr * 0.5), samplerate=sr, channels=1, dtype='float32', device=9)
    sd.wait()
    vol = float(np.sqrt(np.mean(data**2)))
    print(f'WASAPI 录音成功! 采样率={sr}, 音量={vol:.4f}')
except Exception as e:
    print(f'WASAPI 失败: {e}')

# 也试试设备1用不同采样率
for try_sr in [44100, 48000]:
    try:
        data = sd.rec(int(try_sr * 0.3), samplerate=try_sr, channels=1, dtype='float32', device=1)
        sd.wait()
        vol = float(np.sqrt(np.mean(data**2)))
        print(f'MME 设备1 @{try_sr}Hz 成功! 音量={vol:.4f}')
        break
    except Exception as e:
        print(f'MME 设备1 @{try_sr}Hz 失败: {str(e)[:80]}')
