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

## 5. Notas

- El buscador de cartas sigue consultando Scryfall por API.
- La cuenta gratuita de PythonAnywhere expira la web app si no se renueva periodicamente desde el panel.
- El correo SMTP puede requerir configuracion adicional segun el proveedor y las restricciones de la cuenta gratuita.
