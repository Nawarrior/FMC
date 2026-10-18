(() => {
  const form = document.getElementById('tournament-editor');
  if (!form) return;
  const initial = JSON.parse(document.getElementById('tournament-initial').textContent);
  let entries = initial.results;
  if (initial.submitted) {try {const parsed = JSON.parse(initial.submitted); if (Array.isArray(parsed)) entries = parsed;} catch {entries = [];}}
  const root = document.getElementById('tournament-entries'), save = document.getElementById('tournament-save');
  function field(parent, key, label, value = '', type = 'text', limit = 200) {
    const wrap = document.createElement('label'); wrap.textContent = label;
    const input = document.createElement(type === 'textarea' ? 'textarea' : 'input'); input.dataset.key = key; input.value = value ?? '';
    if (type === 'textarea') input.rows = 6; else input.type = type;
    input.maxLength = limit; if (type === 'number') {input.min = 1; input.max = 10000; input.required = true;}
    wrap.append(input); parent.append(wrap); return input;
  }
  function select(parent, key, label, options, idKey, selected) {
    const wrap = document.createElement('label'); wrap.textContent = label;
    const control = document.createElement('select'); control.dataset.key = key;
    const empty = document.createElement('option'); empty.value = ''; empty.textContent = 'Sin asociar'; control.append(empty);
    for (const value of options) {const opt = document.createElement('option'); opt.value = value[idKey]; opt.textContent = value.name; control.append(opt);}
    if (selected && !options.some(value => value[idKey] === selected)) {const unavailable = document.createElement('option'); unavailable.value = selected; unavailable.textContent = 'Asociación no disponible: selecciona Sin asociar'; control.append(unavailable);}
    control.value = selected || ''; wrap.append(control); parent.append(wrap); return control;
  }
  function add(entry = {}) {
    if (root.children.length >= 128) {alert('Puedes registrar hasta 128 resultados.'); return;}
    const panel = document.createElement('fieldset'); panel.className = 'tournament-entry'; panel.dataset.entryId = entry.entry_id || '';
    const title = document.createElement('legend'); title.textContent = 'Resultado'; panel.append(title);
    const grid = document.createElement('div'); grid.className = 'deck-form-grid'; panel.append(grid);
    field(grid, 'position', 'Puesto en el torneo', entry.position || root.children.length + 1, 'number');
    const player = field(grid, 'player_name', 'Nombre del jugador', entry.player_name, 'text', 160);
    const user = select(panel, 'user_id', 'Asociar a un usuario registrado (opcional)', initial.users, 'user_id', entry.user_id);
    const deckName = field(panel, 'deck_name', 'Nombre del deck', entry.deck_name);
    const deck = select(panel, 'deck_id', 'Asociar a un deck publicado (opcional)', initial.decks, 'deck_id', entry.deck_id);
    const list = field(panel, 'deck_text', 'Listado opcional del deck (texto, una carta por línea)', entry.deck_text, 'textarea', 200000);
    list.placeholder = 'Deck\n4 Lightning Bolt\n20 Mountain\n\nSideboard\n2 Duress';
    function association() {
      player.required = !user.value; player.readOnly = Boolean(user.value);
      const selectedUser = initial.users.find(value => value.user_id === user.value); if (selectedUser) player.value = selectedUser.name;
      deckName.required = !deck.value; deckName.readOnly = Boolean(deck.value); list.disabled = Boolean(deck.value);
      const selectedDeck = initial.decks.find(value => value.deck_id === deck.value); if (selectedDeck) deckName.value = selectedDeck.name;
    }
    user.addEventListener('change', association); deck.addEventListener('change', association); association();
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'deck-danger'; remove.textContent = 'Quitar resultado'; remove.addEventListener('click', () => {if (confirm('¿Quitar este resultado del formulario?')) panel.remove();}); panel.append(remove); root.append(panel);
  }
  for (const entry of entries) if (entry && typeof entry === 'object') add(entry);
  document.getElementById('tournament-add-result').addEventListener('click', () => add());
  form.addEventListener('submit', () => {document.getElementById('tournament-results-json').value = JSON.stringify(Array.from(root.children, panel => {
    const entry = {entry_id:panel.dataset.entryId}; for (const input of panel.querySelectorAll('[data-key]')) entry[input.dataset.key] = input.disabled ? '' : input.value; return entry;
  }));});
  save.disabled = false;
  const progress = document.getElementById('tournament-validation-status'), retry = document.getElementById('tournament-validation-retry'), errors = document.getElementById('tournament-validation-errors');
  let busy = false;
  async function resolve() {
    if (busy || !form.dataset.resolve) return;
    busy = true; save.disabled = true; retry.hidden = true;
    try {
      let result;
      do {
        progress.textContent = 'Validando los listados de cartas…';
        const data = new FormData(); data.set('revision', form.querySelector('[name="revision"]').value);
        const response = await fetch(form.dataset.resolve, {method:'POST', headers:{'X-CSRF-Token':form.dataset.csrf}, body:data});
        result = await response.json(); if (!response.ok) throw new Error(result.error || 'No se pudieron validar los listados.');
        document.querySelectorAll('[data-current-revision]').forEach(input => {input.value = result.revision;});
        errors.replaceChildren(); for (const error of result.errors) {const li = document.createElement('li'); li.textContent = error; errors.append(li);}
        if (result.pending) await new Promise(done => setTimeout(done, 150));
      } while (result.pending);
      progress.textContent = result.errors.length ? 'Hay cartas no encontradas. Corrige sus nombres en el listado y guarda el torneo.' : 'Los listados de cartas están validados.';
    } catch (error) {progress.textContent = error.message; retry.hidden = false;}
    finally {busy = false; save.disabled = false;}
  }
  retry.addEventListener('click', resolve);
  if (!initial.submitted && initial.results.some(entry => (entry.cards || []).some(row => row.status === 'pending' || row.status === 'error'))) resolve();
})();
