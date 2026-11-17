"""ML analysis: sends images of a study-source channel to the ML container, saves results.

One run at a time, on its own thread, started and stopped from the Viewer.
Images that already have a result from the same model are skipped.
"""
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request

from src.config import DOWNLOADS_DIR

ML_URL = os.getenv("ML_URL", "http://crawler-ml:8000").rstrip("/")
TIMEOUT = 120


class ServiceDown(Exception):
    pass


def _call(method, path, data=None, timeout=TIMEOUT) -> dict:
    req = urllib.request.Request(ML_URL + path, data=data, method=method,
                                 headers={"Content-Type": "application/octet-stream"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error") or f"HTTP {e.code}"
        except ValueError:
            msg = f"HTTP {e.code}"
        if e.code in (400, 413, 422):
            raise ValueError(msg) from e
        raise ServiceDown(f"ML service error: {msg}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ServiceDown(f"ML service not reachable at {ML_URL} ({getattr(e, 'reason', e)})") from e


def health() -> str:
    """Model id of the running ML service. Raises ServiceDown."""
    model = _call("GET", "/health", timeout=5).get("model")
    if not model:
        raise ServiceDown("ML service answered without a model name")
    return model


class Analyzer:
    def __init__(self, library):
        self.library = library
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.model = None
        self.running = False
        self.channel_id = None
        self.done = self.total = self.errors = self.faces = 0
        self.started_at = self.finished_at = None
        self.result = ""          # done | stopped | failed
        self.message = ""

    def snapshot(self, channel_id=None) -> dict:
        with self._lock:
            state = {
                "running": self.running, "channel_id": self.channel_id, "done": self.done,
                "total": self.total, "errors": self.errors, "faces": self.faces,
                "started_at": self.started_at, "finished_at": self.finished_at,
                "result": self.result, "message": self.message, "model": self.model,
                "ml_url": ML_URL,
            }
        if channel_id is not None:
            state["channel"] = self.library.ml_summary(channel_id, self.model)
            state["study_source"] = self.library.is_study_source(channel_id)
        return state

    def start(self, channel_id: int):
        """Returns an error message, or None if the run started."""
        if not self.library.is_study_source(channel_id):
            return "Turn on Study source for this channel first"
        with self._lock:
            if self.running:
                return "An analysis is already running"
        try:
            model = health()
        except ServiceDown as e:
            return str(e)
        todo = [r["path"] for r in self.library.ml_todo(channel_id, model)]
        with self._lock:
            if self.running:
                return "An analysis is already running"
            self.model = model
            if not todo:
                return "Nothing new: every image of this channel is already analyzed"
            self._stop.clear()
            self.running, self.channel_id = True, channel_id
            self.done, self.total, self.errors, self.faces = 0, len(todo), 0, 0
            self.started_at, self.finished_at = time.time(), None
            self.result = self.message = ""
        threading.Thread(target=self._run, args=(todo, model), daemon=True, name="ml").start()
        return None

    def stop(self) -> None:
        self._stop.set()

    def stop_channel(self, channel_id: int) -> None:
        with self._lock:
            if self.running and self.channel_id == channel_id:
                self._stop.set()

    def _run(self, todo, model):
        result, message = "done", ""
        try:
            for rel in todo:
                if self._stop.is_set():
                    result = "stopped"
                    break
                ok, faces = self._one(rel, model)
                with self._lock:
                    self.done += 1
                    self.errors += not ok
                    self.faces += faces
        except ServiceDown as e:
            result, message = "failed", str(e)
        finally:
            with self._lock:
                self.running = False
                self.finished_at = time.time()
                self.result, self.message = result, message

    def _one(self, rel, model) -> tuple:
        """(ok, faces found). Raises ServiceDown so the run stops and the image is retried later."""
        try:
            data = (DOWNLOADS_DIR / rel).read_bytes()
        except OSError as e:
            self.library.save_ml_result(rel, model, error=f"cannot read file: {e}")
            return False, 0
        try:
            out = _call("POST", "/analyze", data)
        except ValueError as e:
            self.library.save_ml_result(rel, model, error=str(e)[:300])
            return False, 0
        if out.get("model") != model:
            raise ServiceDown("The ML service changed its model during the run. Start again.")
        faces = [(*f["box"], f["score"], base64.b64decode(f["vector"])) for f in out["faces"]]
        self.library.save_ml_result(rel, model, base64.b64decode(out["clip"]),
                                    out.get("width"), out.get("height"), faces)
        return True, len(faces)
