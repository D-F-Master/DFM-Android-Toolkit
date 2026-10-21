"""
批量远控 - WebSocket 路由与会话管理

连接协议：
  客户端 → 服务端（JSON 文本帧）：
    {"type":"start", "devices":["id1","id2"], "source":"id1"}  启动批量会话
    {"type":"mirror", "event":{...}}                            主控事件同步
    {"type":"stop"}                                             关闭全部会话
  服务端 → 客户端：
    {"type":"ready", "devices":[{deviceId,name,width,height},...]}
    {"type":"meta",   "deviceId","name","width","height"}       单设备就绪
    {"type":"error",  "deviceId","message"}                     单设备失败
    {"type":"closed", "deviceId"}                               单设备断开
  视频帧走 binary 帧：1B 设备序号 + 1B 帧类型(1=config,2=frame) + H.264 payload

mirror 事件格式：
  {"kind":"touch","action":0,"x":540,"y":960,"w":1080,"h":2340}
  {"kind":"key","keycode":4,"action":1}
  {"kind":"text","text":"hello"}
  {"kind":"scroll","x":540,"y":960,"hx":0,"vy":-1,"w":1080,"h":2340}
"""
import asyncio
import socket
import threading

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from config import logger
from routes.batch_remote import DeviceSession

router = APIRouter()


def _find_free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class RemoteHub:
    """一条 WebSocket 连接对应的批量远控会话集合"""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.sessions: dict = {}       # device_id -> DeviceSession
        self.device_order: list = []   # device_id 加入顺序（binary 帧里的 index）
        self.source_device: str = None
        self._lock = threading.Lock()

    # ---------- 会话管理 ----------

    def start_devices(self, device_ids: list, source: str):
        """并行启动各设备会话（阻塞调用，需在 executor 里跑）"""
        self.source_device = source
        threads = []
        for did in device_ids:
            t = threading.Thread(target=self._start_one, args=(did,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=30)

    def _start_one(self, device_id: str):
        session = DeviceSession(device_id, _find_free_port())
        ok = session.start()
        if ok:
            with self._lock:
                self.sessions[device_id] = session
                self.device_order.append(device_id)
            self._try_send_json({
                "type": "meta", "deviceId": device_id,
                "name": session.device_name,
                "width": session.video_width, "height": session.video_height,
            })
            threading.Thread(target=self._frame_pump, args=(session,), daemon=True).start()
        else:
            self._try_send_json({
                "type": "error", "deviceId": device_id, "message": "启动会话失败",
            })

    def _frame_pump(self, session: DeviceSession):
        """单设备帧队列 → WebSocket binary 帧"""
        while session.running:
            try:
                frame = session.frame_queue.get(timeout=1.0)
            except Exception:
                continue
            if frame["kind"] == "closed":
                break
            with self._lock:
                if session.device_id not in self.device_order:
                    break
                index = self.device_order.index(session.device_id)
            kind = 1 if frame["kind"] == "config" else 2
            try:
                # asyncio 的 send_bytes 线程安全，直接用 loop.call_soon_threadsafe
                asyncio.run_coroutine_threadsafe(
                    self.ws.send_bytes(bytes([index, kind]) + frame["data"]),
                    self._loop,
                ).result(timeout=5)
            except Exception:
                break
        self._try_send_json({"type": "closed", "deviceId": session.device_id})

    def stop_all(self):
        for s in list(self.sessions.values()):
            s.stop()
        with self._lock:
            self.sessions.clear()
            self.device_order.clear()

    # ---------- 事件同步 ----------

    def _rescale(self, event: dict, session: DeviceSession):
        """
        把主控坐标系下的事件坐标换算到目标设备坐标系

        scrcpy 的 PositionMapper 会丢弃 screenSize 与当前视频尺寸不一致的
        事件（主控与从机分辨率不同时必发生），因此必须在注入前换算。
        返回换算后的 (x, y, w, h)；尺寸信息缺失返回 None。
        """
        src_w = event.get("w")
        src_h = event.get("h")
        dst_w, dst_h = session.video_width, session.video_height
        if not (src_w and src_h and dst_w and dst_h):
            return None
        x = event.get("x", 0) * dst_w / src_w
        y = event.get("y", 0) * dst_h / src_h
        return x, y, dst_w, dst_h

    def mirror_event(self, event: dict):
        """主控事件分发到除主控外的所有设备（坐标自动重映射到各目标分辨率）"""
        targets = [s for did, s in self.sessions.items() if did != self.source_device]
        kind = event.get("kind")
        for s in targets:
            try:
                if kind == "touch":
                    mapped = self._rescale(event, s)
                    if not mapped:
                        continue
                    x, y, w, h = mapped
                    s.inject_touch(event["action"], x, y, w, h)
                elif kind == "key":
                    s.inject_keycode(event["keycode"], event["action"])
                elif kind == "text":
                    s.inject_text(event["text"])
                elif kind == "scroll":
                    mapped = self._rescale(event, s)
                    if not mapped:
                        continue
                    x, y, w, h = mapped
                    s.inject_scroll(x, y, event["hx"], event["vy"], w, h)
            except Exception as e:
                logger.warning(f"[{s.device_id}] mirror {kind} failed: {e}")

    # ---------- WebSocket 发送（线程安全封装） ----------

    def bind_loop(self, loop):
        self._loop = loop

    def _try_send_json(self, data: dict):
        try:
            asyncio.run_coroutine_threadsafe(
                self.ws.send_json(data), self._loop
            ).result(timeout=5)
        except Exception as e:
            logger.warning(f"send json failed: {e}")


@router.websocket("/ws/remote")
async def remote_ws(ws: WebSocket):
    """批量远控 WebSocket 端点"""
    await ws.accept()
    hub = RemoteHub(ws)
    hub.bind_loop(asyncio.get_running_loop())
    logger.info("Remote control WebSocket connected")

    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")

            if mtype == "start":
                devices = msg.get("devices") or []
                source = msg.get("source")
                if not devices:
                    await ws.send_json({"type": "error", "message": "devices 为空"})
                    continue
                hub.stop_all()
                await asyncio.get_running_loop().run_in_executor(
                    None, hub.start_devices, devices, source
                )
                await ws.send_json({
                    "type": "ready",
                    "devices": [
                        {
                            "deviceId": did,
                            "name": hub.sessions[did].device_name,
                            "width": hub.sessions[did].video_width,
                            "height": hub.sessions[did].video_height,
                        }
                        for did in hub.device_order
                    ],
                })

            elif mtype == "mirror":
                event = msg.get("event") or {}
                logger.info(f"mirror event: {event}")
                hub.mirror_event(event)

            elif mtype == "stop":
                hub.stop_all()
                await ws.send_json({"type": "ready", "devices": []})

    except WebSocketDisconnect:
        logger.info("Remote control WebSocket disconnected")
    except Exception as e:
        logger.error(f"Remote WS error: {e}")
    finally:
        hub.stop_all()
