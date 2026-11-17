"""ML service: image bytes in, vectors out. Keeps nothing; no files, no database.

    GET  /health   → {"ok": true, "model": MODEL_ID}
    POST /analyze  body = raw image bytes → {"model", "width", "height", "clip", "faces": [...]}

Vectors are base64 float32 (little-endian), so the app can store them as-is.
"""
import base64
import os
import threading

from flask import Flask, jsonify, request
from waitress import serve

from models import MODEL_ID, Clip, Faces, decode

MAX_BYTES = 50 * 1024 * 1024

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES
clip = Clip()
faces = Faces()
# One image at a time: torch already uses every core.
infer_lock = threading.Lock()


def b64(vector) -> str:
    return base64.b64encode(vector.astype("<f4").tobytes()).decode()


@app.get("/health")
def health():
    return jsonify(ok=True, model=MODEL_ID)


@app.post("/analyze")
def analyze():
    data = request.get_data()
    if not data:
        return jsonify(error="empty body"), 400
    try:
        pil, bgr = decode(data)
    except ValueError as e:
        return jsonify(error=str(e)), 422
    with infer_lock:
        vector = clip.embed([pil])[0]
        found = faces.find(bgr)
    return jsonify(
        model=MODEL_ID, width=pil.width, height=pil.height, clip=b64(vector),
        faces=[{"box": f["box"], "score": f["score"], "vector": b64(f["vector"])} for f in found],
    )


if __name__ == "__main__":
    print(f"ML service ready: {MODEL_ID}", flush=True)
    serve(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), threads=2)
