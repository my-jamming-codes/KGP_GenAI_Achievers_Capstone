FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/opt/fastembed STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Download the reranker at build time so the container starts without it. Embeddings come from the embeddings endpoint by default;
# with EMBED_PROVIDER=fastembed the embedding model is downloaded on first use instead.
RUN python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2')"

COPY src ./src
COPY eval ./eval
COPY rules ./rules
COPY data/applications ./data/applications
# Saved index snapshot: chunks, embeddings and BM25. Build it first with `python -m src.index.hybrid build`.
COPY data/index/chunks.jsonl data/index/embeddings.npy data/index/bm25.pkl data/index/meta.json ./data/index/

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"
CMD ["streamlit", "run", "src/app/main.py", "--server.port=8501", "--server.address=0.0.0.0"]
