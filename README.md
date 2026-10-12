# Buscador de Cartas Magic

Version: `0.1.0-alpha.1`

Aplicacion web en Python que consulta la API de Scryfall para buscar cartas de Magic: The Gathering.

## Funciones

- Busqueda por texto o por sintaxis avanzada de Scryfall.
- Enter en el buscador para listar resultados.
- Vista detalle de una carta con imagen y la mayor parte de su informacion.

## Ejecutar

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Luego abre `http://127.0.0.1:5000`.

## Base de datos

La aplicacion usa SQLite para sus datos propios: usuarios, cartas guardadas, productos, carrito, tiendas, referencias, mensajes e historial de busquedas. Por defecto usa la base `C:\Proyectos\sqlite-tools-win-x64-3540000\FMC.db`.

En el primer arranque migra automaticamente los datos existentes desde los archivos JSON locales si las tablas estan vacias. La busqueda y el detalle de cartas siguen consultando siempre la API de Scryfall.

Para usar otra ruta de base de datos:

```powershell
$env:DATABASE_PATH = "C:\ruta\a\fmc.db"
python app.py
```

## Configuracion segura

Antes de usar la app con datos reales, define una clave secreta propia:

```powershell
$env:FLASK_SECRET_KEY = "cambia-esto-por-un-valor-largo-y-aleatorio"
```

Si no la defines, la app genera una clave temporal al iniciar; eso sirve para desarrollo, pero cerrara las sesiones al reiniciar.

El login con Google esta deshabilitado por defecto. Para reactivarlo, ademas de definir las credenciales, habilita la bandera:

```powershell
$env:GOOGLE_LOGIN_ENABLED = "true"
$env:GOOGLE_CLIENT_ID = "tu_client_id"
$env:GOOGLE_CLIENT_SECRET = "tu_client_secret"
```

Si publicas la app bajo HTTPS, activa cookies seguras:

```powershell
$env:SESSION_COOKIE_SECURE = "true"
```

## Publicacion gratuita

Para publicar la app gratis, usa la guia de PythonAnywhere en `deployment/PYTHONANYWHERE.md`. PythonAnywhere soporta Flask en cuentas gratuitas y permite consultar `api.scryfall.com`, que la app usa para buscar cartas.

## Notificaciones por correo

Los mensajes nuevos y las respuestas envian un aviso al correo del usuario receptor. Configura SMTP en la misma ventana de PowerShell antes de iniciar Flask:

```powershell
$env:SMTP_HOST = "smtp.gmail.com"
$env:SMTP_PORT = "587"
$env:SMTP_USERNAME = "tu-cuenta@gmail.com"
$secret = Read-Host "Clave de aplicacion SMTP" -AsSecureString
$env:SMTP_PASSWORD = [System.Net.NetworkCredential]::new("", $secret).Password
$env:SMTP_FROM_EMAIL = $env:SMTP_USERNAME
$env:APP_BASE_URL = "http://127.0.0.1:5000"
python -m flask --app app run --host 127.0.0.1 --port 5000
```

Usa los datos SMTP de tu proveedor. Con Gmail, crea una clave de aplicacion y no uses la contrasena normal de la cuenta. Para servidores que requieren SSL directo en el puerto 465, define tambien `$env:SMTP_USE_SSL = "true"`. Si SMTP no esta configurado o falla, el mensaje permanece guardado en la aplicacion y se muestra un aviso.
