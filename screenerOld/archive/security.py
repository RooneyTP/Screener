"""
security.py — security helpers.
Gunakan environment variable untuk menyimpan kunci API, bukan hardcode.
.env file: Buat file .env di root folder proyek.
"""

import os
import sys

# Tambahan fungsi untuk keamanan
def load_env_file(env_path=".env"):
    """
    Load environment variables from .env file.
    Format: KEY=VALUE (one per line)
    Skip lines starting with #.
    """
    if not os.path.exists(env_path):
        return
    
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key:
                os.environ[key] = value

# Auto-load .env at import time
load_env_file()

def get_env_var(key: str, default: str = "") -> str:
    """Get environment variable, with optional default."""
    return os.environ.get(key, default)
