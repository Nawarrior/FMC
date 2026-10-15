"""Create the private shared key for the GitHub Actions scheduler."""
from pathlib import Path
import os
import secrets

from dotenv import dotenv_values, set_key


def main() -> None:
    path = Path(__file__).resolve().parents[1] / ".env"
    token = dotenv_values(path).get("DIGEST_SCHEDULER_TOKEN") or secrets.token_urlsafe(32)
    path.touch(mode=0o600, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o600)
    set_key(str(path), "DIGEST_SCHEDULER_TOKEN", token)
    print("Guarda este valor como secret DIGEST_SCHEDULER_TOKEN del repositorio Nawarrior/FMC:")
    print(token)
    print("No lo compartas en el chat. Despues pulsa Web > Reload en PythonAnywhere.")


if __name__ == "__main__":
    main()
