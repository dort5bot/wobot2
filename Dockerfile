##🟥1🟥🟥Dockerfile
FROM python:3.11-slim AS builder

WORKDIR /app

# Önce requirements.txt'yi kopyala ve bağımlılıkları yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim AS runtime

WORKDIR /app

# Sistem paketlerini kur (gerekli olabilir)
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user oluştur
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 appuser

# Builder stage'den Python paketlerini kopyala
COPY --from=builder --chown=appuser:appgroup /root/.local /home/appuser/.local
COPY --chown=appuser:appgroup . .

# PATH'e user Python paketlerini ekle
ENV PATH="/home/appuser/.local/bin:${PATH}"
ENV PYTHONPATH="/home/appuser/.local/lib/python3.11/site-packages:${PYTHONPATH}"

# Python paketlerinin doğru kopyalandığını kontrol et
RUN python -c "import nest_asyncio; print('nest_asyncio successfully imported')" || echo "Import failed"

# Port bilgisi (Render otomatik port kullanır)
EXPOSE 10000

# Health check - Render için uygun port
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:10000/ || exit 1

# Non-root user ile çalıştır
USER appuser

# Container başlatıldığında çalışacak komut
CMD ["python", "main.py"]
