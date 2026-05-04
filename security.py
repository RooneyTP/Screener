import os
from dotenv import load_dotenv
from cryptography.fernet import Fernet
import base64

# Load environment variables
load_dotenv()

# Generate or load encryption key
def get_encryption_key():
    key_file = ".encryption_key"
    if os.path.exists(key_file):
        with open(key_file, "rb") as f:
            return f.read()
    else:
        key = Fernet.generate_key()
        with open(key_file, "wb") as f:
            f.write(key)
        return key

ENCRYPTION_KEY = get_encryption_key()
cipher = Fernet(ENCRYPTION_KEY)

def encrypt_data(data: str) -> str:
    return cipher.encrypt(data.encode()).decode()

def decrypt_data(encrypted_data: str) -> str:
    return cipher.decrypt(encrypted_data.encode()).decode()

def get_env_var(key: str, default=None):
    return os.getenv(key, default)

# Secure storage for sensitive data
def store_secure_data(key: str, value: str):
    """Encrypt and store sensitive data to file"""
    try:
        encrypted = encrypt_data(value)
        with open(f".secure_{key}", "w") as f:
            f.write(encrypted)
    except Exception as e:
        print(f"Failed to store secure data for {key}: {e}")

def load_secure_data(key: str) -> str:
    """Load and decrypt sensitive data from file"""
    try:
        if os.path.exists(f".secure_{key}"):
            with open(f".secure_{key}", "r") as f:
                encrypted = f.read()
            return decrypt_data(encrypted)
    except Exception as e:
        print(f"Failed to load secure data for {key}: {e}")
    return ""

# Example: Store API keys securely
DISCORD_WEBHOOK = get_env_var("DISCORD_WEBHOOK") or load_secure_data("discord_webhook") or "https://discord.com/api/webhooks/default"
API_KEY_YFINANCE = get_env_var("API_KEY_YFINANCE") or load_secure_data("api_key_yfinance") or ""