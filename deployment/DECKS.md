# Mis decks

Los usuarios registrados acceden desde el menú **Mis decks** (`/decks`), visible únicamente con sesión iniciada. Los decks comienzan privados; solo su propietario puede editarlos, exportarlos, eliminarlos o elegir **Publicar deck**. No modifica las cartas Busco/Ofrezco.

**Decks publicados** (`/decks/public`) está disponible para todos, sin iniciar sesión, con filtros combinables por formato y usuario y paginación de 24 mazos. Cada mazo público se abre en modo lectura: permite cambiar vistas, filtrar y ver cartas, sin controles de edición ni peticiones de validación. El nombre del autor enlaza a su perfil público, donde hay accesos a sus cartas Busco, Ofrezco y sus mazos publicados. El propietario puede usar **Ocultar del catálogo** para retirarlo; desde ese momento la URL pública devuelve 404. La vista pública no expone correos ni notas privadas de importación. Todos los decks publicados son visibles para cualquier visitante; la edición queda reservada al propietario.

1. Crear un deck indicando nombre, formato y descripción opcional.
2. Agregar una carta con el buscador o cargar un listado pegado, TXT, CSV, TSV o Excel XLSX.
3. Las cartas se validan con Scryfall en bloques de 25. La página muestra progreso; los errores de conexión se pueden reintentar. Las cartas no encontradas permanecen visibles para corregir su nombre o eliminarlas.
4. La vista inicial muestra nombres y cantidades, con la imagen al pasar el cursor o enfocar el nombre. También permite cambiar a pilas o cuadrícula; agrupar por tipo, valor de maná o color, filtrar por nombre o texto y ordenar por nombre o maná.
5. Editar cantidades y mover cartas entre principal, comandante, sideboard y considerando. En escritorio también se pueden arrastrar a las pestañas de zona. Al pulsar una carta se abre su detalle.

La carga de un listado reemplaza todas las cartas y zonas del deck; cargarlo otra vez no suma cantidades al anterior. Agregar una carta individual utiliza el modo aditivo. Dentro del nuevo listado, las filas validadas con la misma edición y zona se consolidan sumando cantidades. Archivos vacíos o filas con cantidades/formato inválidos no reemplazan los datos existentes. **Vaciar listado** elimina las cartas y conserva nombre, descripción, formato y publicación; requiere propiedad, revisión y CSRF.

Un archivo de texto puede separar zonas con `Commander`, `Deck`, `Sideboard` y `Maybeboard`; también se reconocen `SB:` y la marca `*CMDR*`. Las ediciones pueden indicarse como `4 Lightning Bolt (M11) 146`. Excel/CSV utiliza las columnas de carga masiva existentes: Nombre y Cantidad, con Edicion y Numero opcionales. La zona se selecciona al cargar el archivo.

Límites: 100 decks por usuario, 500 filas por deck, archivos de 2 MB y cantidades de 1 a 9999. El formato es descriptivo; no se realiza comprobación de legalidad ni de restricciones de Commander. La curva y el maná medio usan las cartas validadas de principal y comandante, excluyendo tierras.

Los datos residen en la tabla SQLite `decks`; se crea al iniciar la aplicación. No hay dependencias nuevas. Cada modificación comprueba una revisión para evitar sobrescribir cambios de otra pestaña. Los GET usan los metadatos guardados y no hacen consultas a Scryfall.

El ejemplo descargable está en `static/examples/deck.txt`. No es un mazo completo ni una lista validada para un formato de torneo.

## Resumen de la cabecera

La cabecera reúne toda la información del mazo en un solo panel y muestra solo la ilustración (`art_crop`) de la primera fila. El resumen se consulta de forma asíncrona, tanto en la vista privada como pública. El valor USD suma precios por cantidad de principal, comandante y sideboard; excluye considerando. Se prefiere el precio normal de la edición cargada, con foil/etched como alternativa si falta el normal. Si no hay precio (por ejemplo, cartas digitales) no se considera cero: se muestra la cantidad sin precio y el total parcial. Los importes son de referencia, sin envío ni impuestos.

Los campos `prices` y `game_changer` provienen de Scryfall. Los decks existentes se enriquecen en bloques de 25 cartas, con caché compartido de 24 horas en `deck_card_market`; no se modifica la lista ni su revisión. Los errores de conexión permiten reintentar. Abrir la página no espera a la consulta externa.

Solo en formato Commander se listan Game Changers de principal y comandante. Siguiendo los límites oficiales consultados el 18 de octubre de 2026, el mínimo por este criterio es bracket 1 con cero, bracket 3 con una a tres copias y bracket 4 con más de tres. No se asigna automáticamente un bracket final ni se distingue 1/2 o 4/5: no se analizan combos, turnos extra, negación de tierras, velocidad o intención del piloto. Si quedan cartas sin clasificar, el bracket se marca pendiente. Fuente: https://magic.wizards.com/en/formats/commander
