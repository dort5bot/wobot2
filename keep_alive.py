# keep_alive.py
"""
Mini HTTP server to keep Render free-tier service alive.
Port 10000 → Render assigns automatically via PORT env.
"""

import os
from aiohttp import web

async def handle(request):
    return web.Response(text="Bot is running ✅")

async def start_keepalive():
    app = web.Application()
    app.add_routes([web.get("/", handle)])
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", 10000))  # Render default PORT
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
