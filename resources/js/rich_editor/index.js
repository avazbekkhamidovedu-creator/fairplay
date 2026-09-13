// Browser entry. `npm run build:editor` writes the gitignored IIFE at resources/rich_editor.js.
import { createTiptapEditor, createHeadlessEditor, parseMarkdownToHtml, serializeHtmlToMarkdown } from './createEditor.js';
import { renumberBlankChips } from './extensions/blank.js';

const config = {
  imageUploadUrl: '',
  fileUploadUrl: '',
  searchUsersUrl: '',
  csrfToken: '',
  onChange: null,
  messages: {
    uploadFailed: 'Image upload failed.',
    uploadFailedAuth: 'Image upload failed. Make sure you are signed in with upload permission.',
    fileUploadFailed: 'File upload failed.',
    fileUploadFailedAuth: 'File upload failed. Make sure you are signed in with upload permission.',
  },
  imageInputId: 'img-file',
  fileInputId: 'rte-file',
};

const instances = new Map();

function t(msgid) {
  return (typeof gettext === 'function') ? gettext(msgid) : msgid;
}

function notifyChange() {
  if (typeof config.onChange === 'function') config.onChange();
}

function getRteButtons() {
  return {
    heading: '<select class="rte-heading" title="' + t('Text level') + '"><option value="p">' + t('Body') + '</option><option value="h3">' + t('Heading 1') + '</option><option value="h4">' + t('Heading 2') + '</option><option value="h5">' + t('Heading 3') + '</option></select>',
    sep: '<span class="sep"></span>',
    bold: '<button type="button" data-cmd="bold" title="' + t('Bold (Ctrl+B)') + '"><i class="fa fa-bold"></i></button>',
    italic: '<button type="button" data-cmd="italic" title="' + t('Italic (Ctrl+I)') + '"><i class="fa fa-italic"></i></button>',
    highlight: '<button type="button" data-cmd="highlight" class="hl-btn" title="' + t('Highlight') + '"><i class="fa fa-paint-brush"></i></button>',
    code: '<button type="button" data-cmd="code" title="' + t('Inline code') + '"><i class="fa fa-code"></i></button>',
    codeblock: '<button type="button" data-cmd="codeblock" title="' + t('Code block (Ctrl+Enter to leave)') + '"><i class="fa fa-file-code-o"></i></button>',
    math: '<button type="button" data-cmd="math" title="' + t('Math (Ctrl+Alt+D)') + '"><i class="fa fa-superscript"></i></button>',
    quote: '<button type="button" data-cmd="quote" title="' + t('Quote (Ctrl+Q)') + '"><i class="fa fa-quote-left"></i></button>',
    ul: '<button type="button" data-cmd="ul" title="' + t('Unordered list (Ctrl+U)') + '"><i class="fa fa-list-ul"></i></button>',
    ol: '<button type="button" data-cmd="ol" title="' + t('Ordered list (Ctrl+Shift+O)') + '"><i class="fa fa-list-ol"></i></button>',
    table: '<button type="button" data-cmd="table" title="' + t('Table') + '"><i class="fa fa-table"></i></button>',
    link: '<button type="button" data-cmd="link" title="' + t('Link (Ctrl+L)') + '"><i class="fa fa-link"></i></button>',
    image: '<button type="button" data-cmd="image" title="' + t('Image') + '"' +
      (config.fileUploadUrl ? ' style="display:none;"' : '') + '><i class="fa fa-image"></i></button>',
    file: '<button type="button" data-cmd="file" title="' + t('Attach file') + '"' +
      (config.fileUploadUrl ? '' : ' style="display:none;"') + '><i class="fa fa-paperclip"></i></button>',
    mention: '<button type="button" data-cmd="mention" title="' + t('Mention user (Ctrl+M)') + '"><i class="fa fa-at"></i></button>',
    help: '<button type="button" data-cmd="help" title="' + t('Markdown help') + '"><i class="fa fa-question-circle"></i></button>',
    alignLeft: '<button type="button" data-cmd="alignLeft" title="' + t('Align left') + '"><i class="fa fa-align-left"></i></button>',
    alignCenter: '<button type="button" data-cmd="alignCenter" title="' + t('Align center') + '"><i class="fa fa-align-center"></i></button>',
    alignRight: '<button type="button" data-cmd="alignRight" title="' + t('Align right') + '"><i class="fa fa-align-right"></i></button>',
    blank: '<button type="button" data-cmd="blank" class="blank-insert" title="' + t('Insert blank') + '" style="display:none;"><i class="fa fa-square-o"></i></button>',
  };
}

const RTE_TOOLBAR = [
  'heading', 'sep',
  'bold', 'highlight', 'italic', 'sep',
  'code', 'codeblock', 'math', 'quote', 'sep',
  'ul', 'ol', 'table', 'sep',
  'alignLeft', 'alignCenter', 'alignRight', 'sep',
  'link', 'image', 'file', 'mention', 'help', 'blank',
];

function richEditorTpl(name, opts) {
  opts = opts || {};
  const buttons = getRteButtons();
  const bar = RTE_TOOLBAR.map((k) => buttons[k] || '').join('');
  const placeholder = opts.placeholder || t('Enter content; $$math$$ supported…');
  const mdPlaceholder = opts.mdPlaceholder || t('Markdown and $$math$$ supported…');
  const compact = opts.compact ? ' compact' : '';
  return `<div class="rich-editor mode-rich${compact}" data-editor="${name}">
      <div class="rich-editor-toolbar-row">
        <div class="editor-mode-tabs">
          <button type="button" class="mode-btn active" data-mode="rich">${t('Rich text')}</button>
          <button type="button" class="mode-btn" data-mode="markdown">Markdown</button>
        </div>
        <div class="rich-toolbar">${bar}</div>
      </div>
      <div class="rich-editor-panes">
        <div class="rich-editor-rich"><div class="rte-tiptap-mount"></div></div>
        <div class="rich-editor-markdown"><textarea placeholder="${mdPlaceholder}"></textarea></div>
      </div>
    </div>`;
}

function findRoot(name) {
  return document.querySelector('.rich-editor[data-editor="' + name + '"]');
}

function getInstance(name) {
  return instances.get(name) || null;
}

function getMarkdown(name) {
  const inst = getInstance(name);
  const root = findRoot(name);
  if (!inst || !root) return '';
  if (root.classList.contains('mode-markdown')) {
    return (root.querySelector('textarea') || {}).value || '';
  }
  return inst.getMarkdown() || '';
}

function applyMarkdownContent(inst, md) {
  if (!inst || inst.isDestroyed) return;
  try {
    inst.commands.setContent(md || '', { contentType: 'markdown' });
  } catch (err) {
    if (!inst.isDestroyed) {
      try { inst.commands.setContent(md || ''); } catch (e) { /* ignore */ }
    }
  }
}

function setMarkdown(name, md) {
  const inst = getInstance(name);
  const root = findRoot(name);
  if (!root) return;
  const ta = root.querySelector('textarea');
  if (ta) ta.value = md || '';
  // Hidden ProseMirror views break if we setContent while display/layout is off.
  if (inst && !root.classList.contains('mode-markdown')) {
    applyMarkdownContent(inst, md);
    renumberBlankChips(root);
  }
}

function editorHtml(name) {
  const inst = getInstance(name);
  if (inst) return inst.getHTML();
  return parseMarkdownToHtml(getMarkdown(name));
}

function flushMarkdown(root) {
  const name = root.getAttribute('data-editor');
  const inst = getInstance(name);
  const ta = root.querySelector('textarea');
  if (inst && ta && !root.classList.contains('mode-markdown')) {
    ta.value = inst.getMarkdown() || '';
  }
}

function applyAlign(inst, align) {
  inst.chain().focus().setTextAlign(align).run();
}

function applyCmd(root, cmd) {
  const name = root.getAttribute('data-editor');
  const inst = getInstance(name);
  const isMd = root.classList.contains('mode-markdown');
  const ta = root.querySelector('textarea');
  if (cmd === 'table') {
    openTablePicker(root);
    return;
  }
  if (isMd && ta) {
    applyMarkdownShortcut(ta, cmd);
    ta.dispatchEvent(new Event('input'));
    notifyChange();
    return;
  }
  if (!inst) return;
  switch (cmd) {
    case 'bold': inst.chain().focus().toggleBold().run(); break;
    case 'italic': inst.chain().focus().toggleItalic().run(); break;
    case 'highlight': inst.chain().focus().toggleHighlight().run(); break;
    case 'code': inst.chain().focus().toggleCode().run(); break;
    case 'codeblock': inst.chain().focus().toggleCodeBlock().run(); break;
    case 'quote': inst.chain().focus().toggleBlockquote().run(); break;
    case 'ul': inst.chain().focus().toggleBulletList().run(); break;
    case 'ol': inst.chain().focus().toggleOrderedList().run(); break;
    case 'p': inst.chain().focus().setParagraph().run(); break;
    case 'h3': inst.chain().focus().toggleHeading({ level: 1 }).run(); break;
    case 'h4': inst.chain().focus().toggleHeading({ level: 2 }).run(); break;
    case 'h5': inst.chain().focus().toggleHeading({ level: 3 }).run(); break;
    case 'alignLeft': applyAlign(inst, 'left'); break;
    case 'alignCenter': applyAlign(inst, 'center'); break;
    case 'alignRight': applyAlign(inst, 'right'); break;
    case 'blank': inst.chain().focus().insertBlank().run(); break;
    case 'math': openMathPopover(root); return;
    case 'link': openLinkPopover(root); return;
    case 'mention': openMentionPopover(root); return;
    case 'image': pickImage(root); return;
    case 'file': pickFile(root); return;
    case 'help': openHelpModal(); return;
    default: break;
  }
  flushMarkdown(root);
  renumberBlankChips(root);
  notifyChange();
  updateToolbarState(root);
}

function applyMarkdownShortcut(ta, cmd) {
  const wrap = (left, right, fallback) => {
    const s = ta.selectionStart;
    const e = ta.selectionEnd;
    const sel = ta.value.slice(s, e) || fallback;
    const ins = left + sel + right;
    ta.value = ta.value.slice(0, s) + ins + ta.value.slice(e);
    ta.selectionStart = ta.selectionEnd = s + ins.length;
  };
  switch (cmd) {
    case 'bold': wrap('**', '**', 'bold'); break;
    case 'italic': wrap('*', '*', 'italic'); break;
    case 'highlight': wrap('<mark>', '</mark>', 'text'); break;
    case 'code': wrap('`', '`', 'code'); break;
    case 'codeblock': wrap('```\n', '\n```', 'code'); break;
    case 'quote': wrap('> ', '', 'quote'); break;
    case 'math': wrap('$$', '$$', 'a^2 + b^2 = c^2'); break;
    case 'blank': wrap('{{blank}}', '', ''); break;
    case 'ul': wrap('- ', '', 'item'); break;
    case 'ol': wrap('1. ', '', 'item'); break;
    default: break;
  }
}

function updateToolbarState(root) {
  if (!root || root.classList.contains('mode-markdown')) return;
  const inst = getInstance(root.getAttribute('data-editor'));
  if (!inst) return;
  const map = {
    bold: () => inst.isActive('bold'),
    italic: () => inst.isActive('italic'),
    highlight: () => inst.isActive('highlight'),
    code: () => inst.isActive('code'),
    codeblock: () => inst.isActive('codeBlock'),
    quote: () => inst.isActive('blockquote'),
    ul: () => inst.isActive('bulletList'),
    ol: () => inst.isActive('orderedList'),
    link: () => inst.isActive('link'),
  };
  root.querySelectorAll('.rich-toolbar button[data-cmd]').forEach((btn) => {
    const fn = map[btn.dataset.cmd];
    btn.classList.toggle('active', !!(fn && fn()));
  });
  const headSel = root.querySelector('.rte-heading');
  if (headSel) {
    if (inst.isActive('heading', { level: 1 })) headSel.value = 'h3';
    else if (inst.isActive('heading', { level: 2 })) headSel.value = 'h4';
    else if (inst.isActive('heading', { level: 3 })) headSel.value = 'h5';
    else headSel.value = 'p';
  }
}

function bindRoot(root) {
  if (root.dataset.bound) return;
  root.dataset.bound = '1';
  const name = root.getAttribute('data-editor');
  if (!root.querySelector('.rich-editor-panes')) {
    const panes = document.createElement('div');
    panes.className = 'rich-editor-panes';
    const rich = root.querySelector('.rich-editor-rich');
    const md = root.querySelector('.rich-editor-markdown');
    if (rich && md) {
      rich.parentNode.insertBefore(panes, rich);
      panes.appendChild(rich);
      panes.appendChild(md);
    }
  }
  let mount = root.querySelector('.rte-tiptap-mount');
  if (!mount) {
    const rich = root.querySelector('.rich-editor-rich') || root;
    const legacy = rich.querySelector('.rich-editor-body');
    if (legacy) legacy.remove();
    mount = document.createElement('div');
    mount.className = 'rte-tiptap-mount';
    rich.appendChild(mount);
  }
  const ta = root.querySelector('textarea');
  const placeholder = (mount && mount.getAttribute('data-placeholder'))
    || (root.querySelector('.rich-editor-body') && root.querySelector('.rich-editor-body').getAttribute('data-placeholder'))
    || t('Enter content; $$math$$ supported…');

  const inst = createTiptapEditor({
    element: mount,
    placeholder,
    onMathClick: () => openMathPopover(root, true),
    onMentionTrigger: () => openMentionPopover(root),
    onUpdate: () => {
      if (!root.classList.contains('mode-markdown')) {
        flushMarkdown(root);
        renumberBlankChips(root);
        notifyChange();
        updateToolbarState(root);
        syncTableCellChrome(root);
      }
    },
    onSelectionUpdate: () => syncTableCellChrome(root),
  });
  instances.set(name, inst);
  if (ta && ta.value) applyMarkdownContent(inst, ta.value);
  renumberBlankChips(root);

  root.querySelectorAll('.mode-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const toMd = btn.dataset.mode === 'markdown';
      const fromMd = root.classList.contains('mode-markdown');
      if (toMd === fromMd) return;
      if (root._rteModeRaf) {
        cancelAnimationFrame(root._rteModeRaf);
        root._rteModeRaf = 0;
      }
      if (toMd) {
        flushMarkdown(root);
        root.classList.add('mode-markdown');
        root.classList.remove('mode-rich');
        hideTableChrome();
        if (ta) ta.focus();
      } else {
        root.classList.remove('mode-markdown');
        root.classList.add('mode-rich');
        root._rteModeRaf = requestAnimationFrame(() => {
          root._rteModeRaf = 0;
          if (inst.isDestroyed || root.classList.contains('mode-markdown')) return;
          applyMarkdownContent(inst, (ta && ta.value) || '');
          try { inst.chain().focus('start', { scrollIntoView: false }).run(); } catch (e) { /* jsdom */ }
          renumberBlankChips(root);
          notifyChange();
        });
      }
      root.querySelectorAll('.mode-btn').forEach((b) => b.classList.toggle('active', b === btn));
      notifyChange();
    });
  });

  if (ta) {
    ta.addEventListener('input', () => notifyChange());
  }

  root.querySelectorAll('.rich-toolbar button[data-cmd]').forEach((btn) => {
    btn.addEventListener('mousedown', (e) => {
      if (['link', 'mention', 'image', 'file', 'math', 'table'].indexOf(btn.dataset.cmd) !== -1) {
        e.preventDefault();
      }
    });
    btn.addEventListener('click', () => applyCmd(root, btn.dataset.cmd));
  });
  const headSel = root.querySelector('.rte-heading');
  if (headSel) headSel.addEventListener('change', () => applyCmd(root, headSel.value));

  root.addEventListener('keydown', (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (!mod) return;
    const key = (e.key || '').toLowerCase();
    let cmd = null;
    if (e.altKey && key === 'd') cmd = 'math';
    else if (e.altKey && key === '1') cmd = 'h3';
    else if (e.altKey && key === '2') cmd = 'h4';
    else if (e.altKey && key === '3') cmd = 'h5';
    else if (e.shiftKey && key === 'o') cmd = 'ol';
    else if (!e.altKey && !e.shiftKey) {
      if (key === 'b') cmd = 'bold';
      else if (key === 'i') cmd = 'italic';
      else if (key === 'l') cmd = 'link';
      else if (key === 'q') cmd = 'quote';
      else if (key === 'u') cmd = 'ul';
      else if (key === 'm') cmd = 'mention';
    }
    if (!cmd) return;
    e.preventDefault();
    applyCmd(root, cmd);
  });

  syncUploadButtons(root);
}

function syncUploadButtons(root) {
  const scope = root || document;
  (scope.matches && scope.matches('.rich-editor') ? [scope] : scope.querySelectorAll('.rich-editor')).forEach((el) => {
    const fileBtn = el.querySelector('[data-cmd="file"]');
    const imgBtn = el.querySelector('[data-cmd="image"]');
    if (fileBtn) fileBtn.style.display = config.fileUploadUrl ? '' : 'none';
    if (imgBtn) imgBtn.style.display = config.fileUploadUrl ? 'none' : '';
  });
}

function mountRichEditors(scope) {
  (scope || document).querySelectorAll('[data-rich-mount]').forEach((m) => {
    const name = m.getAttribute('data-rich-mount');
    m.outerHTML = richEditorTpl(name, {
      compact: m.hasAttribute('data-compact'),
      placeholder: m.getAttribute('data-placeholder') || undefined,
    });
  });
}

function initEditors(scope) {
  (scope || document).querySelectorAll('.rich-editor').forEach(bindRoot);
}

/* ---- overlays ---- */
let overlays = null;
function ensureOverlays() {
  if (overlays && overlays.root && overlays.root.isConnected) return overlays;
  overlays = null;
  if (typeof document === 'undefined') return null;
  const root = document.createElement('div');
  root.id = 'rte-overlays';
  root.innerHTML = `
    <div id="rte-link-popover" class="rte-popover" hidden>
      <label class="rte-field"><span>${t('Text')}</span><input type="text" class="rte-link-text" placeholder="${t('Link text')}"></label>
      <label class="rte-field"><span>${t('URL')}</span><input type="url" class="rte-link-url" placeholder="https://"></label>
      <div class="rte-popover-actions"><button type="button" class="rte-btn-cancel">${t('Cancel')}</button><button type="button" class="rte-btn-ok">${t('Insert')}</button></div>
    </div>
    <div id="rte-mention-popover" class="rte-popover" hidden>
      <label class="rte-field"><span>${t('Name or username')}</span><input type="text" class="rte-mention-input" placeholder="${t('Search users…')}" autocomplete="off"></label>
      <ul class="rte-mention-results"></ul>
      <div class="rte-popover-actions"><button type="button" class="rte-btn-cancel">${t('Cancel')}</button><button type="button" class="rte-btn-ok">${t('Insert')}</button></div>
    </div>
    <div id="rte-math-popover" class="rte-popover" hidden>
      <label class="rte-field"><span>${t('LaTeX')}</span><input type="text" class="rte-math-input" placeholder="a^2 + b^2 = c^2"></label>
      <div class="rte-popover-actions"><button type="button" class="rte-btn-cancel">${t('Cancel')}</button><button type="button" class="rte-btn-ok">${t('Insert')}</button></div>
    </div>
    <div id="rte-help-popover" class="rte-popover rte-help" hidden>
      <p>${t('Problem statements use Markdown. Full syntax:')} <a href="https://commonmark.org/help/" target="_blank" rel="noopener">CommonMark</a>.</p>
      <button type="button" class="rte-btn-ok">${t('Close')}</button>
    </div>
    <div id="rte-table-picker" class="rte-table-picker" hidden>
      <div class="rte-table-picker-grid"></div>
      <div class="rte-table-picker-label">3 × 3</div>
    </div>
    <div id="rte-table-cell-chrome" class="rte-table-cell-chrome" hidden>
      <button type="button" class="rte-table-cell-handle" title="${t('Table row and column')}"><i class="fa fa-ellipsis-v"></i></button>
    </div>
    <div id="rte-table-cell-menu" class="rte-table-cell-menu" hidden>
      <button type="button" data-table-act="row-above">${t('Insert row above')}</button>
      <button type="button" data-table-act="row-below">${t('Insert row below')}</button>
      <button type="button" data-table-act="col-left">${t('Insert column left')}</button>
      <button type="button" data-table-act="col-right">${t('Insert column right')}</button>
      <div class="rte-table-cell-menu-sep"></div>
      <button type="button" data-table-act="del-row">${t('Delete row')}</button>
      <button type="button" data-table-act="del-col">${t('Delete column')}</button>
      <button type="button" data-table-act="del-table">${t('Delete table')}</button>
    </div>`;
  document.body.appendChild(root);
  overlays = {
    root,
    link: root.querySelector('#rte-link-popover'),
    mention: root.querySelector('#rte-mention-popover'),
    math: root.querySelector('#rte-math-popover'),
    help: root.querySelector('#rte-help-popover'),
    tablePicker: root.querySelector('#rte-table-picker'),
    tableChrome: root.querySelector('#rte-table-cell-chrome'),
    tableMenu: root.querySelector('#rte-table-cell-menu'),
  };
  bindTableOverlays(overlays);
  overlays.link.querySelector('.rte-btn-cancel').addEventListener('click', closeOverlays);
  overlays.link.querySelector('.rte-btn-ok').addEventListener('click', confirmLink);
  overlays.mention.querySelector('.rte-btn-cancel').addEventListener('click', closeOverlays);
  overlays.mention.querySelector('.rte-btn-ok').addEventListener('click', () => confirmMention());
  overlays.math.querySelector('.rte-btn-cancel').addEventListener('click', closeOverlays);
  overlays.math.querySelector('.rte-btn-ok').addEventListener('click', confirmMath);
  overlays.math.querySelector('.rte-math-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      confirmMath();
    }
  });
  overlays.help.querySelector('.rte-btn-ok').addEventListener('click', closeOverlays);
  const mi = overlays.mention.querySelector('.rte-mention-input');
  let mentionTimer = null;
  mi.addEventListener('input', () => {
    clearTimeout(mentionTimer);
    mentionTimer = setTimeout(() => searchUsers(mi.value), 200);
  });
  overlays.mention.querySelector('.rte-mention-results').addEventListener('click', (e) => {
    const li = e.target.closest('[data-username]');
    if (li) confirmMention(li.getAttribute('data-username'), li.getAttribute('data-label'));
  });
  return overlays;
}

let overlayRoot = null;
let savedInsertPos = null;
function hidePopovers() {
  const o = ensureOverlays();
  if (!o) return;
  o.link.hidden = true;
  o.mention.hidden = true;
  o.math.hidden = true;
  o.help.hidden = true;
  if (o.tablePicker) o.tablePicker.hidden = true;
  hideTableCellMenu();
}

function closeOverlays() {
  hidePopovers();
  overlayRoot = null;
  savedInsertPos = null;
}

function positionPopover(pop, root, cmd) {
  const btn = root.querySelector('.rich-toolbar button[data-cmd="' + cmd + '"]');
  const rect = (btn || root).getBoundingClientRect();
  pop.hidden = false;
  // position:fixed is viewport-relative. Adding scrollY/scrollX drops the
  // popover to the bottom of a scrolled create/edit page.
  pop.style.position = 'fixed';
  const gap = 6;
  const width = pop.offsetWidth || 280;
  const height = pop.offsetHeight || 0;
  let top = rect.bottom + gap;
  if (height && top + height > window.innerHeight - 8) {
    top = Math.max(8, rect.top - height - gap);
  }
  const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
  pop.style.top = top + 'px';
  pop.style.left = left + 'px';
}

const TABLE_PICKER_MAX = 10;
let tableChromeRoot = null;
let tableOverlayBound = false;

function buildPipeTableMd(rows, cols) {
  rows = Math.max(1, Math.min(TABLE_PICKER_MAX, rows | 0));
  cols = Math.max(1, Math.min(TABLE_PICKER_MAX, cols | 0));
  const header = [];
  const sep = [];
  for (let c = 0; c < cols; c++) {
    header.push(t('Col') + ' ' + (c + 1));
    sep.push('---');
  }
  const lines = ['| ' + header.join(' | ') + ' |', '| ' + sep.join(' | ') + ' |'];
  for (let r = 1; r < rows; r++) {
    lines.push('| ' + Array(cols).fill('').join(' | ') + ' |');
  }
  return lines.join('\n');
}

function ensureTablePickerGrid(grid) {
  if (!grid || grid.childElementCount) return;
  for (let r = 1; r <= TABLE_PICKER_MAX; r++) {
    for (let c = 1; c <= TABLE_PICKER_MAX; c++) {
      const cell = document.createElement('button');
      cell.type = 'button';
      cell.className = 'rte-table-picker-cell';
      cell.dataset.rows = String(r);
      cell.dataset.cols = String(c);
      grid.appendChild(cell);
    }
  }
}

function highlightTablePicker(rows, cols) {
  const o = ensureOverlays();
  if (!o || !o.tablePicker) return;
  o.tablePicker.querySelectorAll('.rte-table-picker-cell').forEach((cell) => {
    const r = Number(cell.dataset.rows);
    const c = Number(cell.dataset.cols);
    cell.classList.toggle('is-active', r <= rows && c <= cols);
  });
  const label = o.tablePicker.querySelector('.rte-table-picker-label');
  if (label) label.textContent = rows + ' × ' + cols;
}

function insertTableAt(root, rows, cols) {
  const inst = getInstance(root.getAttribute('data-editor'));
  if (root.classList.contains('mode-markdown')) {
    const ta = root.querySelector('textarea');
    if (!ta) return;
    const md = buildPipeTableMd(rows, cols);
    const s = ta.selectionStart;
    const pad = (s > 0 && ta.value[s - 1] !== '\n') ? '\n\n' : '';
    const text = pad + md + '\n\n';
    ta.value = ta.value.slice(0, s) + text + ta.value.slice(ta.selectionEnd);
    ta.selectionStart = ta.selectionEnd = s + text.length;
    ta.dispatchEvent(new Event('input'));
  } else if (inst) {
    inst.chain().focus().insertTable({ rows, cols, withHeaderRow: true }).run();
    flushMarkdown(root);
    syncTableCellChrome(root);
  }
  notifyChange();
}

function openTablePicker(root) {
  const o = ensureOverlays();
  if (!o || !o.tablePicker) return;
  if (!o.tablePicker.hidden && overlayRoot === root) {
    closeOverlays();
    return;
  }
  hidePopovers();
  overlayRoot = root;
  ensureTablePickerGrid(o.tablePicker.querySelector('.rte-table-picker-grid'));
  highlightTablePicker(3, 3);
  positionPopover(o.tablePicker, root, 'table');
}

function hideTableCellMenu() {
  const o = overlays;
  if (!o || !o.tableMenu) return;
  o.tableMenu.hidden = true;
  if (o.tableChrome) o.tableChrome.classList.remove('menu-open');
}

function hideTableChrome() {
  const o = overlays;
  if (!o) return;
  hideTableCellMenu();
  if (o.tableChrome) o.tableChrome.hidden = true;
  document.querySelectorAll('.rte-table-cell-active').forEach((el) => {
    el.classList.remove('rte-table-cell-active');
  });
  tableChromeRoot = null;
}

function currentTableCellEl(inst) {
  if (!inst || inst.isDestroyed || !inst.isActive('table')) return null;
  const { view, state } = inst;
  const $from = state.selection.$from;
  for (let d = $from.depth; d > 0; d--) {
    const name = $from.node(d).type.name;
    if (name === 'tableCell' || name === 'tableHeader') {
      const node = view.nodeDOM($from.before(d));
      if (node && node.nodeType === 1) return node.closest('td, th') || node;
    }
  }
  const at = view.domAtPos(state.selection.from);
  const el = at.node && (at.node.nodeType === 1 ? at.node : at.node.parentElement);
  return el && el.closest('td, th');
}

function syncTableCellChrome(root) {
  const o = ensureOverlays();
  if (!o || !o.tableChrome) return;
  if (!root || root.classList.contains('mode-markdown')) {
    hideTableChrome();
    return;
  }
  const inst = getInstance(root.getAttribute('data-editor'));
  const cell = inst && currentTableCellEl(inst);
  if (!cell) {
    const active = document.activeElement;
    if (active && active.closest && active.closest('.rte-table-cell-chrome, .rte-table-cell-menu')) {
      return;
    }
    hideTableChrome();
    return;
  }
  const prev = document.querySelector('.rte-table-cell-active');
  if (prev && prev !== cell) hideTableCellMenu();
  document.querySelectorAll('.rte-table-cell-active').forEach((el) => {
    if (el !== cell) el.classList.remove('rte-table-cell-active');
  });
  cell.classList.add('rte-table-cell-active');
  tableChromeRoot = root;
  const rect = cell.getBoundingClientRect();
  o.tableChrome.hidden = false;
  o.tableChrome.style.top = Math.round(rect.top + 3) + 'px';
  o.tableChrome.style.left = Math.round(rect.right - 20) + 'px';
}

function runTableAction(act) {
  const root = tableChromeRoot || overlayRoot;
  if (!root) return;
  const inst = getInstance(root.getAttribute('data-editor'));
  if (!inst) return;
  const chain = inst.chain().focus();
  if (act === 'row-above') chain.addRowBefore().run();
  else if (act === 'row-below') chain.addRowAfter().run();
  else if (act === 'col-left') chain.addColumnBefore().run();
  else if (act === 'col-right') chain.addColumnAfter().run();
  else if (act === 'del-row') chain.deleteRow().run();
  else if (act === 'del-col') chain.deleteColumn().run();
  else if (act === 'del-table') chain.deleteTable().run();
  hideTableCellMenu();
  flushMarkdown(root);
  notifyChange();
  if (act === 'del-table' || !inst.isActive('table')) hideTableChrome();
  else syncTableCellChrome(root);
}

function bindTableOverlays(o) {
  ensureTablePickerGrid(o.tablePicker.querySelector('.rte-table-picker-grid'));
  o.tablePicker.addEventListener('mouseover', (e) => {
    const cell = e.target.closest('.rte-table-picker-cell');
    if (!cell) return;
    highlightTablePicker(Number(cell.dataset.rows), Number(cell.dataset.cols));
  });
  o.tablePicker.addEventListener('click', (e) => {
    const cell = e.target.closest('.rte-table-picker-cell');
    if (!cell || !overlayRoot) return;
    const root = overlayRoot;
    const rows = Number(cell.dataset.rows);
    const cols = Number(cell.dataset.cols);
    closeOverlays();
    insertTableAt(root, rows, cols);
  });
  o.tableChrome.querySelector('.rte-table-cell-handle').addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!o.tableMenu.hidden) {
      hideTableCellMenu();
      return;
    }
    const inst = tableChromeRoot && getInstance(tableChromeRoot.getAttribute('data-editor'));
    o.tableMenu.querySelectorAll('[data-table-act]').forEach((btn) => {
      const act = btn.getAttribute('data-table-act');
      let ok = true;
      if (inst) {
        if (act === 'del-row') ok = inst.can().deleteRow();
        else if (act === 'del-col') ok = inst.can().deleteColumn();
        else if (act === 'del-table') ok = inst.can().deleteTable();
      }
      btn.disabled = !ok;
    });
    const handle = e.currentTarget.getBoundingClientRect();
    o.tableMenu.hidden = false;
    o.tableChrome.classList.add('menu-open');
    const width = o.tableMenu.offsetWidth || 200;
    o.tableMenu.style.top = Math.round(handle.bottom + 4) + 'px';
    o.tableMenu.style.left = Math.max(8, Math.round(handle.right - width)) + 'px';
  });
  o.tableMenu.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-table-act]');
    if (btn && !btn.disabled) runTableAction(btn.getAttribute('data-table-act'));
  });
  if (!tableOverlayBound) {
    tableOverlayBound = true;
    document.addEventListener('mousedown', (e) => {
      const target = e.target;
      if (!target.closest || target.closest('.rte-table-picker, [data-cmd="table"]')) return;
      if (overlays && overlays.tablePicker) overlays.tablePicker.hidden = true;
      if (target.closest('.rte-table-cell-chrome, .rte-table-cell-menu')) return;
      hideTableCellMenu();
    });
    window.addEventListener('scroll', () => {
      if (tableChromeRoot) syncTableCellChrome(tableChromeRoot);
    }, true);
    window.addEventListener('resize', () => {
      if (tableChromeRoot) syncTableCellChrome(tableChromeRoot);
    });
  }
}

function openLinkPopover(root) {
  const o = ensureOverlays();
  overlayRoot = root;
  o.mention.hidden = true;
  o.math.hidden = true;
  if (o.tablePicker) o.tablePicker.hidden = true;
  hideTableCellMenu();
  o.link.querySelector('.rte-link-text').value = '';
  o.link.querySelector('.rte-link-url').value = 'https://';
  positionPopover(o.link, root, 'link');
}

function confirmLink() {
  const o = ensureOverlays();
  if (!o || !overlayRoot) return;
  const root = overlayRoot;
  const text = (o.link.querySelector('.rte-link-text').value || '').trim() || t('Link text');
  const href = (o.link.querySelector('.rte-link-url').value || '').trim() || 'https://';
  const inst = getInstance(root.getAttribute('data-editor'));
  closeOverlays();
  if (root.classList.contains('mode-markdown')) {
    const ta = root.querySelector('textarea');
    const md = '[' + text + '](' + href + ')';
    const s = ta.selectionStart;
    ta.value = ta.value.slice(0, s) + md + ta.value.slice(ta.selectionEnd);
    ta.dispatchEvent(new Event('input'));
  } else if (inst) {
    inst.chain().focus().setLink({ href }).insertContent(text).run();
    flushMarkdown(root);
  }
  notifyChange();
}

function openMentionPopover(root) {
  const o = ensureOverlays();
  overlayRoot = root;
  o.link.hidden = true;
  o.math.hidden = true;
  if (o.tablePicker) o.tablePicker.hidden = true;
  hideTableCellMenu();
  o.mention.querySelector('.rte-mention-input').value = '';
  o.mention.querySelector('.rte-mention-results').innerHTML = '<li class="muted">' + t('Type a name or username') + '</li>';
  positionPopover(o.mention, root, 'mention');
  o.mention.querySelector('.rte-mention-input').focus();
}

function mentionSearchUrl(q) {
  const base = config.searchUsersUrl;
  const sep = base.indexOf('?') >= 0 ? '&' : '?';
  return base + sep + 'username=' + encodeURIComponent(q || '');
}

function searchUsers(q) {
  const o = ensureOverlays();
  if (!o || !config.searchUsersUrl) return;
  const list = o.mention.querySelector('.rte-mention-results');
  const query = String(q || '').trim();
  if (!query) {
    list.innerHTML = '<li class="muted">' + t('Type a name or username') + '</li>';
    return;
  }
  const url = mentionSearchUrl(query);
  fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
    .then((r) => r.json())
    .then((body) => {
      const rows = (body && body.data) || [];
      list.innerHTML = rows.length
        ? rows.map((u) => '<li data-username="' + escapeAttr(u.username) + '" data-label="' + escapeAttr(u.text || u.username) + '">' + escapeHtml(u.text || u.username) + '</li>').join('')
        : '<li class="muted">' + t('No match; you can still insert') + '</li>';
    })
    .catch(() => { list.innerHTML = '<li class="muted">' + t('Search failed') + '</li>'; });
}

function confirmMention(username, label) {
  const o = ensureOverlays();
  if (!o || !overlayRoot) return;
  const root = overlayRoot;
  const name = (username || o.mention.querySelector('.rte-mention-input').value || '').trim();
  if (!name) return;
  const inst = getInstance(root.getAttribute('data-editor'));
  closeOverlays();
  if (root.classList.contains('mode-markdown')) {
    const ta = root.querySelector('textarea');
    const token = '[user:' + name + ']';
    const s = ta.selectionStart;
    ta.value = ta.value.slice(0, s) + token + ta.value.slice(ta.selectionEnd);
    ta.dispatchEvent(new Event('input'));
  } else if (inst) {
    inst.chain().focus().insertMention({
      username: name,
      kind: 'user',
      label: label || name,
    }).run();
    flushMarkdown(root);
  }
  notifyChange();
}

function openMathPopover(root) {
  const o = ensureOverlays();
  overlayRoot = root;
  o.link.hidden = true;
  o.mention.hidden = true;
  if (o.tablePicker) o.tablePicker.hidden = true;
  hideTableCellMenu();
  const inst = getInstance(root.getAttribute('data-editor'));
  let seed = 'a^2 + b^2 = c^2';
  savedInsertPos = null;
  if (inst) {
    savedInsertPos = inst.state.selection.from;
    const node = inst.state.doc.nodeAt(inst.state.selection.from);
    if (node && (node.type.name === 'inlineMath' || node.type.name === 'blockMath') && node.attrs.latex) {
      seed = node.attrs.latex;
    }
  }
  o.math.querySelector('.rte-math-input').value = seed;
  positionPopover(o.math, root, 'math');
  o.math.querySelector('.rte-math-input').focus();
}

function confirmMath() {
  const o = ensureOverlays();
  if (!o || !overlayRoot) return;
  const root = overlayRoot;
  const pos = savedInsertPos;
  const latex = (o.math.querySelector('.rte-math-input').value || '').trim() || 'a^2 + b^2 = c^2';
  const inst = getInstance(root.getAttribute('data-editor'));
  closeOverlays();
  if (root.classList.contains('mode-markdown')) {
    const ta = root.querySelector('textarea');
    const md = latex.includes('\n') ? ('$$\n' + latex + '\n$$') : ('$$' + latex + '$$');
    const s = ta.selectionStart;
    ta.value = ta.value.slice(0, s) + md + ta.value.slice(ta.selectionEnd);
    ta.dispatchEvent(new Event('input'));
  } else if (inst) {
    const node = { type: 'inlineMath', attrs: { latex, delim: 'dollar' } };
    const opts = { pos: typeof pos === 'number' ? pos : undefined };
    const ok = inst.chain().focus().insertInlineMath({ latex, ...opts }).run();
    if (!ok) {
      if (typeof pos === 'number') inst.chain().focus().insertContentAt(pos, node).run();
      else inst.chain().focus().insertContent(node).run();
    }
    flushMarkdown(root);
  }
  notifyChange();
}

function openHelpModal() {
  const o = ensureOverlays();
  o.help.hidden = false;
}

/* ---- upload ---- */
let pendingImgRoot = null;
let pendingFileRoot = null;

function ensureHiddenFileInput(id, accept, onChange) {
  let input = document.getElementById(id);
  if (!input) {
    input = document.createElement('input');
    input.type = 'file';
    input.id = id;
    input.style.display = 'none';
    if (accept) input.accept = accept;
    document.body.appendChild(input);
  }
  input.onchange = onChange;
  return input;
}

function pickImage(root) {
  pendingImgRoot = root;
  const input = ensureHiddenFileInput(config.imageInputId, 'image/*', onImagePicked);
  input.value = '';
  input.click();
}

function pickFile(root) {
  if (!config.fileUploadUrl) {
    alert(config.messages.fileUploadFailed);
    return;
  }
  pendingFileRoot = root;
  const input = ensureHiddenFileInput(config.fileInputId, '', onFilePicked);
  input.value = '';
  input.click();
}

function insertImageAt(root, url, alt) {
  const inst = getInstance(root.getAttribute('data-editor'));
  if (root.classList.contains('mode-markdown')) {
    const ta = root.querySelector('textarea');
    const md = '![' + (alt || '') + '](' + url + ')';
    const s = ta.selectionStart;
    ta.value = ta.value.slice(0, s) + md + ta.value.slice(ta.selectionEnd);
    ta.dispatchEvent(new Event('input'));
  } else if (inst) {
    inst.chain().focus().setImage({ src: url, alt: alt || '' }).run();
    flushMarkdown(root);
  }
  notifyChange();
}

function onImagePicked(e) {
  const file = e.target.files && e.target.files[0];
  const root = pendingImgRoot;
  pendingImgRoot = null;
  if (!file || !root) return;
  if (!config.imageUploadUrl) {
    alert(config.messages.uploadFailed);
    return;
  }
  const fd = new FormData();
  fd.append('markdown-image-upload', file);
  fetch(config.imageUploadUrl, {
    method: 'POST',
    headers: { 'X-CSRFToken': config.csrfToken },
    body: fd,
  })
    .then((r) => r.json().then((body) => ({ ok: r.ok, body })).catch(() => ({ ok: false, body: null })))
    .then(({ ok, body }) => {
      if (!ok || !body || body.status !== 200 || !body.link) {
        alert(config.messages.uploadFailedAuth);
        return;
      }
      insertImageAt(root, body.link, file.name || '');
    })
    .catch(() => alert(config.messages.uploadFailed));
}

function onFilePicked(e) {
  const file = e.target.files && e.target.files[0];
  const root = pendingFileRoot;
  pendingFileRoot = null;
  if (!file || !root) return;
  if (!config.fileUploadUrl) {
    alert(config.messages.fileUploadFailed);
    return;
  }
  const fd = new FormData();
  fd.append('markdown-file-upload', file);
  fetch(config.fileUploadUrl, {
    method: 'POST',
    headers: { 'X-CSRFToken': config.csrfToken },
    body: fd,
  })
    .then((r) => r.json().then((body) => ({ ok: r.ok, body })).catch(() => ({ ok: false, body: null })))
    .then(({ ok, body }) => {
      if (!ok || !body || body.status !== 200 || !body.link) {
        alert((body && body.error) || config.messages.fileUploadFailedAuth);
        return;
      }
      if (body.is_image) {
        insertImageAt(root, body.link, body.name || file.name || '');
        return;
      }
      const inst = getInstance(root.getAttribute('data-editor'));
      const label = String(body.name || file.name || 'file').replace(/[\r\n[\]]/g, ' ').trim() || 'file';
      if (root.classList.contains('mode-markdown')) {
        const ta = root.querySelector('textarea');
        const md = '[' + label + '](' + body.link + ')';
        const s = ta.selectionStart;
        ta.value = ta.value.slice(0, s) + md + ta.value.slice(ta.selectionEnd);
        ta.dispatchEvent(new Event('input'));
      } else if (inst) {
        inst.chain().focus().insertContent({
          type: 'text',
          text: label,
          marks: [{ type: 'link', attrs: { href: body.link } }],
        }).run();
        flushMarkdown(root);
      }
      notifyChange();
    })
    .catch(() => alert(config.messages.fileUploadFailed));
}

function escapeAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}
function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function configure(opts) {
  if (!opts) return;
  if (opts.imageUploadUrl != null) config.imageUploadUrl = opts.imageUploadUrl;
  if (opts.fileUploadUrl != null) config.fileUploadUrl = opts.fileUploadUrl;
  if (opts.searchUsersUrl != null) config.searchUsersUrl = opts.searchUsersUrl;
  if (opts.csrfToken != null) config.csrfToken = opts.csrfToken;
  if (opts.onChange != null) config.onChange = opts.onChange;
  if (opts.imageInputId != null) config.imageInputId = opts.imageInputId;
  if (opts.fileInputId != null) config.fileInputId = opts.fileInputId;
  if (opts.messages) Object.assign(config.messages, opts.messages);
  if (typeof document !== 'undefined') syncUploadButtons(document);
}

const RichEditor = {
  configure,
  html: richEditorTpl,
  mount(root) {
    mountRichEditors(root);
    initEditors(root);
  },
  init(root) {
    initEditors(root);
  },
  getMarkdown,
  getHtml: editorHtml,
  setMarkdown,
  htmlToMd: serializeHtmlToMarkdown,
  mdToHtml: parseMarkdownToHtml,
  onImagePicked,
  onFilePicked,
  _createHeadless: createHeadlessEditor,
  _instances: instances,
};

export default RichEditor;
