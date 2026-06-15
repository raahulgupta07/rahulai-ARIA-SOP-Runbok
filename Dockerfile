# ---------- stage 1: build the SvelteKit SPA ----------
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
# relative /api so it works on any host/port
ENV VITE_API=""
RUN npm run build

# ---------- stage 2: python runtime serving API + SPA ----------
FROM python:3.12-slim AS app
WORKDIR /app

# deps first (layer cache)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# app code + vendored pageindex
COPY app/ ./app/
COPY vendor_pageindex/ ./vendor_pageindex/
COPY scripts/ ./scripts/

# built frontend (served by FastAPI at /)
COPY --from=web /web/build ./frontend/build

# runtime data dirs (page images / uploads) — mounted as a volume in compose
RUN mkdir -p /app/data/pages /app/data/uploads

# build stamp (read by app/version.py at runtime) — see release.sh
ARG BUILD_SHA=local
ARG BUILD_DATE=
ENV BUILD_SHA=$BUILD_SHA
ENV BUILD_DATE=$BUILD_DATE

EXPOSE 8077
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8077"]
