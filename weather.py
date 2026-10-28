# -*- coding: utf-8 -*-
"""天气查询：Open-Meteo 免 key、免注册，30 分钟缓存。

只覆盖常用城市（南昌读书 / 高安老家），查不到城市就不注入，避免瞎答。
"""
import json
import time
import urllib.request

DEFAULT_CITY = "南昌"
CITIES = {"南昌": (28.68, 115.86), "高安": (28.42, 115.37)}

_TTL = 1800          # 30 分钟内复用同一份结果，避免频繁请求
_cache = {}          # city -> (时间戳, 描述文本)

# WMO 天气代码 → 中文
_WMO = {
    0: "晴", 1: "少云", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "强阵雨", 82: "暴雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷雨", 96: "雷雨伴冰雹", 99: "强雷雨伴冰雹",
}


def _fetch(city):
    lat, lon = CITIES[city]
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"
        "&timezone=Asia%2FShanghai&forecast_days=2"
    )
    with urllib.request.urlopen(url, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def get_weather(city=None):
    """返回一句可直接说出口的天气描述；查询失败返回空字符串"""
    city = city or DEFAULT_CITY
    if city not in CITIES:
        return ""
    hit = _cache.get(city)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    try:
        data = _fetch(city)
        cur = (data.get("current") or {})
        daily = (data.get("daily") or {})
        tmax = daily.get("temperature_2m_max") or []
        tmin = daily.get("temperature_2m_min") or []
        code = daily.get("weather_code") or []
        pop = daily.get("precipitation_probability_max") or []

        def wd(i):
            return _WMO.get(code[i], "说不清的天气") if i < len(code) else ""

        now_t = cur.get("temperature_2m")
        head = f"{city}现在 {now_t:.0f}℃" if isinstance(now_t, (int, float)) else f"{city}"
        if len(tmax) and len(tmin):
            head += f"，今天 {tmin[0]:.0f}~{tmax[0]:.0f}℃，{wd(0)}"
        if len(pop) and isinstance(pop[0], (int, float)):
            head += f"，降水概率 {pop[0]:.0f}%"
        if len(tmax) > 1 and len(tmin) > 1:
            head += f"；明天 {tmin[1]:.0f}~{tmax[1]:.0f}℃，{wd(1)}"
        text = head + "。"
        _cache[city] = (time.time(), text)
        return text
    except Exception as e:
        print(f"【天气】查询失败：{e}")
        return ""


if __name__ == "__main__":
    print(get_weather("南昌"))
    print(get_weather("高安"))
