import sounddevice as sd
print('默认输入设备:', sd.query_devices(kind='input')['name'])
print('输入设备列表:')
for i, d in enumerate(sd.query_devices()):
    if d['max_input_channels'] > 0:
        print(f"  [{i}] {d['name']} (in={d['max_input_channels']})")
