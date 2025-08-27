import asyncio, os
from utils.handler_loader import register_handlers
from jobs.worker_a import run_forever as worker_a_run
from jobs.worker_b import run_forever as worker_b_run

# Minimal PTB-like app skeleton (user should replace with real PTB Application)
class DummyApp:
    def add_handler(self, h): pass

app = DummyApp()

def start_workers(loop):
    loop.create_task(worker_a_run())
    loop.create_task(worker_b_run())

def main():
    loop = asyncio.get_event_loop()
    register_handlers(app)
    start_workers(loop)
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()