# Publicar FMC en PythonAnywhere gratis

PythonAnywhere es una buena opcion gratuita para este proyecto porque soporta Flask, permite una app web gratis y `api.scryfall.com` esta en la allowlist de cuentas gratuitas.

## 1. Crear la app

1. Crea una cuenta en `https://www.pythonanywhere.com/`.
2. En una consola Bash de PythonAnywhere, clona el repo:

```bash
git clone https://github.com/Nawarrior/FMC.git
cd FMC
```

3. Crea entorno virtual:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Configurar Web app

1. Ve a **Web**.
2. Crea una nueva web app.
3. Elige **Manual configuration**.
4. Elige Python 3.13 o la version mas cercana disponible.
5. En **Virtualenv**, escribe, cambiando `TU_USUARIO_PYTHONANYWHERE` por tu usuario:

```text
/home/TU_USUARIO_PYTHONANYWHERE/FMC/.venv
```

6. Edita el archivo WSGI y reemplaza su contenido por el de `deployment/pythonanywhere_wsgi.py`.
7. Cambia `CAMBIA_ESTA_CLAVE_LARGA_Y_ALEATORIA` por una clave secreta real.

## 3. Base de datos

En PythonAnywhere se usara:

```text
/home/TU_USUARIO_PYTHONANYWHERE/FMC/FMC.db
```

La app crea las tablas automaticamente al arrancar.

Para subir una copia de tu base local, carga `deployment/FMC.db` desde el panel **Files** de PythonAnywhere y dejala como:

```text
/home/TU_USUARIO_PYTHONANYWHERE/FMC/FMC.db
```

## 4. Recargar

Vuelve a la pestana **Web** y presiona **Reload**.

La app quedara disponible en:

```text
https://TU_USUARIO_PYTHONANYWHERE.pythonanywhere.com
```

## 5. Correo con Gmail

1. Activa la verificacion en dos pasos en la cuenta Gmail que usara el sitio como remitente.
2. Genera una contrasena de aplicacion en https://myaccount.google.com/apppasswords. No uses la contrasena normal de Google.
3. En una consola Bash de PythonAnywhere, ejecuta:

```bash
cd ~/FMC
git pull --ff-only origin main
source .venv/bin/activate
pip install -r requirements.txt
python deployment/configure_email.py
```

4. Acepta `smtp.gmail.com` y el puerto `587`. Ingresa tu correo y la contrasena de aplicacion cuando el asistente lo solicite. La clave no se muestra ni se guarda en el historial de comandos. El asistente crea el archivo privado `.env`, ignorado por Git; la app lo carga al arrancar, sin modificar el WSGI instalado.
5. Comprueba la conexion sin enviar correos:

```bash
python deployment/check_email.py
```

6. Para enviar una prueba a tu propio correo:

```bash
python deployment/check_email.py --to tu-correo@gmail.com
```

7. Pulsa **Web > Reload**. Los mensajes, recuperaciones de clave y nuevas coincidencias utilizaran esta cuenta. Las columnas de seguimiento del envio se agregan automaticamente a la base de datos.

Si la autenticacion falla, revisa que uses una contrasena de aplicacion vigente. Si hay un fallo de conexion, comprueba host, puerto y restricciones de red del plan. El comprobador no muestra contrasenas y distingue esos errores.

## 6. Notas

- El buscador de cartas sigue consultando Scryfall por API.
- La cuenta gratuita de PythonAnywhere expira la web app si no se renueva periodicamente desde el panel.
- El correo SMTP puede requerir configuracion adicional segun el proveedor y las restricciones de la cuenta gratuita.
