# -*- coding: utf-8 -*-
import sounddevice as sd
import numpy as np

for dev_id in [1, 5, 9, 12, 13, 15]:
    try:
        info = sd.query_devices(dev_id)
        print(f'设备 {dev_id}: {info["name"]}')
        data = sd.rec(int(16000 * 0.3), samplerate=16000, channels=1, dtype='float32', device=dev_id)
        sd.wait()
        vol = float(np.sqrt(np.mean(data**2)))
        print(f'  -> 成功! 音量={vol:.4f}')
        break
    except Exception as e:
        print(f'  -> 失败: {str(e)[:100]}')
