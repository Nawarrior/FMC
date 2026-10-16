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

## Carga masiva de cartas

En **Mi listado > Carga masiva de cartas** puedes subir Excel `.xlsx`, CSV, TSV o TXT, o pegar un listado. Elige **Busco** u **Ofrezco** como estado por defecto. Se permiten hasta 500 filas y 2 MB por carga.

Para texto, usa una carta por linea, con cantidad opcional:

```text
4 Duress
2x Thoughtseize
Lightning Bolt
1 Opt (M21) 59
```

Para Excel o CSV usa las columnas `Nombre`, `Cantidad`, `Edicion`, `Numero`, `Nota` y `Estado`. Solo `Nombre` es obligatoria; si omites cantidad se usa 1. `Edicion` es el codigo del set, y `Numero` permite elegir una impresion concreta. La columna `Estado`, si existe, permite mezclar Busco y Ofrezco. En Excel se lee la primera hoja; convierte los archivos antiguos `.xls` a `.xlsx`.

La vista previa valida en Scryfall por grupos de 25. Muestra las cartas reconocidas, ediciones propuestas y errores. Nada se guarda hasta pulsar **Importar seleccionadas**. Los duplicados activos del mismo usuario, edicion y estado se omiten sin sumar cantidades; cada carga solo se confirma una vez. Las coincidencias se agregan a las notificaciones y al resumen programado, sin enviar correos durante la carga.

El listado muestra 50 cartas por pagina y carga las ediciones al abrir el selector, evitando consultas por cada carta al entrar.

## Base de datos

La aplicacion usa SQLite para sus datos propios: usuarios, cartas guardadas, productos, carrito, tiendas, referencias, mensajes e historial de busquedas. La ruta predeterminada se define en `database.py` y puede cambiarse mediante `DATABASE_PATH`.

En el primer arranque migra automaticamente los datos existentes desde los archivos JSON locales si las tablas estan vacias. La busqueda y el detalle de cartas siguen consultando siempre la API de Scryfall.

Para usar otra ruta de base de datos:

```powershell
$env:DATABASE_PATH = "instance/fmc.db"
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

## Notificaciones de coincidencias

Los usuarios con sesion iniciada reciben avisos dentro del sitio, en **Notificaciones**:

- Si alguien busca una carta concreta o la guarda como **Busco**, se avisa a los usuarios que la tienen publicada como **Ofrezco**.
- Si alguien publica una carta como **Ofrezco**, se avisa a quienes la tienen en **Busco**.
- Una busqueda amplia avisa cuando el usuario selecciona una carta de los resultados.
- Las coincidencias se reconocen por nombre, incluso entre ediciones diferentes. Se excluyen las publicaciones propias, terminadas o sin cantidad disponible.
- Cada coincidencia entre dos usuarios, una carta y una direccion genera como maximo un aviso diario. Los avisos se marcan como leidos al abrir Notificaciones.

## Notificaciones por correo

Los mensajes nuevos, las respuestas y las coincidencias de cartas se agrupan en un resumen por usuario a las **09:30 y 16:30 de America/Santiago**. Solo incluye avisos pendientes: una coincidencia ya enviada no se vuelve a enviar aunque se repita otro dia. Si no hay novedades, no se envia correo. La recuperacion de clave se envia inmediatamente.

La aplicacion carga automaticamente el archivo privado `.env` de la carpeta del proyecto, tambien cuando se ejecuta mediante WSGI en PythonAnywhere. Configura el programador externo siguiendo `deployment/EMAIL_DIGESTS.md`; el codigo por si solo no activa el horario en produccion.

Para configurar Gmail sin mostrar la clave en la consola:

```bash
python deployment/configure_email.py
python deployment/check_email.py
```

El asistente solicita el correo remitente y una contrasena de aplicacion de Google. Esta requiere verificacion en dos pasos. Guarda los datos en `.env`, excluido de Git. El comprobador verifica conexion y autenticacion sin enviar correos. Para enviar una prueba a una direccion propia:

```bash
python deployment/check_email.py --to tu-correo@gmail.com
```

En PythonAnywhere, pulsa **Web > Reload** despues de configurar. No basta con definir variables en una consola Bash: la aplicacion web necesita cargar su propia configuracion. Revisa `deployment/PYTHONANYWHERE.md` para actualizar e instalar las dependencias.

Las coincidencias mantienen el aviso dentro del sitio si falla SMTP. Los correos pendientes se reintentan en una ejecucion posterior del resumen. Los mensajes que ya se leyeron en el sitio no se incluyen en el resumen.

Tambien puedes configurar SMTP mediante variables de entorno, por ejemplo en PowerShell:

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
