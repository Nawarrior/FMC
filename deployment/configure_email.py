"""Configure SMTP interactively without exposing the password in commands or Git."""
from getpass import getpass
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import set_key


def main() -> None:
    host = input("Servidor SMTP [smtp.gmail.com]: ").strip() or "smtp.gmail.com"
    port = int(input("Puerto SMTP [587]: ").strip() or "587")
    if not 1 <= port <= 65535:
        raise ValueError("Puerto no valido.")
    username = input("Correo de la cuenta remitente: ").strip()
    password = getpass("Contrasena de aplicacion SMTP (no se muestra): ")
    if host.lower() == "smtp.gmail.com":
        password = password.replace(" ", "")
    if not username or "@" not in username or not password:
        raise ValueError("Debes indicar el correo y la contrasena de aplicacion.")
    sender = input(f"Remitente [{username}]: ").strip() or username
    base_url = input("URL del sitio [https://nawarrior.pythonanywhere.com]: ").strip() or "https://nawarrior.pythonanywhere.com"
    values = {
        "SMTP_HOST": host, "SMTP_PORT": str(port), "SMTP_USERNAME": username,
        "SMTP_PASSWORD": password, "SMTP_FROM_EMAIL": sender,
        "SMTP_USE_SSL": "true" if port == 465 else "false",
        "SMTP_USE_STARTTLS": "false" if port == 465 else "true",
        "APP_BASE_URL": base_url.rstrip("/"),
    }
    path = Path(__file__).resolve().parents[1] / ".env"
    path.touch(mode=0o600, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o600)
    for key, value in values.items():
        set_key(str(path), key, value)
    print("Configuracion guardada en .env. No se envio ningun correo.")
    print("Ahora ejecuta: python deployment/check_email.py")
    print("Despues pulsa Web > Reload en PythonAnywhere.")


if __name__ == "__main__":
    main()
