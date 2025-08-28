# utils/encryption_utils.py — apikey şifreleme
# FERNET_KEY artık .env’de zorun

import os
from cryptography.fernet import Fernet

# ⚡ FERNET_KEY artık .env’de zorunlu
KEY = os.environ.get("FERNET_KEY")
if not KEY:
    raise RuntimeError("FERNET_KEY env variable is not set. Set it in Render dashboard.")

fernet = Fernet(KEY.encode())  # string → bytes

def encrypt_text(text: str) -> str:
    """Metni şifrele ve string olarak döndür."""
    return fernet.encrypt(text.encode()).decode()

def decrypt_text(token: str) -> str:
    """Şifreli metni çöz ve string olarak döndür."""
    return fernet.decrypt(token.encode()).decode()
