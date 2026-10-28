"""QQ 接入：OneBot v11 正向 WebSocket 客户端（适配 NapCatQQ / LLOneBot）"""
import json
import re
import threading
import time

import websocket


def extract_text(event):
    """从 OneBot 事件里取出纯文本（兼容 array 和 string 两种上报格式）"""
    msg = event.get("message")
    if isinstance(msg, str):
        # CQ 码字符串：去掉 [CQ:xxx] 标记
        return re.sub(r"\[CQ:[^\]]*\]", "", msg).strip()
    parts = []
    for seg in msg or []:
        if seg.get("type") == "text":
            parts.append((seg.get("data") or {}).get("text", ""))
    return "".join(parts).strip()


def at_me(event, self_id):
    """群里是否 @ 了自己"""
    msg = event.get("message")
    if not isinstance(msg, list):
        return f"[CQ:at,qq={self_id}]" in str(event.get("raw_message", ""))
    for seg in msg:
        if seg.get("type") == "at":
            if str((seg.get("data") or {}).get("qq")) == str(self_id):
                return True
    return False


class OneBotClient:
    """正向 WS 客户端：连 NapCat 的 WebSocket 服务端，收事件 + 调 API"""

    def __init__(self, ws_url, token="", on_message=None):
        self.ws_url = ws_url
        self.token = token
        self.on_message = on_message
        self.ws = None
        self.connected = False
        self._send_lock = threading.Lock()
        # 同步 API 调用：echo -> 响应
        self._api_results = {}
        self._api_cond = threading.Condition()
        self._echo_seq = 0

    # ---------- 连接 ----------
    def _connect(self):
        header = []
        if self.token:
            header.append(f"Authorization: Bearer {self.token}")
        self.ws = websocket.create_connection(self.ws_url, header=header, timeout=30)
        self.connected = True
        print(f"[QQ] 已连接 {self.ws_url}")

    def is_connected(self):
        """主动陪伴等后台线程在发消息前先看连接状态，避免往断开的 socket 里写"""
        return bool(self.connected and self.ws)

    # ---------- 发消息 ----------
    def send_private(self, user_id, text):
        return self._send_action("send_private_msg", {"user_id": user_id, "message": text})

    def send_group(self, group_id, text):
        return self._send_action("send_group_msg", {"group_id": group_id, "message": text})

    def send_private_record(self, user_id, path):
        """发本地语音条（NapCat 需要 file:// 绝对路径）。等回执，失败返回 False"""
        uri = "file:///" + path.replace("\\", "/")
        r = self.call_api(
            "send_private_msg",
            {"user_id": user_id, "message": [{"type": "record", "data": {"file": uri}}]},
            timeout=120,
        )
        if not r or r.get("retcode") != 0:
            print(f"[QQ] 语音发送失败：{r}")
            return False
        return True

    def _send_action(self, action, params):
        payload = json.dumps(
            {"action": action, "params": params, "echo": str(time.time())},
            ensure_ascii=False,
        )
        with self._send_lock:
            self.ws.send(payload)

    # ---------- 同步 API ----------
    def call_api(self, action, params, timeout=15):
        """同步调用 NapCat API，返回响应对象；超时/失败返回 None"""
        with self._api_cond:
            self._echo_seq += 1
            echo = f"api{self._echo_seq}"
        payload = json.dumps(
            {"action": action, "params": params, "echo": echo},
            ensure_ascii=False,
        )
        try:
            with self._send_lock:
                self.ws.send(payload)
        except Exception as e:
            print(f"[QQ] API {action} 发送失败：{e}")
            return None
        end = time.time() + timeout
        with self._api_cond:
            while echo not in self._api_results:
                remain = end - time.time()
                if remain <= 0:
                    return None
                self._api_cond.wait(remain)
            return self._api_results.pop(echo)

    def get_record(self, file, out_format="mp3"):
        """让 NapCat 把语音转成指定格式（silk 等格式 ffmpeg 解不了），返回本地文件路径"""
        r = self.call_api("get_record", {"file": file, "out_format": out_format}, timeout=30)
        if not r or r.get("retcode") != 0:
            return None
        return (r.get("data") or {}).get("file")

    # ---------- 主循环 ----------
    def run_forever(self):
        while True:
            try:
                self._connect()
                while True:
                    try:
                        raw = self.ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue  # 空闲超时，继续等
                    if not raw:
                        raise ConnectionError("连接被对端关闭")
                    try:
                        event = json.loads(raw)
                    except Exception:
                        continue
                    # API 响应（带 echo + retcode）：存起来供 call_api 同步等待
                    if "echo" in event and "retcode" in event:
                        with self._api_cond:
                            self._api_results[str(event["echo"])] = event
                            self._api_cond.notify_all()
                    if self.on_message:
                        try:
                            self.on_message(event)
                        except Exception as e:
                            print(f"[QQ] 处理消息出错：{e}")
            except KeyboardInterrupt:
                print("[QQ] 已停止")
                return
            except Exception as e:
                self.connected = False
                print(f"[QQ] 连接异常：{e}，5 秒后重连")
                time.sleep(5)


def split_text(text, limit=400):
    """把长回复切成多条，避免触发 QQ 单条长度限制"""
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    chunks, buf = [], ""
    for sent in re.split(r"(?<=[。！？!?\n])", text):
        if not sent:
            continue
        if len(buf) + len(sent) > limit:
            if buf:
                chunks.append(buf)
            buf = sent
        else:
            buf += sent
    if buf:
        chunks.append(buf)
    return chunks
