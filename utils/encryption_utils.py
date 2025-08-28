# src/utils/encryption_utils.py
# apikey şifreleme için 

from cryptography.fernet import Fernet

KEY = b"your-fernet-key-here"  # bunu güvenli şekilde saklayın

fernet = Fernet(KEY)

def encrypt_text(text: str) -> str:
    return fernet.encrypt(text.encode()).decode()

def decrypt_text(token: str) -> str:
    return fernet.decrypt(token.encode()).decode()
