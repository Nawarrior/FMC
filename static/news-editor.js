(() => {
  const form = document.getElementById('news-editor');
  if (!form) return;
  const importToggle = document.getElementById('news-import-toggle');
  const importPanel = document.getElementById('news-json-import');
  importToggle.addEventListener('click', () => {
    importPanel.hidden = !importPanel.hidden;
    importToggle.setAttribute('aria-expanded', String(!importPanel.hidden));
    if (!importPanel.hidden) document.getElementById('news-json-text').focus();
  });
  const initial = JSON.parse(document.getElementById('news-editor-initial').textContent);
  const sections = document.getElementById('news-sections');
  const summaries = document.getElementById('news-summary-items');
  const types = {text: 'Texto', highlights: 'Tarjetas numeradas', image: 'Imagen de producto', gallery: 'Galería de cartas o imágenes', links: 'Enlaces y recursos', list: 'Lista de elementos', video: 'Video de YouTube o Twitch'};

  function button(label, action) {
    const node = document.createElement('button');
    node.type = 'button'; node.textContent = label; node.addEventListener('click', action);
    return node;
  }
  function field(parent, key, label, value = '', options = {}) {
    const wrapper = document.createElement('label'); wrapper.className = 'news-field'; wrapper.textContent = label;
    const input = document.createElement(options.multiline ? 'textarea' : 'input');
    input.dataset.key = key; input.value = typeof value === 'string' ? value : '';
    input.maxLength = options.limit || 200;
    if (options.multiline) input.rows = 3; else input.type = options.url ? 'url' : 'text';
    if (options.required) input.required = true;
    wrapper.append(input); parent.append(wrapper); return input;
  }
  function values(parent) {
    const result = {};
    for (const input of parent.querySelectorAll('[data-key]')) result[input.dataset.key] = input.value;
    return result;
  }
  function controls(node, parent, label) {
    const row = document.createElement('div'); row.className = 'news-editor-actions';
    const up = button('↑ Subir', () => {if (node.previousElementSibling) parent.insertBefore(node, node.previousElementSibling); update();});
    const down = button('↓ Bajar', () => {if (node.nextElementSibling) parent.insertBefore(node.nextElementSibling, node); update();});
    up.setAttribute('aria-label', 'Subir ' + label); down.setAttribute('aria-label', 'Bajar ' + label);
    row.append(up, down, button('Quitar', () => {if (confirm('¿Quitar ' + label + '?')) {node.remove(); update();}}));
    node.append(row);
  }
  function addItem(parent, kind, item = {}) {
    if (parent.children.length >= 24) {alert('Cada sección admite hasta 24 elementos.'); return;}
    const node = document.createElement('div'); node.className = 'news-editor-entry';
    const fields = document.createElement('div'); fields.className = 'news-entry-fields news-editor-grid'; node.append(fields);
    field(fields, 'title', kind === 'links' ? 'Texto del enlace' : kind === 'video' ? 'Título del video' : 'Título o nombre de la carta', item.title, {required: kind === 'links' || kind === 'video'});
    if (kind !== 'links') field(fields, 'text', 'Descripción o pie de imagen', item.text, {multiline:true, limit:2000});
    if (kind === 'image' || kind === 'gallery') {
      field(fields, 'image_url', 'Dirección de la imagen', item.image_url, {url:true, required:true, limit:2000});
      field(fields, 'alt', 'Descripción de la imagen', item.alt, {required:true, limit:300});
      field(fields, 'credit', 'Créditos o fuente de la imagen', item.credit, {limit:1000});
    }
    if (kind === 'links' || kind === 'image' || kind === 'gallery') field(fields, 'url', kind === 'links' ? 'Dirección del enlace' : 'Enlace al pulsar la imagen (opcional)', item.url, {url:true, required:kind === 'links', limit:2000});
    if (kind === 'video') {
      field(fields, 'url', 'Enlace de YouTube, clip o video grabado de Twitch', item.url, {url:true, required:true, limit:2000});
      field(fields, 'credit', 'Créditos, canal y derechos', item.credit, {multiline:true, limit:1000});
    }
    if (kind === 'links') field(fields, 'text', 'Descripción (opcional)', item.text, {multiline:true, limit:2000});
    controls(node, parent, 'elemento'); parent.append(node); update();
  }
  function addSection(section = {}) {
    if (sections.children.length >= 30) {alert('Puedes agregar hasta 30 secciones.'); return;}
    const kind = types[section.type] ? section.type : 'text';
    const node = document.createElement('fieldset'); node.className = 'news-editor-block'; node.dataset.type = kind;
    const legend = document.createElement('legend'); legend.textContent = types[kind]; node.append(legend);
    const fields = document.createElement('div'); fields.className = 'news-section-fields'; node.append(fields);
    field(fields, 'title', 'Título de la sección', section.title);
    field(fields, 'text', 'Texto de la sección', section.text, {multiline:true, limit:20000});
    const entries = document.createElement('div'); entries.className = 'news-block-entries'; node.append(entries);
    if (kind !== 'text') {
      const add = button(kind === 'links' ? 'Agregar enlace' : kind === 'highlights' ? 'Agregar tarjeta' : kind === 'list' ? 'Agregar elemento' : kind === 'video' ? 'Agregar video' : 'Agregar imagen', () => {
        if (kind === 'image' && entries.children.length) {alert('Para agregar varias imágenes, usa una galería.'); return;}
        addItem(entries, kind);
      });
      node.append(add);
      if (Array.isArray(section.items)) for (const item of section.items) {if (item && typeof item === 'object') addItem(entries, kind, item);}
    }
    controls(node, sections, 'sección'); sections.append(node); update();
  }
  function addSummary(item = {}) {
    if (summaries.children.length >= 12) {alert('Puedes agregar hasta 12 datos esenciales.'); return;}
    const node = document.createElement('div'); node.className = 'news-editor-entry';
    const fields = document.createElement('div'); fields.className = 'news-editor-grid'; node.append(fields);
    field(fields, 'label', 'Etiqueta (por ejemplo, Precio oficial)', item.label, {limit:120, required:true});
    field(fields, 'value', 'Valor (por ejemplo, US$249,99)', item.value, {limit:400, required:true});
    controls(node, summaries, 'dato'); summaries.append(node); update();
  }
  function update() {
    document.getElementById('news-sections-data').value = JSON.stringify(Array.from(sections.children, node => ({
      type:node.dataset.type, ...values(node.querySelector('.news-section-fields')),
      items:Array.from(node.querySelector('.news-block-entries').children, entry => values(entry.querySelector('.news-entry-fields')))
    })));
    document.getElementById('news-summary-data').value = JSON.stringify(Array.from(summaries.children, node => values(node)));
  }
  if (Array.isArray(initial.sections)) for (const section of initial.sections) {if (section && typeof section === 'object') addSection(section);}
  if (Array.isArray(initial.summary_items)) for (const item of initial.summary_items) {if (item && typeof item === 'object') addSummary(item);}
  document.getElementById('news-add-section').addEventListener('click', () => addSection({type:document.getElementById('news-block-type').value}));
  document.getElementById('news-add-summary').addEventListener('click', () => addSummary());
  form.addEventListener('input', update); form.addEventListener('submit', update); update();
  for (const submit of form.querySelectorAll('[data-news-submit]')) submit.disabled = false;
})();
