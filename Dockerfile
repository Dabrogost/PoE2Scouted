FROM python:3.11-slim

RUN useradd -m -u 1000 user

ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    EASYOCR_MODEL_DIR=/home/user/.cache/easyocr

WORKDIR $HOME/app

RUN mkdir -p $HOME/.cache/easyocr && chown -R user:user $HOME/.cache $HOME/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=user requirements.txt $HOME/app/requirements.txt

USER user

RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . $HOME/app

EXPOSE 7860

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
