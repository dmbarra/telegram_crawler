# ML service (CLIP + faces) and the ml_test.py script. All models are baked in at
# build time, so the container needs no internet when it runs.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch: the default wheel pulls in ~2 GB of CUDA libraries.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

ENV HF_HOME=/models/hf
ADD https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx /models/yunet.onnx
ADD https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx /models/sface.onnx
RUN python -c "import open_clip; open_clip.create_model_and_transforms('ViT-B-32', pretrained='laion2b_s34b_b79k')" \
    && chmod -R a+rX /models

WORKDIR /app
COPY models.py service.py ml_test.py ./
# Runs with --read-only: anything that wants to write a cache goes to the /tmp tmpfs.
ENV HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HOME=/tmp XDG_CACHE_HOME=/tmp PYTHONUNBUFFERED=1
USER 1000
EXPOSE 8000
ENTRYPOINT ["python"]
CMD ["service.py"]
