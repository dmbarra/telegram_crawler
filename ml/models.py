"""Models used by the ML service and the test script.

CLIP ViT-B-32 (open_clip, LAION-2B) → 512-number image vector.
YuNet (face detection) + SFace (face recognition, OpenCV) → box + 128-number face vector.
"""
import io
import os
from pathlib import Path

import cv2
import numpy as np

CLIP_MODEL, CLIP_WEIGHTS = "ViT-B-32", "laion2b_s34b_b79k"
MODELS_DIR = Path(os.getenv("MODELS_DIR", "/models"))
FACE_MAX_SIDE = 1280
FACE_MIN_SCORE = 0.8
# SFace cosine score: >= 0.363 means same person (OpenCV's published threshold).
SAME_PERSON = 0.363
# Saved with every result; a new value means old results get processed again.
MODEL_ID = f"clip-{CLIP_MODEL}-{CLIP_WEIGHTS}+yunet-2023mar+sface-2021dec"


def normalize(v: np.ndarray) -> np.ndarray:
    v = v.astype(np.float32)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


class Clip:
    def __init__(self):
        import open_clip
        import torch
        torch.set_num_threads(os.cpu_count() or 4)
        self.torch = torch
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            CLIP_MODEL, pretrained=CLIP_WEIGHTS)
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(CLIP_MODEL)

    def embed(self, images) -> np.ndarray:
        """PIL images → (N, 512) normalized vectors."""
        batch = self.torch.stack([self.preprocess(im.convert("RGB")) for im in images])
        with self.torch.no_grad():
            return normalize(self.model.encode_image(batch).float().numpy())

    def texts(self, texts) -> np.ndarray:
        with self.torch.no_grad():
            return normalize(self.model.encode_text(self.tokenizer(texts)).float().numpy())


class Faces:
    def __init__(self, min_score: float = FACE_MIN_SCORE):
        self.detector = cv2.FaceDetectorYN.create(
            str(MODELS_DIR / "yunet.onnx"), "", (320, 320), score_threshold=min_score)
        self.recognizer = cv2.FaceRecognizerSF.create(str(MODELS_DIR / "sface.onnx"), "")

    def find(self, img) -> list:
        """BGR image → [{"box": [x, y, w, h] in original pixels, "score", "vector" (128,)}]."""
        scale = min(1.0, FACE_MAX_SIDE / max(img.shape[:2]))
        if scale < 1:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        self.detector.setInputSize((img.shape[1], img.shape[0]))
        _, found = self.detector.detect(img)
        faces = []
        for row in found if found is not None else []:
            vector = self.recognizer.feature(self.recognizer.alignCrop(img, row))
            faces.append({
                "box": [round(float(v) / scale, 1) for v in row[:4]],
                "score": float(row[-1]),
                "vector": normalize(vector[0]),
            })
        return faces


def decode(data: bytes):
    """Image bytes → (PIL image, BGR array). Raises ValueError if unreadable."""
    from PIL import Image
    try:
        pil = Image.open(io.BytesIO(data))
        pil.load()
    except OSError as e:
        raise ValueError(f"not an image: {e}") from e
    bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        bgr = cv2.cvtColor(np.asarray(pil.convert("RGB")), cv2.COLOR_RGB2BGR)
    return pil, bgr
