# src/utils/encryption_utils.py
# apikey şifreleme için 
import os
from cryptography.fernet import Fernet

KEY = os.environ.get("FERNET_KEY")  # .env'den al
if not KEY:
    raise ValueError("FERNET_KEY is not set in environment variables")
fernet = Fernet(KEY.encode())  # string → bytes

def encrypt_text(text: str) -> str:
    return fernet.encrypt(text.encode()).decode()

def decrypt_text(token: str) -> str:
    return fernet.decrypt(token.encode()).decode()
