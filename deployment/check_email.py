"""Check the SMTP connection; sending a test requires an explicit --to address."""
import argparse
from pathlib import Path
import smtplib
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mailer import open_smtp, send_email, smtp_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Comprueba SMTP sin mostrar credenciales.")
    parser.add_argument("--to", help="Destinatario de un correo de prueba (opcional)")
    args = parser.parse_args()
    try:
        settings = smtp_settings()
        if settings is None:
            print("Falta configurar SMTP. Ejecuta: python deployment/configure_email.py")
            return 1
        if args.to:
            send_email(args.to, "Prueba de correo Findmycard", "El envio de correo de Findmycard esta funcionando.")
            print("El servidor SMTP acepto el correo de prueba. Revisa la bandeja de entrada y spam.")
        else:
            with open_smtp(settings):
                print("Conexion y autenticacion SMTP correctas. No se envio ningun correo.")
        return 0
    except smtplib.SMTPAuthenticationError:
        print("Autenticacion rechazada. Revisa el usuario y la contrasena de aplicacion del proveedor.")
    except smtplib.SMTPRecipientsRefused:
        print("El servidor rechazo el destinatario de prueba.")
    except (OSError, smtplib.SMTPException):
        print("No se pudo conectar o completar el envio. Revisa host, puerto, TLS y las restricciones del alojamiento.")
    except ValueError as exc:
        print(f"Configuracion no valida: {exc}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
