##🟥1🟥🟥Dockerfile venv tabanlı
# ---------- Build Stage ----------
FROM python:3.11-slim AS builder

WORKDIR /app

# Gerekli sistem araçlarını yükle (C kütüphaneleri vs.)
RUN apt-get update && apt-get install -y gcc libffi-dev && rm -rf /var/lib/apt/lists/*

# Virtual environment oluştur
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Requirements dosyasını kopyala ve yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------- Runtime Stage ----------
FROM python:3.11-slim AS runtime

WORKDIR /app

# Sağlık kontrolü ve bazı araçlar
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Non-root user oluştur
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 appuser

# Virtual environment’ı builder aşamasından kopyala
COPY --from=builder /opt/venv /opt/venv

# PATH ve PYTHONPATH ayarları
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH="/opt/venv/lib/python3.11/site-packages"

# Uygulama dosyalarını kopyala
COPY --chown=appuser:appgroup . .

# Port ve sağlık kontrolü
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:10000/ || exit 1

# Non-root kullanıcıya geç
USER appuser

# Uygulamayı başlat
CMD ["python", "main.py"]

