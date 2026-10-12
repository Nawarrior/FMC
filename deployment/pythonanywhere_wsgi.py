import os
import sys
from pathlib import Path


USERNAME = os.environ.get("USER", "TU_USUARIO_PYTHONANYWHERE")
PROJECT_DIR = Path.home() / "FMC"

if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

os.environ.setdefault("DATABASE_PATH", str(PROJECT_DIR / "FMC.db"))
os.environ.setdefault("FLASK_SECRET_KEY", "CAMBIA_ESTA_CLAVE_LARGA_Y_ALEATORIA")
os.environ.setdefault("APP_BASE_URL", f"https://{USERNAME}.pythonanywhere.com")
os.environ.setdefault("GOOGLE_LOGIN_ENABLED", "false")

from app import app as application
