FROM node:22-slim AS frontend
WORKDIR /workspace
COPY . .
RUN npm install --prefix "Design System Overview" --legacy-peer-deps && npm run build --prefix "Design System Overview" && mkdir -p /tmp/frontend-dist && cp -r "Design System Overview/dist/." /tmp/frontend-dist/

FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FRONTEND_DIST=/app/frontend/dist

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend /tmp/frontend-dist /app/frontend/dist

RUN mkdir -p static templates
EXPOSE 7860
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}"]
