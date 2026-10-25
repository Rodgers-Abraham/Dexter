import asyncio
import base64
import io
import json
import re
import os
import sys
import time
import threading
import cv2
import mss
import mss.tools
import sounddevice as sd
import numpy as np
from pathlib import Path

try:
    import PIL.Image
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

from google import genai
from google.genai import types

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

IMG_MAX_W = 640
IMG_MAX_H = 360
JPEG_Q    = 55

SYSTEM_PROMPT = (
    "You are DEXTER from Iron Man movies. "
    "Analyze images with technical precision and intelligence. "
    "Help the user in a way they can understand — don't be overly complex. "
    "Be concise, smart, and helpful like Tony Stark's AI assistant. "
    "Respond in maximum 2 short sentences. Speed is priority. "
    "Address the user as 'sir' for a tone of respect. "
    "Ask if the user needs any further help with their problem."
)


def _get_api_key() -> str:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            keys = json.load(f)
        key = keys.get("gemini_api_key", "")
        if not key:
            raise ValueError("gemini_api_key not found")
        return key
    except Exception as e:
        raise RuntimeError(f"Could not load API key: {e}")


def _get_camera_index() -> int:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if "camera_index" in cfg:
            return int(cfg["camera_index"])
    except Exception:
        pass

    print("[Camera] 🔍 No camera index in config. Auto-detecting...")
    best_index = 0

    for idx in range(6):
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            continue
        for _ in range(5):
            cap.read()
        ret, frame = cap.read()
        cap.release()
        if ret and frame is not None and frame.mean() > 5:
            best_index = idx
            print(f"[Camera] ✅ Camera found at index {idx} — saving to config.")
            break
        else:
            print(f"[Camera] ⚠️  Index {idx}: no valid frame.")

    try:
        cfg = {}
        if API_CONFIG_PATH.exists():
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        cfg["camera_index"] = best_index
        with open(API_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
        print(f"[Camera] 💾 Camera index {best_index} saved to config.")
    except Exception as e:
        print(f"[Camera] ⚠️  Could not save camera index: {e}")

    return best_index


def _to_jpeg(img_bytes: bytes) -> bytes:
    if not _PIL_OK:
        return img_bytes
    img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img.thumbnail([IMG_MAX_W, IMG_MAX_H], PIL.Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_Q, optimize=False)
    return buf.getvalue()


def _capture_portal_screenshot(timeout_sec=15) -> bytes | None:
    try:
        import dbus
        import dbus.mainloop.glib
        from gi.repository import GLib
        import random
        import os
    except ImportError as e:
        print(f"[ScreenProcess] DBus or GLib not available for portal: {e}")
        return None

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    
    loop = GLib.MainLoop()
    result = {"uri": None, "error": None}
    
    token = f"dexter_screenshot_{random.randint(1000, 9999)}"
    connection_name = bus.get_unique_name().replace(".", "_").replace(":", "_")
    request_path = f"/org/freedesktop/portal/desktop/request/{connection_name}/{token}"
    
    def on_response(response_code, results):
        if response_code == 0:
            result["uri"] = results.get("uri")
        else:
            result["error"] = f"User denied or error code: {response_code}"
        loop.quit()
        
    receiver = bus.add_signal_receiver(
        on_response,
        signal_name="Response",
        dbus_interface="org.freedesktop.portal.Request",
        bus_name="org.freedesktop.portal.Desktop",
        path=request_path
    )
    
    try:
        portal_obj = bus.get_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop")
        iface = dbus.Interface(portal_obj, "org.freedesktop.portal.Screenshot")
        
        options = {
            "interactive": dbus.Boolean(False),
            "handle_token": dbus.String(token)
        }
        
        iface.Screenshot("", options)
    except Exception as e:
        result["error"] = f"DBus call failed: {e}"
        receiver.remove()
        return None
        
    def on_timeout():
        result["error"] = "Timeout waiting for screenshot authorization."
        loop.quit()
        return False
        
    GLib.timeout_add(int(timeout_sec * 1000), on_timeout)
    loop.run()
    receiver.remove()
    
    if result["uri"]:
        uri = str(result["uri"])
        if uri.startswith("file://"):
            filepath = uri[7:]
            if os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    data = f.read()
                try:
                    os.remove(filepath)
                except Exception:
                    pass
                return data
    else:
        print(f"[ScreenshotPortal] Error: {result['error']}")
    return None

def _capture_screenshot() -> bytes:
    import shutil
    import subprocess
    import os

    # A helper to check if image is likely blank (solid color / black) based on size
    def is_likely_blank(data: bytes) -> bool:
        return len(data) < 15000

    # 1. Try silent gnome-screenshot first if installed
    if shutil.which("gnome-screenshot"):
        try:
            tmp_path = "/tmp/dexter_screen.png"
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            
            # Run gnome-screenshot
            subprocess.run(["gnome-screenshot", "-f", tmp_path], check=True, capture_output=True, timeout=5)
            if os.path.exists(tmp_path):
                with open(tmp_path, "rb") as f:
                    png_data = f.read()
                try: os.remove(tmp_path)
                except Exception: pass
                
                jpeg_data = _to_jpeg(png_data)
                if not is_likely_blank(jpeg_data):
                    print("[ScreenProcess] 🖥️ Screen captured silently via gnome-screenshot")
                    return jpeg_data
        except Exception as e:
            print(f"[ScreenProcess] gnome-screenshot failed: {e}")

    # 2. Try mss (very fast, silent, works on X11 and Xwayland windows)
    try:
        with mss.mss() as sct:
            shot      = sct.grab(sct.monitors[1])
            png_bytes = mss.tools.to_png(shot.rgb, shot.size)
        jpeg_data = _to_jpeg(png_bytes)
        
        # If it is not blank, return it directly!
        if not is_likely_blank(jpeg_data):
            print("[ScreenProcess] 🖥️ Screen captured via mss")
            return jpeg_data
    except Exception as e:
        print(f"[ScreenProcess] mss capture failed: {e}")

    # 3. Fallback to Portal Screenshot (prompts user on Wayland/X11)
    print("[ScreenProcess] Xwayland screen capture was blank or failed. Requesting Wayland desktop portal screenshot...")
    try:
        portal_data = _capture_portal_screenshot()
        if portal_data:
            jpeg_data = _to_jpeg(portal_data)
            print("[ScreenProcess] 🖥️ Screen captured via XDG Desktop Portal")
            return jpeg_data
    except Exception as e:
        print(f"[ScreenProcess] XDG Portal screenshot failed: {e}")

    # 4. Ultimate fallback: return whatever mss captured even if it is blank/black
    print("[ScreenProcess] Portal screenshot failed or timed out. Returning mss buffer as fallback.")
    try:
        with mss.mss() as sct:
            shot      = sct.grab(sct.monitors[1])
            png_bytes = mss.tools.to_png(shot.rgb, shot.size)
        return _to_jpeg(png_bytes)
    except Exception:
        # If even mss fails completely, return a small 1x1 black pixel to avoid crash
        return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"


def _capture_camera() -> bytes:
    camera_index = _get_camera_index()
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise RuntimeError(f"Camera could not be opened: index {camera_index}")
    for _ in range(10):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        raise RuntimeError("Could not capture camera frame.")
    if _PIL_OK:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(rgb)
        img.thumbnail([IMG_MAX_W, IMG_MAX_H], PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_Q, optimize=False)
        return buf.getvalue()
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
    return buf.tobytes()


class _LiveSession:

    def __init__(self):
        self._loop:      asyncio.AbstractEventLoop | None = None
        self._thread:    threading.Thread | None          = None
        self._session                                     = None
        self._out_queue: asyncio.Queue | None             = None
        self._audio_in:  asyncio.Queue | None             = None
        self._ready:     threading.Event                  = threading.Event()
        self._player                                      = None
        self._send_lock: asyncio.Lock | None              = None

    def start(self, player=None):
        if self._thread and self._thread.is_alive():
            return
        self._player = player
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="VisionSessionThread"
        )
        self._thread.start()
        ok = self._ready.wait(timeout=20)
        if not ok:
            raise RuntimeError("Vision session did not start within 20s.")
        print("[ScreenProcess] ✅ Vision session ready (no mic)")

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        self._out_queue = asyncio.Queue(maxsize=30)
        self._audio_in  = asyncio.Queue()
        self._send_lock = asyncio.Lock()

        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            system_instruction=SYSTEM_PROMPT,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

        while True:
            try:
                print("[ScreenProcess] 🔌 Vision session connecting...")
                async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
                    self._session = session
                    self._ready.set()
                    print("[ScreenProcess] ✅ Vision session connected")
                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(self._send_loop())
                        tg.create_task(self._recv_loop())
                        tg.create_task(self._play_loop())
            except Exception as e:
                print(f"[ScreenProcess] ⚠️ Disconnected: {e} — reconnecting...")
                self._session = None
                self._ready.clear()
                await asyncio.sleep(2)
                self._ready.set()

    async def _send_loop(self):
        while True:
            item = await self._out_queue.get()
            if self._session:
                image_bytes, mime_type, user_text = item
                try:
                    b64 = base64.b64encode(image_bytes).decode("utf-8")
                    await self._session.send_client_content(
                        turns={
                            "parts": [
                                {"inline_data": {"mime_type": mime_type, "data": b64}},
                                {"text": user_text}
                            ]
                        },
                        turn_complete=True
                    )
                    print("[ScreenProcess] ✅ Image sent")
                except Exception as e:
                    print(f"[ScreenProcess] ⚠️ Send error: {e}")

    async def _recv_loop(self):
        transcript_buf: list[str] = []
        try:
            async for response in self._session.receive():
                if response.data:
                    await self._audio_in.put(response.data)
                sc = response.server_content
                if not sc:
                    continue
                if sc.output_transcription and sc.output_transcription.text:
                    chunk = sc.output_transcription.text.strip()
                    if chunk:
                        transcript_buf.append(chunk)
                if sc.turn_complete:
                    if transcript_buf and self._player:
                        full = re.sub(r'\s+', ' ', " ".join(transcript_buf)).strip()
                        if full:
                            self._player.write_log(f"Dexter: {full}")
                            print(f"[ScreenProcess] 💬 {full}")
                    transcript_buf = []
        except Exception as e:
            print(f"[ScreenProcess] ⚠️ Recv error: {e}")
            transcript_buf = []
            await asyncio.sleep(0.3)

    async def _play_loop(self):
        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()
        try:
            while True:
                chunk = await self._audio_in.get()
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[ScreenProcess] ❌ Play error: {e}")
            raise
        finally:
            stream.stop()
            stream.close()

    def analyze(self, image_bytes: bytes, mime_type: str, user_text: str):
        if not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._out_queue.put((image_bytes, mime_type, user_text)),
            self._loop
        )

    def is_ready(self) -> bool:
        return self._session is not None


_live       = _LiveSession()
_started    = False
_start_lock = threading.Lock()


def _ensure_started(player=None):
    global _started
    with _start_lock:
        if not _started:
            _live.start(player=player)
            _started = True
        elif player is not None:
            _live._player = player


def screen_process(
    parameters:     dict,
    response:       str | None = None,
    player=None,
    session_memory=None,
) -> bool:
    user_text = (parameters or {}).get("text") or (parameters or {}).get("user_text", "")
    user_text = (user_text or "").strip()
    if not user_text:
        print("[ScreenProcess] ⚠️ No user_text provided.")
        return False

    angle = (parameters or {}).get("angle", "screen").lower().strip()
    print(f"[ScreenProcess] angle={angle!r}  text={user_text!r}")

    _ensure_started(player=player)

    try:
        if angle == "camera":
            image_bytes = _capture_camera()
            mime_type   = "image/jpeg"
            print("[ScreenProcess] 📷 Camera captured")
        else:
            image_bytes = _capture_screenshot()
            mime_type   = "image/jpeg" if _PIL_OK else "image/png"
            print("[ScreenProcess] 🖥️ Screen captured")
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"[ScreenProcess] ❌ Capture error: {e}")
        return False

    print(f"[ScreenProcess] 📦 {len(image_bytes)} bytes → sending")
    _live.analyze(image_bytes, mime_type, user_text)
    return True


def warmup_session(player=None):
    try:
        _ensure_started(player=player)
    except Exception as e:
        print(f"[ScreenProcess] ⚠️ Warmup error: {e}")


if __name__ == "__main__":
    print("[TEST] screen_processor.py v8 — image-only session")
    print("=" * 50)
    mode    = input("screen / camera (default: screen): ").strip().lower() or "screen"
    request = input("Question (Enter for default): ").strip() or "What do you see? Be brief."

    t0 = time.perf_counter()
    warmup_session()
    print(f"Session ready — {time.perf_counter()-t0:.2f}s\n")

    t1     = time.perf_counter()
    result = screen_process({"angle": mode, "text": request}, player=None)
    print(f"Sent — {time.perf_counter()-t1:.3f}s | audio incoming...")
    time.sleep(8)
    print(f"\n{'✅' if result else '❌'}")
