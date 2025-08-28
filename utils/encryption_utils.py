# utils/encryption_utils.py
# apikey şifreleme için
import os
from cryptography.fernet import Fernet

# .env'den FERNET_KEY al, yoksa otomatik oluştur
KEY = os.environ.get("FERNET_KEY")
if not KEY:
    KEY = Fernet.generate_key().decode()
    print("FERNET_KEY env yok, otomatik oluşturuldu:", KEY)

fernet = Fernet(KEY.encode())  # string → bytes

def encrypt_text(text: str) -> str:
    """Metni şifrele ve string olarak döndür."""
    return fernet.encrypt(text.encode()).decode()

def decrypt_text(token: str) -> str:
    """Şifreli metni çöz ve string olarak döndür."""
    return fernet.decrypt(token.encode()).decode()
