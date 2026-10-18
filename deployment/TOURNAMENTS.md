# Torneos

La sección pública `/tournaments` muestra torneos, fecha, lugar o tienda y resultados ordenados por puesto. Se puede filtrar por formato. Las fichas y sus decks se consultan sin iniciar sesión, en modo lectura.

El administrador accede desde **Mantenedor Torneos** (`/tournaments/manage`). Puede crear, editar y eliminar torneos, indicando:

- Nombre, fecha y formato.
- Una tienda registrada o un lugar escrito manualmente. La asociación a una tienda es opcional; si se selecciona y no se escribe un lugar, se utiliza su dirección o nombre.
- Descripción opcional.
- Hasta 128 resultados, cada uno con puesto, jugador y nombre del deck.

Los jugadores pueden asociarse a usuarios existentes; en ese caso se utiliza su nombre registrado y se enlaza su perfil público. Para personas sin cuenta se escribe únicamente su nombre, sin crear usuarios. Una tienda no registrada también se escribe como lugar, sin crear una tienda.

Para cada resultado se puede seleccionar un deck publicado, o escribir un nombre de deck y pegar un listado de cartas opcional. Los decks privados no aparecen como opciones. Los listados propios aceptan cantidades y nombres, una carta por línea, con encabezados `Commander`, `Deck`, `Sideboard` y `Maybeboard`. Tras guardar, el navegador valida las cartas mediante Scryfall en bloques de 25 y muestra los errores para corregirlos. Los fallos de conexión se pueden reintentar. La ficha del listado utiliza las vistas, imágenes y valoración aproximada existentes de decks, sin permitir su edición al visitante.

Guardar un torneo lo hace público inmediatamente. Editar sus resultados reemplaza los anteriores; los listados que no cambian conservan sus cartas ya validadas. Las asociaciones se validan contra registros existentes y no exponen correos. Si se retira la publicación de un deck asociado, su enlace público deja de mostrarse en el torneo. El torneo conserva el nombre registrado del jugador, tienda y deck.

La tabla SQLite `tournaments` se crea automáticamente al iniciar la aplicación. No se agregan dependencias. Las modificaciones requieren rol de administrador, CSRF y una revisión vigente para evitar sobrescribir otra pestaña. El formulario tiene un límite de 2 MB; cada listado admite hasta 500 filas.
