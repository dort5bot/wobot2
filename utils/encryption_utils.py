# utils/encryption_utils.py
# API key ve diğer hassas veriler için şifreleme

import os
from cryptography.fernet import Fernet

KEY = os.environ.get("FERNET_KEY")
if not KEY:
    raise RuntimeError("FERNET_KEY env variable is not set. Add it to your .env or Render dashboard.")

fernet = Fernet(KEY.encode())

def encrypt_text(text: str) -> str:
    """Metni şifrele ve base64 string döndür."""
    return fernet.encrypt(text.encode()).decode()

def decrypt_text(token: str) -> str:
    """Şifrelenmiş stringi çöz."""
    return fernet.decrypt(token.encode()).decode()
