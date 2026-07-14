# 构建上下文 = TelecomQt 项目根（见 deploy/docker-compose.yml）
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app /app/app
COPY frontend /app/frontend

# 实验数据挂载点（docker-compose 挂 ./backend/data:/data）
ENV FRONTEND_DIR=/app/frontend
ENV EXPERIMENTS_DIR=/data/experiments
VOLUME ["/data"]

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
