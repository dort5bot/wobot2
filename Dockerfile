##🟥1🟥🟥Dockerfile
FROM python:3.11-slim AS builder

WORKDIR /app

# Python bağımlılıklarını kopyala ve kur
COPY requirements.txt .
RUN pip install --user -r requirements.txt

FROM python:3.11-slim AS runtime

WORKDIR /app

# Non-root user oluştur
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 appuser

# Builder stage'den Python paketlerini kopyala
COPY --from=builder --chown=appuser:appgroup /root/.local /home/appuser/.local
COPY --chown=appuser:appgroup . .

# PATH'e user Python paketlerini ekle
ENV PATH="/home/appuser/.local/bin:${PATH}"

# Port bilgisi (3000 portu için)
EXPOSE 3000

# Health check - 3000 portunda çalıştığını varsayarak
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:3000/health || exit 1

# Non-root user ile çalıştır
USER appuser

# Container başlatıldığında çalışacak komut
CMD ["python", "main.py"]
