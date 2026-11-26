FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ERRATA_DB_PATH=/data/errata.db

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY errata_app ./errata_app

RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 5000

# 单 worker：4×4 分析为 CPU 密集型且内存峰值约 180MB，
# 多 worker 会成倍占用内存；线程用于并行处理普通页面请求。
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "120", "errata_app.app:app"]
