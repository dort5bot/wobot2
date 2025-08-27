#otomotik handler ekleme dosyası 
#handler_loader.py
#get register+Handler eklenebilir 

import os
import importlib
import logging

LOG = logging.getLogger("handler_loader")

def load_handlers(application, path="handlers"):
    """
    handlers klasöründeki tüm python dosyalarını tarar
    ve varsa register(application) fonksiyonunu çağırır.
    """
    for file in os.listdir(path):
        if file.endswith(".py") and file != "__init__.py":
            module_name = f"{path}.{file[:-3]}"
            try:
                module = importlib.import_module(module_name)
                if hasattr(module, "register"):
                    module.register(application)
                    LOG.info(f"Handler loaded: {module_name}")
            except Exception as e:
                LOG.exception(f"Failed to load handler {module_name}: {e}")
