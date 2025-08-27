# Simple handler loader for main.py - imports all handlers in handlers/ directory
import importlib, pkgutil, handlers
def register_handlers(app):
    # app expected to have add_handler function similar to PTB
    for finder, name, ispkg in pkgutil.iter_modules(handlers.__path__):
        mod = importlib.import_module("handlers." + name)
        if hasattr(mod, "register"):
            mod.register(app)