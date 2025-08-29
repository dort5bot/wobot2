# utils/health.py
# ♦️ Basit sistem sağlık kontrolü
'''

log, internet bağlantısı, CPU gibi sistem durumlarını göstermek için kullanılır
'''

import platform
import psutil
import socket
import asyncio


async def health_check():
    """
    Sistem sağlığı ile ilgili temel bilgileri döner:
    - hostname, platform, CPU, RAM, uptime, aktif bağlantılar
    """
    try:
        return {
            "hostname": socket.gethostname(),
            "platform": platform.system(),
            "platform_version": platform.version(),
            "cpu_percent": psutil.cpu_percent(interval=0.5),
            "memory": {
                "total": psutil.virtual_memory().total,
                "available": psutil.virtual_memory().available,
                "percent": psutil.virtual_memory().percent
            },
            "uptime_seconds": int(psutil.boot_time() and (psutil.time.time() - psutil.boot_time())),
            "connections": len(psutil.net_connections())
        }
    except Exception as e:
        return {"error": str(e)}
