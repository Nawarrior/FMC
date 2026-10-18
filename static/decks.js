(() => {
  const app = document.getElementById('deck-app');
  if (!app) return;
  let deck = JSON.parse(document.getElementById('deck-initial').textContent);
  let view = 'list', board = 'main', busy = false;
  const base = app.dataset.base, csrf = app.dataset.csrf;
  const readonly = app.dataset.readonly === 'true';
  let analysisVersion = 0, analysisController;
  const zones = {main:'Principal', commander:'Comandante', side:'Sideboard', maybe:'Considerando'};
  const colorNames = {W:'Blanco', U:'Azul', B:'Negro', R:'Rojo', G:'Verde'};
  const status = document.getElementById('deck-status');
  const retry = document.getElementById('deck-retry');
  const groups = document.getElementById('deck-cards');
  const dialog = document.getElementById('deck-card-dialog');
  const preview = document.createElement('div'); preview.className = 'deck-hover-preview'; preview.hidden = true; preview.setAttribute('aria-hidden', 'true');
  const previewImage = document.createElement('img'); preview.append(previewImage); document.body.append(preview);
  const hidePreview = () => {preview.hidden = true;};
  function positionPreview(x, y) {
    const box = preview.getBoundingClientRect(), gap = 20, margin = 12;
    const left = x + gap + box.width <= window.innerWidth - margin ? x + gap : x - box.width - gap;
    preview.style.left = Math.max(margin, Math.min(left, window.innerWidth - box.width - margin)) + 'px';
    preview.style.top = Math.max(margin, Math.min(y - 30, window.innerHeight - box.height - margin)) + 'px';
  }
  function showPreview(row, x, y) {
    if (!row.card?.image_url || dialog.open) return;
    previewImage.src = row.card.image_url; previewImage.alt = row.card.name || row.name;
    preview.hidden = false; positionPreview(x, y);
  }
  previewImage.addEventListener('error', hidePreview);
  window.addEventListener('scroll', hidePreview, {passive:true, capture:true});
  window.addEventListener('resize', hidePreview);
  document.addEventListener('keydown', event => {if (event.key === 'Escape') hidePreview();});
  const node = (tag, text, className) => {const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (className) el.className = className; return el;};
  const sum = rows => rows.reduce((n, row) => n + row.quantity, 0);
  const ready = row => row.status === 'ready';
  const type = row => {
    const line = row.card?.type_line || '';
    for (const [term, label] of [['Land','Tierras'],['Creature','Criaturas'],['Planeswalker','Planeswalkers'],['Instant','Instantáneos'],['Sorcery','Conjuros'],['Artifact','Artefactos'],['Enchantment','Encantamientos'],['Battle','Batallas']]) if (line.includes(term)) return label;
    return 'Otras cartas';
  };
  const message = (text, error = false) => {status.textContent = text; status.classList.toggle('error', error);};
  function lock(value) {
    busy = value;
    app.querySelectorAll('[data-mutate], form button[type="submit"], #deck-delete, #deck-publish, #deck-clear').forEach(el => {el.disabled = value;});
  }
  function mana(cost) {
    const span = node('span', undefined, 'deck-mana'); span.setAttribute('aria-label', cost || 'Sin coste de maná');
    for (const token of (cost || '').match(/\{[^}]+\}/g) || []) {
      const symbol = token.slice(1, -1), el = node('span', symbol, 'deck-mana-symbol');
      if (/^[WUBRG]$/.test(symbol)) el.dataset.color = symbol;
      span.append(el);
    }
    return span;
  }
  function image(card, className = '') {
    const img = node('img', undefined, className); img.src = card.image_url; img.alt = card.name; img.loading = 'lazy'; img.decoding = 'async';
    img.addEventListener('error', () => {img.hidden = true;}); return img;
  }
  function inspect(row) {
    hidePreview();
    const target = document.getElementById('deck-card-detail'); target.replaceChildren();
    if (row.card?.image_url) target.append(image(row.card));
    const copy = node('div'); copy.append(node('h2', row.card?.name || row.name), mana(row.card?.mana_cost), node('p', row.card?.type_line || row.error), node('p', row.card?.oracle_text || '', 'deck-oracle'));
    if (row.card) {const link = node('a', 'Ver carta y ediciones →', 'saved-link'); link.href = '/card/' + encodeURIComponent(row.card.id); link.target = '_blank'; link.rel = 'noopener'; copy.append(link);}
    target.append(copy); dialog.showModal();
  }
  function action(label, callback) {
    const button = node('button', label, 'deck-mini-button'); button.type = 'button'; button.dataset.mutate = ''; button.disabled = busy; button.addEventListener('click', callback); return button;
  }
  function edit(row, fields) {return mutate('/cards/' + encodeURIComponent(row.row_id), fields);}
  function cardRow(row) {
    const el = node('article', undefined, 'deck-card' + (!ready(row) ? ' deck-card-unresolved' : ''));
    el.draggable = !readonly;
    el.addEventListener('dragstart', event => {hidePreview(); event.dataTransfer.setData('text/plain', row.row_id);});
    if (view !== 'list') {
      const art = node('button', undefined, 'deck-card-art'); art.type = 'button'; art.setAttribute('aria-label', 'Ver ' + row.name); art.addEventListener('click', () => inspect(row));
      if (row.card?.image_url) art.append(image(row.card)); else art.append(node('span', row.status === 'pending' ? 'Validando…' : 'Revisar carta', 'deck-card-placeholder'));
      const qty = node('span', '×' + row.quantity, 'deck-card-badge'); art.append(qty); el.append(art);
    }
    const name = node('button', row.name, 'deck-card-name'); name.type = 'button'; name.addEventListener('click', () => inspect(row));
    name.addEventListener('pointerenter', event => {if (event.pointerType !== 'touch') showPreview(row, event.clientX, event.clientY);});
    name.addEventListener('pointermove', event => {if (!preview.hidden) positionPreview(event.clientX, event.clientY);});
    name.addEventListener('pointerleave', hidePreview);
    name.addEventListener('focus', () => {const rect = name.getBoundingClientRect(); showPreview(row, rect.right, rect.top);});
    name.addEventListener('blur', hidePreview);
    const copy = node('div', undefined, 'deck-card-copy');
    if (view === 'list') copy.append(node('span', row.quantity, 'deck-list-quantity'), name);
    else copy.append(name, mana(row.card?.mana_cost));
    if (row.error) copy.append(node('small', row.error, 'deck-card-error'));
    if (row.edition_note) copy.append(node('small', row.edition_note, 'deck-edition-note'));
    if (readonly) {el.append(copy); return el;}
    const controls = node('div', undefined, 'deck-card-controls');
    const minus = action('−', () => edit(row, {quantity:row.quantity - 1})); minus.setAttribute('aria-label', 'Quitar una copia de ' + row.name); minus.disabled = busy || row.quantity <= 1;
    const plus = action('+', () => edit(row, {quantity:row.quantity + 1})); plus.setAttribute('aria-label', 'Agregar una copia de ' + row.name); plus.disabled = busy || row.quantity >= 9999;
    const quantity = node('input'); quantity.type = 'number'; quantity.min = '1'; quantity.max = '9999'; quantity.value = row.quantity; quantity.dataset.mutate = ''; quantity.disabled = busy; quantity.setAttribute('aria-label', 'Cantidad de ' + row.name);
    quantity.addEventListener('change', () => {if (quantity.reportValidity()) edit(row, {quantity:quantity.value});});
    const select = node('select'); select.dataset.mutate = ''; select.disabled = busy; select.setAttribute('aria-label', 'Zona de ' + row.name);
    for (const [key, label] of Object.entries(zones)) {const opt = node('option', label); opt.value = key; opt.selected = row.board === key; select.append(opt);}
    select.addEventListener('change', () => edit(row, {board:select.value}));
    const remove = action('×', () => {if (confirm('¿Quitar ' + row.name + ' del deck?')) edit(row, {action:'delete'});}); remove.setAttribute('aria-label', 'Eliminar ' + row.name);
    controls.append(minus, quantity, plus, select, remove);
    if (row.status === 'error') controls.append(action('Corregir', () => {const name = prompt('Nombre completo de la carta:', row.name); if (name?.trim()) edit(row, {name:name.trim()});}));
    copy.append(controls); el.append(copy); return el;
  }
  function stats() {
    const main = deck.cards.filter(row => ['main','commander'].includes(row.board) && ready(row));
    const spells = main.filter(row => type(row) !== 'Tierras');
    document.getElementById('deck-total').textContent = sum(main);
    document.getElementById('deck-total-extra').textContent = `${sum(deck.cards.filter(row => row.board === 'side' && ready(row)))} sideboard · ${deck.cards.filter(row => !ready(row)).length} filas por revisar`;
    for (const tab of app.querySelectorAll('[data-count]')) tab.textContent = sum(deck.cards.filter(row => row.board === tab.dataset.count));
    document.getElementById('deck-average').textContent = sum(spells) ? (spells.reduce((n, row) => n + row.card.cmc * row.quantity, 0) / sum(spells)).toFixed(2) : '—';
    const histogram = Array(8).fill(0); for (const row of spells) histogram[Math.min(7, Math.floor(row.card.cmc))] += row.quantity;
    const curve = document.getElementById('deck-curve'); curve.replaceChildren(); const maximum = Math.max(...histogram, 1);
    histogram.forEach((count, index) => {const column = node('div', undefined, 'deck-curve-column'); column.setAttribute('aria-label', `Maná ${index === 7 ? '7 o más' : index}: ${count} cartas`); const bar = node('div', undefined, 'deck-curve-bar'); bar.style.height = Math.max(2, count / maximum * 100) + 'px'; column.append(node('small', count), bar, node('span', index === 7 ? '7+' : index)); curve.append(column);});
    const types = document.getElementById('deck-types'); types.replaceChildren(); const counts = {};
    for (const row of main) counts[type(row)] = (counts[type(row)] || 0) + row.quantity;
    for (const [label, count] of Object.entries(counts)) {const line = node('div', undefined, 'deck-stat-row'); line.append(node('span', label), node('strong', count)); types.append(line);}
  }
  function insights(result) {
    const price = document.getElementById('deck-price');
    price.textContent = result.usd_total === null ? 'Sin precio disponible' : '≈ ' + new Intl.NumberFormat('es-CL', {style:'currency', currency:'USD', currencyDisplay:'code', minimumFractionDigits:2}).format(Number(result.usd_total));
    document.getElementById('deck-price-coverage').textContent = `${result.priced_copies} copias con precio` + (result.missing_price_copies ? ` · ${result.missing_price_copies} sin precio · total parcial` : '') + (result.price_checked_at ? ' · ' + new Date(result.price_checked_at).toLocaleDateString('es-CL') : '');
    const commander = document.getElementById('deck-commander-insights'); commander.hidden = !result.commander;
    if (result.commander) {
      document.getElementById('deck-bracket').textContent = result.bracket_minimum === null ? 'Bracket mínimo: pendiente' : result.bracket_minimum === 4 ? 'Bracket mínimo: 4 · Optimized (o 5 · cEDH)' : result.bracket_minimum === 3 ? 'Bracket mínimo: 3 · Upgraded' : 'Bracket mínimo: 1 · Exhibition';
      document.getElementById('deck-game-changer-count').textContent = `${result.game_changer_count} Game Changers detectados` + (result.unknown_game_changer_copies ? ` · ${result.unknown_game_changer_copies} copias pendientes de clasificar` : '') + ' · principal y comandante';
      const changes = document.getElementById('deck-game-changers'); changes.replaceChildren();
      for (const entry of result.game_changers) {const button = node('button', entry.name + (entry.quantity > 1 ? ' ×' + entry.quantity : ''), 'deck-game-changer'); button.type = 'button'; const row = {name:entry.name, card:{name:entry.name, id:entry.card_id, image_url:entry.image_url}}; button.addEventListener('click', () => inspect(row)); button.addEventListener('pointerenter', event => {if (event.pointerType !== 'touch') showPreview(row, event.clientX, event.clientY);}); button.addEventListener('pointerleave', hidePreview); changes.append(button);}
    }
  }
  async function refreshInsights() {
    const version = ++analysisVersion;
    analysisController?.abort(); analysisController = new AbortController();
    const signal = analysisController.signal, progress = document.getElementById('deck-analysis-status'), retry = document.getElementById('deck-analysis-retry');
    retry.hidden = true; progress.textContent = 'Consultando precios y clasificación…';
    try {
      let result;
      do {
        const response = await fetch(app.dataset.analysis, {signal, headers:{Accept:'application/json'}});
        result = await response.json();
        if (!response.ok) throw new Error(result.error || 'No se pudo calcular el resumen.');
        if (version !== analysisVersion) return;
        insights(result); progress.textContent = result.pending ? `${result.pending} cartas pendientes de consultar…` : 'Estimación de referencia; no incluye envío ni impuestos.';
        if (result.pending) await new Promise(done => setTimeout(done, 150));
      } while (result.pending && !signal.aborted);
    } catch (error) {if (error.name !== 'AbortError' && version === analysisVersion) {if (document.getElementById('deck-price').textContent === 'Calculando…') document.getElementById('deck-price').textContent = 'Sin estimación'; progress.textContent = 'No se pudo actualizar la estimación. Puedes reintentar.'; retry.hidden = false;}}
  }
  function render() {
    hidePreview();
    document.getElementById('deck-name').textContent = deck.name; document.getElementById('deck-format').textContent = deck.format; document.getElementById('deck-description').textContent = deck.description;
    document.getElementById('deck-visibility').textContent = deck.published ? 'Deck publicado' : 'Deck privado';
    const first = deck.cards[0]?.card, cover = document.getElementById('deck-header-image');
    cover.hidden = !first?.art_url; if (first?.art_url) {cover.src = first.art_url; cover.alt = 'Ilustración de ' + first.name;}
    document.getElementById('deck-commander-insights').hidden = deck.format !== 'Commander';
    if (!readonly) {
      document.getElementById('deck-publish').textContent = deck.published ? 'Ocultar del catálogo' : 'Publicar deck';
      document.getElementById('deck-public-link').hidden = !deck.published;
    }
    groups.className = 'deck-groups deck-view-' + view; groups.replaceChildren(); stats();
    const query = document.getElementById('deck-filter').value.trim().toLocaleLowerCase();
    const rows = deck.cards.filter(row => row.board === board && (!query || [row.name, row.card?.oracle_text, row.card?.type_line].join(' ').toLocaleLowerCase().includes(query)));
    const order = document.getElementById('deck-sort').value;
    rows.sort((a, b) => (order === 'mana' ? (a.card?.cmc || 0) - (b.card?.cmc || 0) : 0) || a.name.localeCompare(b.name));
    const groupBy = document.getElementById('deck-group').value, grouped = new Map();
    for (const row of rows) {
      let key = !ready(row) ? 'Por revisar' : type(row);
      if (ready(row) && groupBy === 'mana') key = 'Maná ' + row.card.cmc;
      if (ready(row) && groupBy === 'color') key = row.card.colors.length > 1 ? 'Multicolor' : colorNames[row.card.colors[0]] || 'Incoloro';
      if (!grouped.has(key)) grouped.set(key, []); grouped.get(key).push(row);
    }
    for (const [key, entries] of [...grouped].sort((a, b) => a[0].localeCompare(b[0], 'es', {numeric:true}))) {
      const section = node('section', undefined, 'deck-group'); const heading = node('h2', key); heading.append(node('span', sum(entries), 'deck-group-count'));
      const cards = node('div', undefined, 'deck-group-cards'); for (const row of entries) cards.append(cardRow(row)); section.append(heading, cards); groups.append(section);
    }
    if (!rows.length) {const empty = node('div', undefined, 'deck-empty'); empty.append(node('span', '✦'), node('h2', query ? 'Sin coincidencias' : 'Una estrategia por construir'), node('p', query ? 'Prueba otro nombre o texto de carta.' : 'Agrega una carta o carga un listado para esta zona.')); groups.append(empty);}
    document.getElementById('deck-visible-count').textContent = `${sum(rows)} cartas · ${rows.length} filas`;
  }
  async function post(path, fields) {
    const data = fields instanceof FormData ? fields : new FormData();
    if (!(fields instanceof FormData)) for (const [key, value] of Object.entries(fields || {})) data.set(key, value);
    data.set('revision', deck.revision);
    const response = await fetch(base + path, {method:'POST', headers:{'X-CSRF-Token':csrf, 'Accept':'application/json'}, body:data});
    const payload = await response.json().catch(() => ({error:'No se pudo guardar. Comprueba tu sesión e intenta otra vez.'}));
    if (!response.ok) {
      if (response.status === 409) {const current = await fetch(base + '/data'); if (current.ok) {deck = await current.json(); render();}}
      throw new Error(payload.error || 'No se pudo completar la acción.');
    }
    return payload;
  }
  async function resolve() {
    retry.hidden = true;
    while (deck.cards.some(row => row.status === 'pending')) {
      message(`Validando cartas… ${deck.cards.filter(ready).length} listas · ${deck.cards.filter(row => row.status === 'pending').length} pendientes`);
      deck = await post('/resolve'); render();
      if (deck.cards.some(row => row.status === 'pending')) await new Promise(done => setTimeout(done, 150));
    }
    const errors = deck.cards.filter(row => row.status === 'error').length;
    retry.hidden = !deck.cards.some(row => row.status === 'error' && row.error?.startsWith('No encontrada.'));
    message(errors ? `Deck guardado. Hay ${errors} filas por corregir; puedes revisarlas en sus zonas.` : 'Todos los cambios están guardados.', Boolean(errors));
  }
  async function mutate(path, fields) {
    if (busy || readonly) return;
    let saved = false;
    lock(true); message('Guardando…');
    try {deck = await post(path, fields); saved = true; render(); await resolve();}
    catch (error) {message(error.message, true); retry.hidden = !deck.cards.some(row => row.status === 'pending' || row.status === 'error' && row.error?.startsWith('No encontrada.'));}
    finally {lock(false); render(); refreshInsights();}
    return saved;
  }
  app.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => {view = button.dataset.view; app.querySelectorAll('[data-view]').forEach(b => b.setAttribute('aria-pressed', String(b === button))); render();}));
  app.querySelectorAll('[data-board]').forEach(button => {
    button.addEventListener('click', () => {board = button.dataset.board; app.querySelectorAll('[data-board]').forEach(b => b.setAttribute('aria-pressed', String(b === button))); render();});
    if (readonly) return;
    button.addEventListener('dragover', event => {event.preventDefault(); button.classList.add('deck-drop-target');});
    button.addEventListener('dragleave', () => button.classList.remove('deck-drop-target'));
    button.addEventListener('drop', event => {event.preventDefault(); button.classList.remove('deck-drop-target'); const row = deck.cards.find(row => row.row_id === event.dataTransfer.getData('text/plain')); if (row) edit(row, {board:button.dataset.board});});
  });
  for (const id of ['deck-group','deck-sort','deck-filter']) document.getElementById(id).addEventListener('input', render);
  document.getElementById('deck-close-dialog').addEventListener('click', () => dialog.close());
  const membershipDialog = document.getElementById('deck-membership-dialog');
  document.getElementById('deck-optimize').addEventListener('click', () => {hidePreview(); membershipDialog.showModal();});
  document.getElementById('deck-membership-close').addEventListener('click', () => membershipDialog.close());
  document.getElementById('deck-analysis-retry').addEventListener('click', refreshInsights);
  document.getElementById('deck-header-image').addEventListener('error', event => {event.currentTarget.hidden = true;});
  if (readonly) {render(); refreshInsights(); return;}
  document.getElementById('deck-publish').addEventListener('click', () => mutate('/publish', {published:deck.published ? '0' : '1'}));
  document.getElementById('deck-import-form').addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return;
    const form = event.currentTarget, values = new FormData(form);
    if (deck.cards.length && (values.get('cards_text')?.trim() || values.get('file')?.size) && !confirm('Esta carga reemplazará todas las cartas y zonas del deck. ¿Continuar?')) return;
    if (await mutate('/import', values)) form.reset();
  });
  document.getElementById('deck-add-form').addEventListener('submit', event => {event.preventDefault(); const values = new FormData(event.currentTarget); mutate('/import', {mode:'append', cards_text:values.get('quantity') + ' ' + values.get('name'), board:values.get('board')});});
  document.getElementById('deck-clear').addEventListener('click', async () => {
    if (busy || !deck.cards.length || !confirm('¿Vaciar todas las cartas y zonas de este listado? Se conservarán el nombre y los datos del deck.')) return;
    if (await mutate('/clear')) document.getElementById('deck-import').open = true;
  });
  document.getElementById('deck-metadata-form').addEventListener('submit', event => {event.preventDefault(); mutate('/edit', new FormData(event.currentTarget));});
  document.getElementById('deck-edit-toggle').addEventListener('click', () => {const panel = document.getElementById('deck-metadata'); panel.hidden = !panel.hidden;});
  document.getElementById('deck-delete').addEventListener('click', async () => {if (busy || !confirm('¿Eliminar este deck y todas sus cartas?')) return; lock(true); try {const result = await post('/delete'); window.location.assign(result.redirect);} catch (error) {message(error.message, true); lock(false);}});
  retry.addEventListener('click', async () => {if (busy) return; lock(true); try {if (deck.cards.some(row => row.status === 'error' && row.error?.startsWith('No encontrada.'))) {deck = await post('/retry'); render();} await resolve();} catch (error) {message(error.message, true); retry.hidden = false;} finally {lock(false); render(); refreshInsights();}});
  let timer, requestId = 0;
  document.getElementById('deck-card-search').addEventListener('input', event => {
    clearTimeout(timer); const query = event.target.value.trim(), id = ++requestId;
    if (query.length < 3) {document.getElementById('deck-suggestions').replaceChildren(); return;}
    timer = setTimeout(async () => {try {const result = await fetch('/api/autocomplete?q=' + encodeURIComponent(query)); if (!result.ok) return; const payload = await result.json(); if (id !== requestId) return; const list = document.getElementById('deck-suggestions'); list.replaceChildren(); for (const name of payload.items || []) {const option = node('option'); option.value = name; list.append(option);}} catch { /* The user can still enter a name. */ }}, 300);
  });
  render();
  refreshInsights();
  retry.hidden = !deck.cards.some(row => row.status === 'pending' || row.status === 'error' && row.error?.startsWith('No encontrada.'));
  if (deck.cards.some(row => row.status === 'pending')) retry.click();
})();
