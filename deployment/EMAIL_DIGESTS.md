# Resumenes de correo: 09:30 y 16:30 en Chile

Las busquedas, publicaciones y mensajes guardan sus avisos sin esperar al servidor de correo. GitHub Actions llama al sitio dos veces al dia; cada usuario recibe un solo resumen por horario con sus coincidencias nuevas y mensajes sin leer. Sin novedades no recibe correo. La recuperacion de clave sigue siendo inmediata.

## Activar en PythonAnywhere y GitHub

En Bash de PythonAnywhere:

```bash
cd ~/FMC
git pull --ff-only origin main
source .venv/bin/activate
pip install -r requirements.txt
python deployment/configure_digest.py
```

El asistente guarda una clave privada en `.env` y muestra el valor para copiarlo directamente a GitHub. No compartas ese valor en un chat ni lo agregues a un archivo del repositorio. Si repites el asistente conserva la misma clave.

1. Abre https://github.com/Nawarrior/FMC/settings/secrets/actions
2. Pulsa **New repository secret**.
3. Nombre: `DIGEST_SCHEDULER_TOKEN`.
4. Valor: la clave que mostro el asistente. Guarda el secret.
5. En PythonAnywhere pulsa **Web > Reload**.
6. En GitHub, abre **Actions > Resumen de correos** y comprueba que el workflow este habilitado.

No necesitas configurar tareas de PythonAnywhere. Las cuentas gratuitas recientes no disponen de tareas programadas y las antiguas solo tienen una tarea diaria.

## Horarios y comprobacion

El workflow `.github/workflows/email-digests.yml` usa `30 9,16 * * *` con la zona `America/Santiago`. Se adapta automaticamente al horario de verano. GitHub puede retrasar las ejecuciones; el servidor acepta hasta dos horas de retraso, sin repetir el resumen de un mismo horario.

Puedes usar **Run workflow** para comprobar la conexion: fuera de la ventana de envio devolvera `slot: null` y no enviara correos; dentro de la ventana procesara los pendientes. Los logs solo muestran el horario y los contadores de envios y fallos. Si hay fallos SMTP los avisos quedan pendientes.

En repositorios publicos GitHub puede desactivar los workflows programados despues de 60 dias sin actividad; revisa **Actions** si dejan de ejecutarse.

## Evitar repeticiones

- Se registra cada resumen por destinatario, fecha y horario: repetir la llamada no vuelve a enviar un resumen completado.
- Las coincidencias se identifican por destinatario, usuario que busca/ofrece, nombre de carta y direccion del aviso. Las ediciones y las busquedas repetidas otros dias no generan un segundo correo de la misma coincidencia.
- Si aparecen novedades despues de un resumen completado, esperan hasta el siguiente horario.
- Los mensajes historicos anteriores a esta actualizacion no se envian retroactivamente. Solo se incluyen los nuevos mensajes pendientes que el destinatario aun no haya leido.
- Los avisos dentro del sitio siguen apareciendo inmediatamente.

Documentacion: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule y https://help.pythonanywhere.com/pages/ScheduledTasks/
