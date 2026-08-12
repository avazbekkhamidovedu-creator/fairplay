/**
 * Reusable rich-text / Markdown editor (dual mode).
 *
 * Sole source of truth: the Markdown <textarea> inside each .rich-editor.
 * Contenteditable is only a projection for rich mode; htmlToMd / mdToHtml
 * are projection helpers, never the authority for save/preview.
 *
 * Preferred for new authoring UIs (create_v2). Martor remains for legacy
 * admin forms; both feed the same server markdown preview pipeline.
 *
 * Browser: window.RichEditor
 * Node (tests): module.exports = RichEditor
 *
 * API:
 *   RichEditor.configure({ imageUploadUrl, csrfToken, onChange, messages })
 *   RichEditor.html(name, opts) / mount(root) / init(root)
 *   RichEditor.getMarkdown(name) / getHtml(name) / setMarkdown(name, md)
 *   RichEditor.htmlToMd(html) / mdToHtml(md)   // projection only
 *   RichEditor.onImagePicked(event)
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.RichEditor = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const config = {
    imageUploadUrl: '',
    searchUsersUrl: '',
    csrfToken: '',
    onChange: null,
    messages: {
      uploadFailed: 'Image upload failed.',
      uploadFailedAuth: 'Image upload failed. Make sure you are signed in with upload permission.',
    },
    imageInputId: 'img-file',
  };

  function notifyChange() {
    if (typeof config.onChange === 'function') config.onChange();
  }

  function t(msgid) {
    return (typeof gettext === 'function') ? gettext(msgid) : msgid;
  }

  // Icons aligned with Martor (Semantic → Font Awesome 4 equivalents).
  // Headings: site markdown demotes by +2 (# → h3), so labels match preview.
  function getRteButtons() {
    return {
      heading: '<select class="rte-heading" title="' + t('Text level') + '"><option value="p">' + t('Body') + '</option><option value="h3">' + t('Heading 1') + '</option><option value="h4">' + t('Heading 2') + '</option><option value="h5">' + t('Heading 3') + '</option></select>',
      sep: '<span class="sep"></span>',
      bold: '<button type="button" data-cmd="bold" title="' + t('Bold (Ctrl+B)') + '"><i class="fa fa-bold"></i></button>',
      italic: '<button type="button" data-cmd="italic" title="' + t('Italic (Ctrl+I)') + '"><i class="fa fa-italic"></i></button>',
      highlight: '<button type="button" data-cmd="highlight" class="hl-btn" title="' + t('Highlight') + '"><i class="fa fa-paint-brush"></i></button>',
      code: '<button type="button" data-cmd="code" title="' + t('Code') + '"><i class="fa fa-code"></i></button>',
      math: '<button type="button" data-cmd="math" title="' + t('Math (Ctrl+Alt+D)') + '"><i class="fa fa-superscript"></i></button>',
      quote: '<button type="button" data-cmd="quote" title="' + t('Quote (Ctrl+Q)') + '"><i class="fa fa-quote-left"></i></button>',
      ul: '<button type="button" data-cmd="ul" title="' + t('Unordered list (Ctrl+U)') + '"><i class="fa fa-list-ul"></i></button>',
      ol: '<button type="button" data-cmd="ol" title="' + t('Ordered list (Ctrl+Shift+O)') + '"><i class="fa fa-list-ol"></i></button>',
      table: '<button type="button" data-cmd="table" title="' + t('Table') + '"><i class="fa fa-table"></i></button>',
      link: '<button type="button" data-cmd="link" title="' + t('Link (Ctrl+L)') + '"><i class="fa fa-link"></i></button>',
      image: '<button type="button" data-cmd="image" title="' + t('Image') + '"><i class="fa fa-image"></i></button>',
      mention: '<button type="button" data-cmd="mention" title="' + t('Mention user (Ctrl+M)') + '"><i class="fa fa-at"></i></button>',
      help: '<button type="button" data-cmd="help" title="' + t('Markdown help') + '"><i class="fa fa-question-circle"></i></button>',
      alignLeft: '<button type="button" data-cmd="alignLeft" title="' + t('Align left') + '"><i class="fa fa-align-left"></i></button>',
      alignCenter: '<button type="button" data-cmd="alignCenter" title="' + t('Align center') + '"><i class="fa fa-align-center"></i></button>',
      alignRight: '<button type="button" data-cmd="alignRight" title="' + t('Align right') + '"><i class="fa fa-align-right"></i></button>',
      blank: '<button type="button" data-cmd="blank" class="blank-insert" title="' + t('Insert blank') + '" style="display:none;"><i class="fa fa-square-o"></i></button>',
    };
  }
  // Groups: 等级 | 加粗/高亮 | 代码/公式/引用 | 列表/表格 | 对齐 | 链接等
  const RTE_TOOLBAR = [
    'heading', 'sep',
    'bold', 'highlight', 'italic', 'sep',
    'code', 'math', 'quote', 'sep',
    'ul', 'ol', 'table', 'sep',
    'alignLeft', 'alignCenter', 'alignRight', 'sep',
    'link', 'image', 'mention', 'help', 'blank',
  ];
  const TABLE_PICKER_MAX = 10;

  /** Build a GFM pipe table. ``rows``/``cols`` are HTML dimensions (first row = header). */
  function buildPipeTableMd(rows, cols) {
    rows = Math.max(1, Math.min(TABLE_PICKER_MAX, rows | 0));
    cols = Math.max(1, Math.min(TABLE_PICKER_MAX, cols | 0));
    const header = [];
    const sep = [];
    for (let c = 0; c < cols; c++) {
      header.push((typeof interpolate === 'function')
        ? interpolate(t('Column %s'), [c + 1])
        : t('Column %s').replace('%s', String(c + 1)));
      sep.push('---');
    }
    const lines = [
      '| ' + header.join(' | ') + ' |',
      '| ' + sep.join(' | ') + ' |',
    ];
    for (let r = 1; r < rows; r++) {
      const cells = [];
      for (let c = 0; c < cols; c++) cells.push('');
      lines.push('| ' + cells.join(' | ') + ' |');
    }
    return lines.join('\n');
  }

  function richEditorTpl(name, opts) {
    opts = opts || {};
    const RTE_BUTTONS = getRteButtons();
    const buttons = RTE_TOOLBAR.map(k => RTE_BUTTONS[k] || '').join('');
    const placeholder = opts.placeholder || t('Enter content; $$math$$ supported…');
    const mdPlaceholder = opts.mdPlaceholder || t('Markdown and $$math$$ supported…');
    const compact = opts.compact ? ' compact' : '';
    return `<div class="rich-editor mode-rich${compact}" data-editor="${name}">
      <div class="rich-editor-toolbar-row">
        <div class="editor-mode-tabs">
          <button type="button" class="mode-btn active" data-mode="rich">${t('Rich text')}</button>
          <button type="button" class="mode-btn" data-mode="markdown">Markdown</button>
        </div>
        <div class="rich-toolbar">${buttons}</div>
      </div>
      <div class="rich-editor-rich"><div class="rich-editor-body" contenteditable="true" data-placeholder="${placeholder}"></div></div>
      <div class="rich-editor-markdown"><textarea placeholder="${mdPlaceholder}"></textarea></div>
    </div>`;
  }

  function mountRichEditors(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-rich-mount]').forEach(m => {
      const name = m.getAttribute('data-rich-mount');
      m.outerHTML = richEditorTpl(name, {
        toolbar: m.getAttribute('data-toolbar') || 'mini',
        compact: m.hasAttribute('data-compact'),
        placeholder: m.getAttribute('data-placeholder') || undefined,
      });
    });
  }

  function findEditor(name) {
    return document.querySelector('.rich-editor[data-editor="' + name + '"]');
  }

  function editorParts(editor) {
    return {
      rich: editor.querySelector('.rich-editor-body'),
      ta: editor.querySelector('.rich-editor-markdown textarea'),
    };
  }

  /** Push contenteditable → Markdown textarea (source of truth). */
  function syncMarkdownFromRich(editor) {
    const parts = editorParts(editor);
    if (!parts.rich || !parts.ta) return;
    parts.ta.value = normalizePipeTables(htmlToMd(parts.rich.innerHTML));
  }

  /** Rebuild contenteditable projection from Markdown textarea. */
  function projectRichFromMarkdown(editor) {
    const parts = editorParts(editor);
    if (!parts.rich || !parts.ta) return;
    parts.ta.value = normalizePipeTables(parts.ta.value);
    parts.rich.innerHTML = mdToHtml(parts.ta.value);
  }

  function getMd(name) {
    const ed = findEditor(name);
    if (!ed) return '';
    const ta = ed.querySelector('.rich-editor-markdown textarea');
    if (!ta) return '';
    if (!ed.classList.contains('mode-markdown')) syncMarkdownFromRich(ed);
    const normalized = normalizePipeTables(ta.value);
    if (normalized !== ta.value) ta.value = normalized;
    return ta.value;
  }

  function editorHtml(name) {
    return mdToHtml(getMd(name));
  }

  function setMarkdown(name, md) {
    const ed = findEditor(name);
    if (!ed) return;
    const ta = ed.querySelector('.rich-editor-markdown textarea');
    if (!ta) return;
    ta.value = normalizePipeTables(md || '');
    if (!ed.classList.contains('mode-markdown')) projectRichFromMarkdown(ed);
    syncPaneHeight(ed);
  }

  function measureContentHeight(el, floor) {
    if (!el) return floor;
    const prevMin = el.style.minHeight;
    const prevH = el.style.height;
    el.style.minHeight = '0';
    el.style.height = 'auto';
    // contenteditable/textarea scrollHeight after clearing forced min-height
    const measured = el.scrollHeight || 0;
    el.style.minHeight = prevMin;
    el.style.height = prevH;
    return Math.max(measured, floor);
  }

  function syncPaneHeight(editor) {
    const rich = editor.querySelector('.rich-editor-body');
    const ta = editor.querySelector('.rich-editor-markdown textarea');
    if (!rich || !ta) return;
    // Shell height is CSS-owned (same for rich / Markdown). Clear inline sizes
    // so mode switches cannot leave one pane taller/shorter than the other.
    rich.style.minHeight = '';
    rich.style.height = '';
    ta.style.minHeight = '';
    ta.style.height = '';
  }

  // Block-level cmds reproject from MD so rich view matches source (lists/quotes/fences).
  // Inline cmds (bold/italic/highlight/inline code) only sync, to keep the caret.
  const BLOCK_CMDS = {
    heading: 1, h3: 1, h4: 1, h5: 1, p: 1,
    ul: 1, ol: 1, quote: 1, table: 1,
  };

  function initEditors(root) {
    const scope = root || document;
    scope.querySelectorAll('.rich-editor').forEach(editor => {
      if (editor.dataset.bound) return;
      editor.dataset.bound = '1';
      const rich = editor.querySelector('.rich-editor-body');
      const ta = editor.querySelector('.rich-editor-markdown textarea');
      editor.querySelectorAll('.mode-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const toMd = btn.dataset.mode === 'markdown';
          const fromMd = editor.classList.contains('mode-markdown');
          if (toMd === fromMd) return;
          syncPaneHeight(editor);
          if (toMd) {
            // rich → markdown: flush projection into source
            syncMarkdownFromRich(editor);
          } else {
            // markdown → rich: rebuild projection from source
            projectRichFromMarkdown(editor);
          }
          editor.classList.toggle('mode-markdown', toMd);
          editor.classList.toggle('mode-rich', !toMd);
          editor.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b === btn));
          syncPaneHeight(editor);
          notifyChange();
          updateTableChrome(editor);
          if (toMd) hideTableChrome();
          closeTablePicker();
        });
      });
      if (rich) {
        rich.addEventListener('input', () => {
          syncMarkdownFromRich(editor);
          syncPaneHeight(editor);
          notifyChange();
        });
        rich.addEventListener('keyup', () => {
          saveSelection(editor);
          updateToolbarState(editor);
          updateTableChrome(editor);
        });
        rich.addEventListener('mouseup', () => {
          saveSelection(editor);
          updateToolbarState(editor);
          updateTableChrome(editor);
        });
        rich.addEventListener('click', () => updateTableChrome(editor));
        rich.addEventListener('keydown', (e) => handleEditorShortcut(editor, e));
      }
      if (ta) {
        ta.addEventListener('input', () => {
          syncPaneHeight(editor);
          notifyChange();
        });
        // Word / PDF / web paste often inserts blank lines between table rows,
        // which breaks GFM + server mistune. Collapse after paste.
        ta.addEventListener('paste', () => {
          setTimeout(() => {
            const next = normalizePipeTables(ta.value);
            if (next !== ta.value) {
              const pos = ta.selectionStart;
              ta.value = next;
              try { ta.selectionStart = ta.selectionEnd = Math.min(pos, next.length); } catch (e) { /* ignore */ }
              ta.dispatchEvent(new Event('input'));
            }
          }, 0);
        });
        ta.addEventListener('mouseup', () => {
          saveSelection(editor);
          syncPaneHeight(editor);
        });
        ta.addEventListener('keyup', () => saveSelection(editor));
        ta.addEventListener('keydown', (e) => handleEditorShortcut(editor, e));
      }
      editor.querySelectorAll('.rich-toolbar button[data-cmd]').forEach(btn => {
        // Preserve caret before toolbar steals focus (link / mention / align / image / table).
        btn.addEventListener('mousedown', (e) => {
          const cmd = btn.dataset.cmd;
          if (cmd === 'link' || cmd === 'mention' || cmd === 'image' || cmd === 'table' ||
              cmd === 'alignLeft' || cmd === 'alignCenter' || cmd === 'alignRight') {
            e.preventDefault();
            saveSelection(editor);
          }
        });
        btn.addEventListener('click', () => applyFormat(editor, btn.dataset.cmd, btn));
      });
      const headSel = editor.querySelector('.rte-heading');
      if (headSel) headSel.addEventListener('change', () => applyFormat(editor, headSel.value));
      // Initial projection from whatever is already in the textarea (usually empty).
      if (!editor.classList.contains('mode-markdown')) projectRichFromMarkdown(editor);
      syncPaneHeight(editor);
      updateTableChrome(editor);
    });
  }

  function selectionNode(area) {
    const sel = window.getSelection();
    if (!sel.rangeCount || !area) return null;
    let n = sel.anchorNode;
    if (!n) return null;
    if (n.nodeType === 3) n = n.parentElement;
    return area.contains(n) ? n : null;
  }

  function selectionInTag(area, tag) {
    const n = selectionNode(area);
    return n ? !!n.closest(tag) : false;
  }

  function unwrapTagFromSelection(area, tag) {
    const n = selectionNode(area);
    const el = n && n.closest(tag);
    if (!el || !area.contains(el)) return;
    const parent = el.parentNode;
    while (el.firstChild) parent.insertBefore(el.firstChild, el);
    parent.removeChild(el);
  }

  function updateToolbarState(editor) {
    if (!editor || editor.classList.contains('mode-markdown')) return;
    const area = editor.querySelector('.rich-editor-body');
    if (!area || !selectionNode(area)) return;
    editor.querySelectorAll('.rich-toolbar button[data-cmd]').forEach(btn => {
      const cmd = btn.dataset.cmd;
      let active = false;
      switch (cmd) {
        case 'bold': active = document.queryCommandState('bold'); break;
        case 'italic': active = document.queryCommandState('italic'); break;
        case 'highlight': active = selectionInTag(area, 'mark'); break;
        case 'ul': active = document.queryCommandState('insertUnorderedList'); break;
        case 'ol': active = document.queryCommandState('insertOrderedList'); break;
        case 'quote': active = selectionInTag(area, 'blockquote'); break;
        case 'code': active = selectionInTag(area, 'code') || selectionInTag(area, 'pre'); break;
        case 'link': active = selectionInTag(area, 'a'); break;
      }
      btn.classList.toggle('active', !!active);
    });
    const headSel = editor.querySelector('.rte-heading');
    if (headSel) {
      const block = selectionNode(area) && selectionNode(area).closest('h3,h4,h5,h1,h2,p,blockquote');
      if (block) {
        const map = { H1: 'h3', H2: 'h3', H3: 'h3', H4: 'h4', H5: 'h5', H6: 'h5', P: 'p' };
        headSel.value = map[block.tagName] || 'p';
      }
    }
  }

  let selectionChangeBound = false;
  function ensureSelectionListener() {
    if (selectionChangeBound || typeof document === 'undefined') return;
    selectionChangeBound = true;
    document.addEventListener('selectionchange', () => {
      const sel = window.getSelection();
      if (!sel.rangeCount) return;
      let n = sel.anchorNode;
      if (!n) return;
      if (n.nodeType === 3) n = n.parentElement;
      if (!n || !n.closest) return;
      const area = n.closest('.rich-editor-body');
      if (area) {
        const ed = area.closest('.rich-editor');
        updateToolbarState(ed);
        updateTableChrome(ed);
      }
    });
  }

  function escapeAttr(s) {
    return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  }

  function handleEditorShortcut(editor, e) {
    const mod = e.ctrlKey || e.metaKey;
    if (!mod) return;
    const key = (e.key || '').toLowerCase();
    let cmd = null;
    if (e.altKey) {
      if (key === 'd') cmd = 'math';
      else if (key === '1') cmd = 'h3';
      else if (key === '2') cmd = 'h4';
      else if (key === '3') cmd = 'h5';
    } else if (e.shiftKey) {
      if (key === 'o') cmd = 'ol';
    } else {
      if (key === 'b') cmd = 'bold';
      else if (key === 'i') cmd = 'italic';
      else if (key === 'l') cmd = 'link';
      else if (key === 'q') cmd = 'quote';
      else if (key === 'u') cmd = 'ul';
      else if (key === 'm') cmd = 'mention';
    }
    if (!cmd) return;
    e.preventDefault();
    applyFormat(editor, cmd);
  }

  function applyFormat(editor, cmd, btn) {
    if (cmd === 'image') { pickImage(editor); return; }
    if (cmd === 'help') { openHelpModal(); return; }
    if (cmd === 'link') { openLinkPopover(editor); return; }
    if (cmd === 'mention') { openMentionPopover(editor); return; }
    if (cmd === 'table') { openTablePicker(editor, btn); return; }

    const isMd = editor.classList.contains('mode-markdown');
    if (isMd) {
      const ta = editor.querySelector('.rich-editor-markdown textarea');
      applyMarkdown(ta, cmd);
      ta.focus();
      ta.dispatchEvent(new Event('input'));
      return;
    }
    const area = editor.querySelector('.rich-editor-body');
    area.focus();
    let isBlockCode = false;
    switch (cmd) {
      case 'bold': document.execCommand('bold'); break;
      case 'italic': document.execCommand('italic'); break;
      case 'highlight':
        if (selectionInTag(area, 'mark')) unwrapTagFromSelection(area, 'mark');
        else wrapInline('mark');
        break;
      case 'heading': case 'h3': document.execCommand('formatBlock', false, 'h3'); break;
      case 'h4': document.execCommand('formatBlock', false, 'h4'); break;
      case 'h5': document.execCommand('formatBlock', false, 'h5'); break;
      case 'p': document.execCommand('formatBlock', false, 'p'); break;
      case 'ul': document.execCommand('insertUnorderedList'); break;
      case 'ol': document.execCommand('insertOrderedList'); break;
      case 'quote':
        if (selectionInTag(area, 'blockquote')) document.execCommand('formatBlock', false, 'p');
        else document.execCommand('formatBlock', false, 'blockquote');
        break;
      case 'code':
        if (selectionInTag(area, 'pre')) {
          unwrapTagFromSelection(area, 'pre');
          isBlockCode = true;
        } else if (selectionInTag(area, 'code') && !selectionInTag(area, 'pre')) {
          unwrapTagFromSelection(area, 'code');
        } else {
          const sel = window.getSelection();
          const text = (sel && sel.rangeCount) ? sel.toString() : '';
          if (!text || text.indexOf('\n') !== -1) {
            const body = (text || 'code').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            document.execCommand('insertHTML', false, '<pre><code>' + body + '</code></pre><p><br></p>');
            isBlockCode = true;
          } else {
            wrapInline('code');
          }
        }
        break;
      case 'alignLeft': applyTextAlign(area, 'left'); break;
      case 'alignCenter': applyTextAlign(area, 'center'); break;
      case 'alignRight': applyTextAlign(area, 'right'); break;
      case 'math':
        document.execCommand('insertText', false, '$$ a^2 + b^2 = c^2 $$');
        break;
      case 'blank': document.execCommand('insertText', false, '{{blank}}'); break;
    }
    // Always flush into Markdown source; reproject block cmds so rich view matches MD.
    // Align skips reproject: wrapping/rebuilding can drop resized img width/height.
    syncMarkdownFromRich(editor);
    if (BLOCK_CMDS[cmd] || isBlockCode) {
      projectRichFromMarkdown(editor);
    }
    syncPaneHeight(editor);
    notifyChange();
    updateToolbarState(editor);
    updateTableChrome(editor);
  }

  /** Align current block or selected image; keeps heading tags (unlike bare justify*). */
  function applyTextAlign(area, align) {
    restoreSavedSelection(area);
    const sel = window.getSelection();
    let node = selectionNode(area);
    let img = null;
    if (node) {
      if (node.tagName === 'IMG') img = node;
      else if (node.closest) {
        const around = node.closest('img');
        if (around && area.contains(around)) img = around;
      }
    }
    if (!img && sel && sel.rangeCount) {
      const r = sel.getRangeAt(0);
      const c = r.commonAncestorContainer;
      const el = c.nodeType === 1 ? c : c.parentElement;
      if (el) {
        if (el.tagName === 'IMG' && area.contains(el)) img = el;
        else {
          const found = el.querySelector && el.querySelector('img.img-selected');
          if (found && area.contains(found)) img = found;
        }
      }
    }
    if (!img) img = area.querySelector('img.img-selected');

    const target = img || (node && node.closest &&
      node.closest('h1,h2,h3,h4,h5,h6,p,div,blockquote,li'));
    if (!target || !area.contains(target)) {
      document.execCommand('justify' + align.charAt(0).toUpperCase() + align.slice(1));
      return;
    }

    // Prefer an existing align wrapper around the target.
    let wrap = target.parentElement && target.parentElement !== area &&
      target.parentElement.matches &&
      target.parentElement.matches('div[style*="text-align"]')
      ? target.parentElement : null;
    if (!wrap && target.tagName === 'DIV' && /text-align/i.test(target.getAttribute('style') || '')) {
      wrap = target;
    }

    if (img) {
      if (!wrap) {
        wrap = document.createElement('div');
        // Avoid invalid <p><div>…</div></p> (browser repair can reshuffle the img).
        const parent = img.parentElement;
        if (parent && parent !== area && /^(P|H[1-6]|LI|SPAN)$/.test(parent.tagName) &&
            parent.parentElement) {
          parent.parentElement.insertBefore(wrap, parent);
          wrap.appendChild(img);
          if (!parent.textContent.trim() && !parent.querySelector('img,video,br')) {
            parent.remove();
          }
        } else {
          img.parentNode.insertBefore(wrap, img);
          wrap.appendChild(img);
        }
      }
      if (align === 'left') {
        wrap.style.textAlign = 'left';
      } else {
        wrap.style.textAlign = align;
      }
      return;
    }

    // Headings / paragraphs: set style on the element itself (serialized with tag preserved).
    if (/^H[1-6]$/.test(target.tagName) || target.tagName === 'P' || target.tagName === 'BLOCKQUOTE') {
      if (align === 'left') target.style.textAlign = '';
      else target.style.textAlign = align;
      if (wrap && wrap !== target && wrap.children.length === 1 && wrap.firstChild === target) {
        // unwrap redundant wrapper
        wrap.parentNode.insertBefore(target, wrap);
        wrap.remove();
      }
      return;
    }

    if (wrap) {
      wrap.style.textAlign = align === 'left' ? 'left' : align;
      return;
    }
    document.execCommand('justify' + align.charAt(0).toUpperCase() + align.slice(1));
  }

  function restoreSavedSelection(area) {
    if (!savedRange || savedRange.md || !savedRange.range) return;
    try {
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(savedRange.range);
      if (area) area.focus();
    } catch (err) { /* range may be stale */ }
  }

  let pendingImgEditor = null;
  function pickImage(editor) {
    pendingImgEditor = editor;
    const input = document.getElementById(config.imageInputId);
    if (!input) return;
    input.value = '';
    input.click();
  }

  function onImagePicked(e) {
    const file = e.target.files && e.target.files[0];
    if (!file || !pendingImgEditor) return;
    const ed = pendingImgEditor;
    pendingImgEditor = null;
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
      .then(r => r.json().then(body => ({ ok: r.ok, body })).catch(() => ({ ok: false, body: null })))
      .then(({ ok, body }) => {
        if (!ok || !body || body.status !== 200 || !body.link) {
          alert(config.messages.uploadFailedAuth);
          return;
        }
        insertImageUrl(ed, body.link);
      })
      .catch(() => alert(config.messages.uploadFailed));
  }

  function insertImageUrl(ed, url) {
    if (ed.classList.contains('mode-markdown')) {
      const ta = ed.querySelector('.rich-editor-markdown textarea');
      const s = savedRange && savedRange.md ? savedRange.start : ta.selectionStart;
      const e = savedRange && savedRange.md ? savedRange.end : ta.selectionEnd;
      const md = '![](' + url + ')';
      ta.value = ta.value.slice(0, s) + md + ta.value.slice(e);
      ta.selectionStart = ta.selectionEnd = s + md.length;
      ta.focus();
      ta.dispatchEvent(new Event('input'));
      clearSavedSelection();
      return;
    }
    const area = ed.querySelector('.rich-editor-body');
    restoreSavedSelection(area);
    document.execCommand('insertHTML', false,
      '<img src="' + escapeAttr(url) + '" style="max-width:100%;border-radius:4px;">');
    syncMarkdownFromRich(ed);
    notifyChange();
    clearSavedSelection();
  }

  function wrapInline(tag) {
    const sel = window.getSelection();
    if (!sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    const el = document.createElement(tag);
    el.appendChild(range.extractContents());
    range.insertNode(el);
    sel.removeAllRanges();
  }

  function applyMarkdown(ta, cmd) {
    const s = ta.selectionStart, e = ta.selectionEnd, sel = ta.value.slice(s, e);
    const wrap = (pre, post, ph) => {
      const txt = sel || ph;
      ta.value = ta.value.slice(0, s) + pre + txt + post + ta.value.slice(e);
      const start = s + pre.length;
      ta.selectionStart = start;
      ta.selectionEnd = start + txt.length;
    };
    const insertAtCaret = (text, selectOffset, selectLen) => {
      ta.value = ta.value.slice(0, s) + text + ta.value.slice(e);
      const pos = s + (selectOffset == null ? text.length : selectOffset);
      ta.selectionStart = pos;
      ta.selectionEnd = pos + (selectLen || 0);
    };
    const linePrefix = (pre) => {
      const ls = ta.value.lastIndexOf('\n', s - 1) + 1;
      ta.value = ta.value.slice(0, ls) + pre + ta.value.slice(ls);
    };
    switch (cmd) {
      case 'bold': wrap('**', '**', t('Bold')); break;
      case 'italic': wrap('*', '*', t('Italic')); break;
      case 'highlight': wrap('<mark>', '</mark>', t('Highlight')); break;
      case 'code':
        if (sel && sel.indexOf('\n') !== -1) wrap('```\n', '\n```', 'code');
        else wrap('`', '`', 'code');
        break;
      case 'quote': linePrefix('> '); break;
      // Match site renderer: # → h3, ## → h4, ### → h5
      case 'heading': case 'h3': linePrefix('# '); break;
      case 'h4': linePrefix('## '); break;
      case 'h5': linePrefix('### '); break;
      case 'ul': linePrefix('- '); break;
      case 'ol': linePrefix('1. '); break;
      case 'alignLeft': case 'alignCenter': case 'alignRight': break;
      case 'math':
        if (sel && sel.indexOf('\n') !== -1) wrap('$$\n', '\n$$', 'a^2+b^2=c^2');
        else wrap('$$ ', ' $$', 'a^2 + b^2 = c^2');
        break;
      case 'mention':
        if (sel) wrap('[user:', ']', sel);
        else insertAtCaret('[user:]', 6, 0);
        break;
      case 'blank': insertAtCaret('{{blank}}'); break;
    }
  }

  /* ---- Table size picker + row/col controls ---- */
  let tablePickerEditor = null;
  let tablePickerDocBound = false;

  function insertPipeTable(editor, rows, cols) {
    const md = buildPipeTableMd(rows, cols);
    if (editor.classList.contains('mode-markdown')) {
      const ta = editor.querySelector('.rich-editor-markdown textarea');
      if (!ta) return;
      const s = ta.selectionStart;
      const e = ta.selectionEnd;
      const pad = (s > 0 && ta.value[s - 1] !== '\n') ? '\n\n' : (s > 0 ? '\n' : '');
      const text = pad + md + '\n';
      ta.value = ta.value.slice(0, s) + text + ta.value.slice(e);
      const pos = s + text.length;
      ta.selectionStart = ta.selectionEnd = pos;
      ta.focus();
      ta.dispatchEvent(new Event('input'));
      return;
    }
    const area = editor.querySelector('.rich-editor-body');
    if (!area) return;
    area.focus();
    restoreSavedSelection(area);
    document.execCommand('insertHTML', false, mdToHtml(md) + '<p><br></p>');
    syncMarkdownFromRich(editor);
    projectRichFromMarkdown(editor);
    syncPaneHeight(editor);
    notifyChange();
    updateToolbarState(editor);
    updateTableChrome(editor);
  }

  function ensureTablePicker() {
    let el = document.getElementById('rte-table-picker');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'rte-table-picker';
    el.className = 'rte-table-picker';
    el.hidden = true;
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', 'Insert table');
    let cells = '';
    for (let r = 1; r <= TABLE_PICKER_MAX; r++) {
      for (let c = 1; c <= TABLE_PICKER_MAX; c++) {
        cells += '<button type="button" class="rte-table-picker-cell" data-r="' + r +
          '" data-c="' + c + '" aria-label="' + r + '\u00d7' + c + '"></button>';
      }
    }
    el.innerHTML =
      '<div class="rte-table-picker-grid">' + cells + '</div>' +
      '<div class="rte-table-picker-label">Insert a 1\u00d71 table</div>';
    document.body.appendChild(el);

    const label = el.querySelector('.rte-table-picker-label');
    const paint = (rows, cols) => {
      el.querySelectorAll('.rte-table-picker-cell').forEach((cell) => {
        const r = +cell.dataset.r;
        const c = +cell.dataset.c;
        cell.classList.toggle('is-active', r <= rows && c <= cols);
      });
      label.textContent = 'Insert a ' + rows + '\u00d7' + cols + ' table';
    };
    paint(1, 1);

    el.addEventListener('mouseover', (e) => {
      const cell = e.target.closest('.rte-table-picker-cell');
      if (!cell || !el.contains(cell)) return;
      paint(+cell.dataset.r, +cell.dataset.c);
    });
    el.addEventListener('mouseleave', () => paint(1, 1));
    el.addEventListener('mousedown', (e) => e.preventDefault());
    el.addEventListener('click', (e) => {
      const cell = e.target.closest('.rte-table-picker-cell');
      if (!cell || !tablePickerEditor) return;
      const editor = tablePickerEditor;
      const rows = +cell.dataset.r;
      const cols = +cell.dataset.c;
      closeTablePicker();
      insertPipeTable(editor, rows, cols);
    });
    return el;
  }

  function closeTablePicker() {
    const el = document.getElementById('rte-table-picker');
    if (el) el.hidden = true;
    tablePickerEditor = null;
  }

  function openTablePicker(editor, anchorBtn) {
    const el = ensureTablePicker();
    if (!el.hidden && tablePickerEditor === editor) {
      closeTablePicker();
      return;
    }
    tablePickerEditor = editor;
    el.hidden = false;
    el.querySelectorAll('.rte-table-picker-cell').forEach((c) => c.classList.remove('is-active'));
    const first = el.querySelector('.rte-table-picker-cell[data-r="1"][data-c="1"]');
    if (first) first.classList.add('is-active');
    const label = el.querySelector('.rte-table-picker-label');
    if (label) label.textContent = 'Insert a 1\u00d71 table';

    const btn = anchorBtn || editor.querySelector('.rich-toolbar button[data-cmd="table"]');
    const rect = (btn || editor).getBoundingClientRect();
    const pad = 6;
    // position:fixed — use viewport coordinates
    el.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 280)) + 'px';
    el.style.top = (rect.bottom + pad) + 'px';

    if (!tablePickerDocBound) {
      tablePickerDocBound = true;
      document.addEventListener('mousedown', (e) => {
        const picker = document.getElementById('rte-table-picker');
        if (!picker || picker.hidden) return;
        if (picker.contains(e.target)) return;
        if (e.target.closest && e.target.closest('.rich-toolbar button[data-cmd="table"]')) return;
        closeTablePicker();
      });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeTablePicker();
      });
    }
  }

  let tableChromeEditor = null;
  let tableChromeDocBound = false;

  function ensureTableChrome() {
    let root = document.getElementById('rte-table-cell-chrome');
    if (root) return root;
    root = document.createElement('div');
    root.id = 'rte-table-cell-chrome';
    root.className = 'rte-table-cell-chrome';
    root.hidden = true;
    root.innerHTML =
      '<button type="button" class="rte-table-cell-handle" title="' + t('Table actions') + '" aria-label="' + t('Table actions') + '">' +
      '<i class="fa fa-chevron-down"></i></button>' +
      '<div class="rte-table-cell-menu" hidden>' +
      '<button type="button" data-table-act="col-left">Insert column left</button>' +
      '<button type="button" data-table-act="col-right">Insert column right</button>' +
      '<button type="button" data-table-act="row-below">Insert row below</button>' +
      '<div class="rte-table-cell-menu-sep"></div>' +
      '<button type="button" data-table-act="del-row">Delete row</button>' +
      '<button type="button" data-table-act="del-col">Delete column</button>' +
      '<button type="button" data-table-act="del-table">Delete table</button>' +
      '</div>';
    document.body.appendChild(root);

    const handle = root.querySelector('.rte-table-cell-handle');
    const menu = root.querySelector('.rte-table-cell-menu');
    handle.addEventListener('mousedown', (e) => e.preventDefault());
    menu.addEventListener('mousedown', (e) => e.preventDefault());
    handle.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const open = menu.hidden;
      menu.hidden = !open;
      root.classList.toggle('menu-open', open);
      if (open) positionTableChromeMenu(root);
    });
    menu.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-table-act]');
      if (!btn || !tableChromeEditor) return;
      e.preventDefault();
      e.stopPropagation();
      const ed = tableChromeEditor;
      menu.hidden = true;
      root.classList.remove('menu-open');
      runTableAction(ed, btn.getAttribute('data-table-act'));
    });

    if (!tableChromeDocBound) {
      tableChromeDocBound = true;
      document.addEventListener('mousedown', (e) => {
        const chrome = document.getElementById('rte-table-cell-chrome');
        if (!chrome || chrome.hidden) return;
        if (chrome.contains(e.target)) return;
        chrome.querySelector('.rte-table-cell-menu').hidden = true;
        chrome.classList.remove('menu-open');
      });
      window.addEventListener('scroll', () => {
        if (tableChromeEditor) updateTableChrome(tableChromeEditor);
      }, true);
      window.addEventListener('resize', () => {
        if (tableChromeEditor) updateTableChrome(tableChromeEditor);
      });
    }
    return root;
  }

  function hideTableChrome() {
    const root = document.getElementById('rte-table-cell-chrome');
    if (!root) return;
    root.hidden = true;
    root.classList.remove('menu-open');
    const menu = root.querySelector('.rte-table-cell-menu');
    if (menu) menu.hidden = true;
    document.querySelectorAll('.rich-editor-body .rte-table-cell-active').forEach((c) => {
      c.classList.remove('rte-table-cell-active');
    });
    tableChromeEditor = null;
  }

  function positionTableChromeMenu(root) {
    const handle = root.querySelector('.rte-table-cell-handle');
    const menu = root.querySelector('.rte-table-cell-menu');
    if (!handle || !menu || menu.hidden) return;
    const hr = handle.getBoundingClientRect();
    const mw = 220;
    let left = hr.left + hr.width / 2 - mw / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - mw - 8));
    // Prefer above the handle (Notion-style); fall back below if no room.
    menu.style.width = mw + 'px';
    const mh = menu.offsetHeight || 200;
    let top = hr.top - mh - 8;
    if (top < 8) top = hr.bottom + 8;
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';
  }

  function closestEditorTable(node, area) {
    if (!node || !area) return null;
    let el = node.nodeType === 1 ? node : node.parentElement;
    if (!el || !el.closest) return null;
    const wrap = el.closest('.h-scrollable-table');
    if (wrap && area.contains(wrap)) return wrap.querySelector('table');
    const table = el.closest('table');
    return (table && area.contains(table)) ? table : null;
  }

  function activeTableContext(editor) {
    const area = editor.querySelector('.rich-editor-body');
    if (!area || editor.classList.contains('mode-markdown')) return null;
    const node = selectionNode(area);
    const table = closestEditorTable(node, area);
    if (!table) return null;
    let el = node && (node.nodeType === 1 ? node : node.parentElement);
    const cell = el && el.closest ? el.closest('th,td') : null;
    if (!cell || !table.contains(cell)) return null;
    const row = cell.parentElement;
    const colIndex = Array.prototype.indexOf.call(row.children, cell);
    return { table: table, cell: cell, row: row, colIndex: colIndex };
  }

  /** Show cell chevron handle when caret is inside a table cell. */
  function updateTableChrome(editor) {
    // Drop legacy toolbar bar if an older session injected it.
    const legacy = editor && editor.querySelector && editor.querySelector('.rte-table-controls');
    if (legacy) legacy.remove();

    const ctx = editor ? activeTableContext(editor) : null;
    if (!ctx || !ctx.cell) {
      if (tableChromeEditor === editor) hideTableChrome();
      return;
    }
    const root = ensureTableChrome();
    tableChromeEditor = editor;
    editor._rteTableCtx = ctx;
    document.querySelectorAll('.rich-editor-body .rte-table-cell-active').forEach((c) => {
      if (c !== ctx.cell) c.classList.remove('rte-table-cell-active');
    });
    ctx.cell.classList.add('rte-table-cell-active');

    const rect = ctx.cell.getBoundingClientRect();
    root.hidden = false;
    // Tiny handle in the cell's top-right corner — must not cover editing area.
    const size = 18;
    const pad = 2;
    root.style.left = Math.max(0, rect.right - size - pad) + 'px';
    root.style.top = (rect.top + pad) + 'px';
    if (!root.querySelector('.rte-table-cell-menu').hidden) positionTableChromeMenu(root);
  }

  // Back-compat name used by older call sites in this file.
  function updateTableControls(editor) {
    updateTableChrome(editor);
  }

  function tableColCount(table) {
    const head = table.querySelector('thead tr') || table.querySelector('tr');
    return head ? head.children.length : 0;
  }

  function makeTableCell(tag) {
    const cell = document.createElement(tag || 'td');
    cell.innerHTML = '<br>';
    return cell;
  }

  function insertColumnAt(table, at, tagForRow) {
    table.querySelectorAll('tr').forEach((tr) => {
      const tag = tagForRow ? tagForRow(tr) : (tr.closest('thead') ? 'th' : 'td');
      const cell = makeTableCell(tag);
      if (at >= tr.children.length) tr.appendChild(cell);
      else tr.insertBefore(cell, tr.children[at]);
    });
  }

  function runTableAction(editor, act) {
    const area = editor.querySelector('.rich-editor-body');
    if (!area) return;
    area.focus();
    restoreSavedSelection(area);
    let ctx = activeTableContext(editor) || editor._rteTableCtx;
    if (!ctx || !ctx.table || !area.contains(ctx.table)) {
      hideTableChrome();
      return;
    }
    const table = ctx.table;
    const wrap = table.closest('.h-scrollable-table') || table;
    const cols = tableColCount(table);
    if (cols < 1) return;

    if (act === 'del-table') {
      const p = document.createElement('p');
      p.innerHTML = '<br>';
      wrap.parentNode.insertBefore(p, wrap);
      wrap.parentNode.removeChild(wrap);
    } else if (act === 'row-below') {
      let tbody = table.querySelector('tbody');
      if (!tbody) {
        tbody = document.createElement('tbody');
        table.appendChild(tbody);
      }
      const tr = document.createElement('tr');
      for (let i = 0; i < cols; i++) tr.appendChild(makeTableCell('td'));
      if (ctx.row && ctx.row.parentElement === tbody) {
        tbody.insertBefore(tr, ctx.row.nextSibling);
      } else if (ctx.row && ctx.row.closest('thead')) {
        tbody.insertBefore(tr, tbody.firstChild);
      } else {
        tbody.appendChild(tr);
      }
    } else if (act === 'col-left') {
      if (cols >= TABLE_PICKER_MAX) return;
      const at = ctx.colIndex >= 0 ? ctx.colIndex : 0;
      insertColumnAt(table, at);
    } else if (act === 'col-right') {
      if (cols >= TABLE_PICKER_MAX) return;
      const at = ctx.colIndex >= 0 ? ctx.colIndex + 1 : cols;
      insertColumnAt(table, at);
    } else if (act === 'del-row') {
      if (!ctx.row) return;
      // Header row is required by GFM — deleting it removes the whole table.
      if (ctx.row.closest('thead')) {
        const p = document.createElement('p');
        p.innerHTML = '<br>';
        wrap.parentNode.insertBefore(p, wrap);
        wrap.parentNode.removeChild(wrap);
      } else {
        const parent = ctx.row.parentElement;
        parent.removeChild(ctx.row);
      }
    } else if (act === 'del-col') {
      if (ctx.colIndex < 0) return;
      if (cols <= 1) {
        const p = document.createElement('p');
        p.innerHTML = '<br>';
        wrap.parentNode.insertBefore(p, wrap);
        wrap.parentNode.removeChild(wrap);
      } else {
        table.querySelectorAll('tr').forEach((tr) => {
          if (tr.children[ctx.colIndex]) tr.removeChild(tr.children[ctx.colIndex]);
        });
      }
    } else {
      return;
    }

    // Sync MD from the mutated DOM; skip reproject so caret/layout stay stable.
    syncMarkdownFromRich(editor);
    syncPaneHeight(editor);
    notifyChange();
    updateToolbarState(editor);
    updateTableChrome(editor);
  }

  /* ---- Link / mention / help overlays ---- */
  let overlayEditor = null;
  let savedRange = null;
  let mentionTimer = null;

  function ensureOverlays() {
    if (typeof document === 'undefined') return null;
    if (document.getElementById('rte-overlays')) {
      return {
        root: document.getElementById('rte-overlays'),
        link: document.getElementById('rte-link-popover'),
        mention: document.getElementById('rte-mention-popover'),
        help: document.getElementById('rte-help-modal'),
      };
    }
    const root = document.createElement('div');
    root.id = 'rte-overlays';
    root.innerHTML = `
      <div id="rte-link-popover" class="rte-popover" hidden>
        <div class="rte-popover-title">${t('Insert link')}</div>
        <label class="rte-field"><span>${t('Text')}</span><input type="text" class="rte-link-text" placeholder="${t('Link text')}"></label>
        <label class="rte-field"><span>${t('URL')}</span><input type="url" class="rte-link-url" placeholder="https://"></label>
        <div class="rte-popover-actions">
          <button type="button" class="rte-btn-cancel">${t('Cancel')}</button>
          <button type="button" class="rte-btn-ok">${t('Insert')}</button>
        </div>
      </div>
      <div id="rte-mention-popover" class="rte-popover" hidden>
        <div class="rte-popover-title">${t('Mention user')}</div>
        <label class="rte-field"><span>${t('Username')}</span><input type="text" class="rte-mention-input" placeholder="${t('Search users…')}" autocomplete="off"></label>
        <ul class="rte-mention-results"></ul>
        <div class="rte-popover-actions">
          <button type="button" class="rte-btn-cancel">${t('Cancel')}</button>
          <button type="button" class="rte-btn-ok">${t('Insert')}</button>
        </div>
      </div>
      <div id="rte-help-modal" class="rte-help-modal" hidden>
        <div class="rte-help-dialog">
          <div class="rte-help-head">
            <h2><i class="fa fa-question-circle"></i> ${t('Markdown help')}</h2>
            <button type="button" class="rte-help-close" aria-label="${t('Close')}">×</button>
          </div>
          <div class="rte-help-body">
            <p>${t('Problem statements use Markdown. Full syntax:')} <a href="https://commonmark.org/help/" target="_blank" rel="noopener">CommonMark</a>.</p>
            <table class="rte-help-table">
              <thead><tr><th>${t('Syntax')}</th><th>${t('Shortcut')}</th><th>${t('Result')}</th></tr></thead>
              <tbody>
                <tr><td><code>**bold**</code></td><td>Ctrl+B</td><td><strong>${t('bold')}</strong></td></tr>
                <tr><td><code>*italic*</code></td><td>Ctrl+I</td><td><em>${t('italic')}</em></td></tr>
                <tr><td><code># ${t('Heading')}</code></td><td>Ctrl+Alt+1</td><td>${t('Level-1 heading (larger in preview)')}</td></tr>
                <tr><td><code>## ${t('Heading')}</code></td><td>Ctrl+Alt+2</td><td>${t('Level-2 heading')}</td></tr>
                <tr><td><code>### ${t('Heading')}</code></td><td>Ctrl+Alt+3</td><td>${t('Level-3 heading')}</td></tr>
                <tr><td><code>[${t('text')}](https://)</code></td><td>Ctrl+L</td><td>${t('Link')}</td></tr>
                <tr><td><code>![${t('alt')}](url)</code></td><td>—</td><td>${t('Image')}</td></tr>
                <tr><td><code>&gt; ${t('quote')}</code></td><td>Ctrl+Q</td><td>${t('Block quote')}</td></tr>
                <tr><td><code>- ${t('list')}</code> / <code>1. ${t('list')}</code></td><td>Ctrl+U / Ctrl+Shift+O</td><td>${t('List')}</td></tr>
                <tr><td><code>| A | B |</code><br><code>| --- | --- |</code><br><code>| 1 | 2 |</code></td><td>—</td><td>${t('Table')}</td></tr>
                <tr><td><code>\`code\`</code> / ${t('fenced code block')}</td><td>—</td><td>${t('Code')}</td></tr>
                <tr><td><code>$$ a^2+b^2 $$</code></td><td>Ctrl+Alt+D</td><td>${t('Math')}</td></tr>
                <tr><td><code>[user:${t('username')}]</code></td><td>Ctrl+M</td><td>${t('Mention user')}</td></tr>
                <tr><td><code>&lt;mark&gt;${t('highlight')}&lt;/mark&gt;</code></td><td>—</td><td>${t('Highlight')}</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>`;
    document.body.appendChild(root);
    const link = document.getElementById('rte-link-popover');
    const mention = document.getElementById('rte-mention-popover');
    const help = document.getElementById('rte-help-modal');
    link.querySelector('.rte-btn-cancel').addEventListener('click', () => hidePopovers());
    link.querySelector('.rte-btn-ok').addEventListener('click', confirmLinkPopover);
    link.querySelector('.rte-link-url').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); confirmLinkPopover(); }
      if (e.key === 'Escape') hidePopovers();
    });
    mention.querySelector('.rte-btn-cancel').addEventListener('click', () => hidePopovers());
    mention.querySelector('.rte-btn-ok').addEventListener('click', () => confirmMention());
    const mi = mention.querySelector('.rte-mention-input');
    mi.addEventListener('input', () => scheduleMentionSearch(mi.value));
    mi.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const first = mention.querySelector('.rte-mention-results li');
        if (first) confirmMention(first.dataset.username);
        else confirmMention();
      }
      if (e.key === 'Escape') hidePopovers();
    });
    mention.querySelector('.rte-mention-results').addEventListener('click', (e) => {
      const li = e.target.closest('li');
      if (li) confirmMention(li.dataset.username);
    });
    help.querySelector('.rte-help-close').addEventListener('click', () => { help.hidden = true; });
    help.addEventListener('click', (e) => { if (e.target === help) help.hidden = true; });
    document.addEventListener('mousedown', (e) => {
      if (e.target.closest && (e.target.closest('#rte-link-popover') ||
          e.target.closest('#rte-mention-popover') ||
          e.target.closest('.rich-toolbar button[data-cmd="link"]') ||
          e.target.closest('.rich-toolbar button[data-cmd="mention"]'))) return;
      if (!link.hidden || !mention.hidden) hidePopovers();
    });
    return { root, link, mention, help };
  }

  function hidePopovers() {
    const o = ensureOverlays();
    if (!o) return;
    o.link.hidden = true;
    o.mention.hidden = true;
    clearSavedSelection();
  }

  function clearSavedSelection() {
    savedRange = null;
    overlayEditor = null;
  }

  function saveSelection(editor) {
    overlayEditor = editor;
    if (editor.classList.contains('mode-markdown')) {
      const ta = editor.querySelector('textarea');
      savedRange = {
        md: true,
        start: ta.selectionStart,
        end: ta.selectionEnd,
        text: ta.value.slice(ta.selectionStart, ta.selectionEnd),
      };
      return;
    }
    const sel = window.getSelection();
    const area = editor.querySelector('.rich-editor-body');
    if (sel && sel.rangeCount && area) {
      const anchor = sel.anchorNode;
      if (anchor && area.contains(anchor)) {
        savedRange = { md: false, range: sel.getRangeAt(0).cloneRange(), text: sel.toString() };
        return;
      }
    }
    // Keep previous in-editor caret if toolbar click already stole focus.
    if (savedRange && !savedRange.md && overlayEditor === editor) return;
    savedRange = { md: false, range: null, text: '' };
  }

  function positionPopover(pop, editor, cmd) {
    const btn = editor.querySelector('.rich-toolbar button[data-cmd="' + cmd + '"]');
    const rect = (btn || editor).getBoundingClientRect();
    pop.hidden = false;
    pop.style.top = (window.scrollY + rect.bottom + 6) + 'px';
    pop.style.left = Math.max(8, Math.min(window.scrollX + rect.left, window.scrollX + window.innerWidth - pop.offsetWidth - 8)) + 'px';
  }

  function openLinkPopover(editor) {
    const o = ensureOverlays();
    if (!o) return;
    // Selection must already be captured on mousedown; refresh if still in editor.
    saveSelection(editor);
    o.mention.hidden = true;
    o.link.hidden = false;
    const text = (savedRange && savedRange.text) || '';
    o.link.querySelector('.rte-link-text').value = text || '';
    o.link.querySelector('.rte-link-url').value = 'https://';
    positionPopover(o.link, editor, 'link');
    o.link.querySelector(text ? '.rte-link-url' : '.rte-link-text').focus();
  }

  function confirmLinkPopover() {
    const o = ensureOverlays();
    if (!o || !overlayEditor) return;
    const text = (o.link.querySelector('.rte-link-text').value || '').trim() || t('Link text');
    const href = (o.link.querySelector('.rte-link-url').value || '').trim() || 'https://';
    const ed = overlayEditor;
    const snap = savedRange;
    o.link.hidden = true;
    savedRange = snap;
    overlayEditor = ed;
    insertLink(ed, text, href);
    clearSavedSelection();
  }

  function insertLink(ed, text, href) {
    if (ed.classList.contains('mode-markdown')) {
      const ta = ed.querySelector('textarea');
      const s = savedRange && savedRange.md ? savedRange.start : ta.selectionStart;
      const e = savedRange && savedRange.md ? savedRange.end : ta.selectionEnd;
      const md = '[' + text + '](' + href + ')';
      ta.value = ta.value.slice(0, s) + md + ta.value.slice(e);
      ta.selectionStart = ta.selectionEnd = s + md.length;
      ta.focus();
      ta.dispatchEvent(new Event('input'));
      return;
    }
    const area = ed.querySelector('.rich-editor-body');
    area.focus();
    const sel = window.getSelection();
    if (savedRange && savedRange.range) {
      try {
        sel.removeAllRanges();
        sel.addRange(savedRange.range);
      } catch (err) { /* ignore */ }
    }
    const hasSel = !!(sel && sel.rangeCount && !sel.getRangeAt(0).collapsed && sel.toString());
    if (hasSel && text === sel.toString()) {
      document.execCommand('createLink', false, href);
    } else {
      document.execCommand('insertHTML', false,
        '<a href="' + escapeAttr(href) + '">' + escapeHtmlText(text) + '</a>');
    }
    syncMarkdownFromRich(ed);
    notifyChange();
    updateToolbarState(ed);
  }

  function openMentionPopover(editor) {
    const o = ensureOverlays();
    if (!o) return;
    saveSelection(editor);
    if (!config.searchUsersUrl) {
      if (editor.classList.contains('mode-markdown')) {
        applyMarkdown(editor.querySelector('textarea'), 'mention');
        editor.querySelector('textarea').dispatchEvent(new Event('input'));
      } else {
        const area = editor.querySelector('.rich-editor-body');
        restoreSavedSelection(area);
        document.execCommand('insertText', false, '[user:]');
        syncMarkdownFromRich(editor);
        notifyChange();
      }
      clearSavedSelection();
      return;
    }
    o.link.hidden = true;
    o.mention.hidden = false;
    o.mention.querySelector('.rte-mention-input').value = (savedRange && savedRange.text) || '';
    o.mention.querySelector('.rte-mention-results').innerHTML = '';
    positionPopover(o.mention, editor, 'mention');
    o.mention.querySelector('.rte-mention-input').focus();
    scheduleMentionSearch(o.mention.querySelector('.rte-mention-input').value);
  }

  function scheduleMentionSearch(q) {
    clearTimeout(mentionTimer);
    mentionTimer = setTimeout(() => searchUsers(q), 200);
  }

  function searchUsers(q) {
    const o = ensureOverlays();
    if (!o || !config.searchUsersUrl) return;
    const list = o.mention.querySelector('.rte-mention-results');
    const username = (q || '').trim();
    if (!username || /\s/.test(username)) {
      list.innerHTML = '<li class="muted">' + t('Type part of a username to search') + '</li>';
      return;
    }
    list.innerHTML = '<li class="muted">' + t('Searching…') + '</li>';
    const url = config.searchUsersUrl + (config.searchUsersUrl.indexOf('?') >= 0 ? '&' : '?') +
      'username=' + encodeURIComponent(username);
    fetch(url, { credentials: 'same-origin' })
      .then(r => r.json())
      .then(body => {
        if (!body || body.status !== 200 || !body.data || !body.data.length) {
          list.innerHTML = '<li class="muted">' + t('No match; you can still insert') + '</li>';
          return;
        }
        list.innerHTML = body.data.map(u =>
          '<li data-username="' + escapeAttr(u.username) + '">@' + escapeHtmlText(u.username) + '</li>'
        ).join('');
      })
      .catch(() => { list.innerHTML = '<li class="muted">' + t('Search failed') + '</li>'; });
  }

  function confirmMention(username) {
    const o = ensureOverlays();
    if (!o || !overlayEditor) return;
    const name = (username || o.mention.querySelector('.rte-mention-input').value || '').trim();
    const ed = overlayEditor;
    const snap = savedRange;
    o.mention.hidden = true;
    savedRange = snap;
    overlayEditor = ed;
    const token = '[user:' + name + ']';
    if (ed.classList.contains('mode-markdown')) {
      const ta = ed.querySelector('textarea');
      const s = savedRange && savedRange.md ? savedRange.start : ta.selectionStart;
      const e = savedRange && savedRange.md ? savedRange.end : ta.selectionEnd;
      ta.value = ta.value.slice(0, s) + token + ta.value.slice(e);
      const caret = s + (name ? token.length : 6);
      ta.selectionStart = ta.selectionEnd = caret;
      ta.focus();
      ta.dispatchEvent(new Event('input'));
      clearSavedSelection();
      return;
    }
    const area = ed.querySelector('.rich-editor-body');
    restoreSavedSelection(area);
    document.execCommand('insertText', false, token);
    syncMarkdownFromRich(ed);
    notifyChange();
    clearSavedSelection();
  }

  function openHelpModal() {
    const o = ensureOverlays();
    if (!o) return;
    o.help.hidden = false;
  }

  /* ---- Markdown conversion (editor display only) ---- */
  function protectMath(str, store) {
    return (str || '')
      .replace(/\$\$([\s\S]+?)\$\$/g, (m) => { store.push(m); return '\u0000M' + (store.length - 1) + '\u0000'; })
      .replace(/\\\[([\s\S]+?)\\\]/g, (m) => { store.push(m); return '\u0000M' + (store.length - 1) + '\u0000'; })
      .replace(/\\\(([\s\S]+?)\\\)/g, (m) => { store.push(m); return '\u0000M' + (store.length - 1) + '\u0000'; })
      .replace(/~([^~\n]+?)~/g, (m) => { store.push(m); return '\u0000M' + (store.length - 1) + '\u0000'; });
  }
  function restoreMath(str, store) {
    return str.replace(/\u0000M(\d+)\u0000/g, (m, i) => store[+i]);
  }

  function imgToMarkup(img) {
    const src = img.getAttribute('src') || '';
    const alt = img.getAttribute('alt') || '';
    let w = img.style.width || img.getAttribute('width') || '';
    let h = img.style.height || img.getAttribute('height') || '';
    // Fall back to parsing the style attribute (some browsers clear style.* after moves).
    if (!w || !h) {
      const raw = img.getAttribute('style') || '';
      if (!w) {
        const m = raw.match(/(?:^|;)\s*width\s*:\s*([^;]+)/i);
        if (m) w = m[1].trim();
      }
      if (!h) {
        const m = raw.match(/(?:^|;)\s*height\s*:\s*([^;]+)/i);
        if (m) h = m[1].trim();
      }
    }
    if (h === 'auto') h = '';
    if (w && /^\d+$/.test(w)) w += 'px';
    if (h && /^\d+$/.test(h)) h += 'px';
    let style = '';
    if (w) style += 'width:' + w + ';';
    if (h) style += 'height:' + h + ';';
    if (!style) return '![' + alt + '](' + src + ')';
    return '<img src="' + src + '"' + (alt ? ' alt="' + alt + '"' : '') + ' style="' + style + '">';
  }

  const INLINE_TAGS = {
    strong: 1, b: 1, em: 1, i: 1, code: 1, mark: 1, a: 1, span: 1, u: 1,
    sub: 1, sup: 1, font: 1, small: 1, big: 1,
  };

  function isHighlightBg(bg) {
    // Only treat our toolbar highlight (and close yellows) as <mark>, not arbitrary spans.
    const s = String(bg || '').toLowerCase().replace(/\s+/g, '');
    return s === '#fde68a' || s === 'rgb(253,230,138)' || s.indexOf('253,230,138') !== -1;
  }

  function serializeNode(n) {
    if (n.nodeType === 3) return n.nodeValue;
    if (n.nodeType !== 1) return '';
    const tag = n.tagName.toLowerCase();
    if (tag === 'br') return '\n';
    if (tag === 'img') return imgToMarkup(n);
    if (tag === 'a') return '[' + serializeInline(n) + '](' + (n.getAttribute('href') || '') + ')';
    if (tag === 'code') return '`' + serializeInline(n) + '`';
    let inner = serializeInline(n);
    if (!inner) return '';
    const st = n.style || {};
    const fw = String(st.fontWeight || '');
    const isBold = tag === 'b' || tag === 'strong' || fw === 'bold' || fw === 'bolder' ||
                   (/^\d+$/.test(fw) && parseInt(fw, 10) >= 600);
    const isItalic = tag === 'i' || tag === 'em' || st.fontStyle === 'italic' || st.fontStyle === 'oblique';
    const bg = String(st.backgroundColor || '');
    const isMark = tag === 'mark' || isHighlightBg(bg);
    const deco = String(st.textDecoration || '') + ' ' + String(st.textDecorationLine || '');
    const isUnder = tag === 'u' || deco.indexOf('underline') !== -1;
    if (isMark) inner = '<mark>' + inner + '</mark>';
    if (isUnder) inner = '<u>' + inner + '</u>';
    if (isItalic && inner.trim()) inner = '*' + inner + '*';
    if (isBold && inner.trim()) inner = '**' + inner + '**';
    return inner;
  }

  function serializeInline(node) {
    let out = '';
    node.childNodes.forEach(n => { out += serializeNode(n); });
    return out;
  }

  function blockAlign(el) {
    const a = el.style && el.style.textAlign;
    return (a === 'center' || a === 'right' || a === 'left') ? a : '';
  }

  /** Serialize children of an align <div>: keep heading/p tags; route imgs via imgToMarkup. */
  function serializeAlignInner(el) {
    let inner = '';
    Array.prototype.forEach.call(el.childNodes, (child) => {
      if (child.nodeType === 3) {
        inner += child.nodeValue;
        return;
      }
      if (child.nodeType !== 1) return;
      const ct = child.tagName.toLowerCase();
      if (/^h[1-6]$/.test(ct) || ct === 'p' || ct === 'blockquote') {
        inner += '<' + ct + '>' + serializeInline(child) + '</' + ct + '>';
        return;
      }
      if (ct === 'img') {
        inner += imgToMarkup(child);
        return;
      }
      if (ct === 'br') {
        inner += '<br>';
        return;
      }
      // Nested align / misc: fall back to inline serialization of this node.
      inner += serializeNode(child);
    });
    return inner;
  }

  function serializeBlocks(nodes) {
    let out = '';
    let inlineBuf = '';

    function flushInline() {
      const t = inlineBuf.replace(/[ \t]+\n/g, '\n').replace(/\n[ \t]+/g, '\n').trim();
      inlineBuf = '';
      if (t) out += t + '\n\n';
    }

    nodes.forEach(n => {
      if (n.nodeType === 3) {
        inlineBuf += n.nodeValue;
        return;
      }
      if (n.nodeType !== 1) return;
      const tag = n.tagName.toLowerCase();

      // Soft break inside a paragraph run
      if (tag === 'br') {
        inlineBuf += '\n';
        return;
      }
      // Inline elements must NOT start a new markdown line (that was shredding prose).
      if (INLINE_TAGS[tag] || tag === 'img') {
        inlineBuf += serializeNode(n);
        return;
      }

      flushInline();

      const align = blockAlign(n);
      // Preserve heading/paragraph tags inside align wrappers; serialize children so
      // resized <img style="width/height"> survives (raw innerHTML is brittle).
      if (align && /^h[1-6]$/.test(tag)) {
        out += '<div style="text-align:' + align + '"><' + tag + '>' +
          serializeInline(n) + '</' + tag + '></div>\n\n';
        return;
      }
      if (align && (tag === 'p' || tag === 'blockquote')) {
        out += '<div style="text-align:' + align + '"><' + tag + '>' +
          serializeInline(n) + '</' + tag + '></div>\n\n';
        return;
      }
      if (align && tag === 'div') {
        out += '<div style="text-align:' + align + '">' + serializeAlignInner(n) + '</div>\n\n';
        return;
      }
      if (tag === 'div' && n.classList && n.classList.contains('h-scrollable-table')) {
        const t = n.querySelector('table');
        const md = tableToMd(t);
        if (md) out += md + '\n\n';
        return;
      }
      if (tag === 'table') {
        const md = tableToMd(n);
        if (md) out += md + '\n\n';
        return;
      }
      switch (tag) {
        // Match AwesomeRenderer header demotion: #→h3, ##→h4, ###→h5
        case 'h1': case 'h2': case 'h3':
          out += '# ' + serializeInline(n).trim() + '\n\n'; break;
        case 'h4':
          out += '## ' + serializeInline(n).trim() + '\n\n'; break;
        case 'h5': case 'h6':
          out += '### ' + serializeInline(n).trim() + '\n\n'; break;
        case 'ul': case 'ol':
          Array.prototype.forEach.call(n.children, (li, i) => {
            if (li.tagName && li.tagName.toLowerCase() === 'li')
              out += (tag === 'ol' ? (i + 1) + '. ' : '- ') + serializeInline(li).trim() + '\n';
          });
          out += '\n';
          break;
        case 'blockquote':
          out += '> ' + serializeInline(n).trim().replace(/\n/g, '\n> ') + '\n\n';
          break;
        case 'pre': {
          const codeEl = n.querySelector && n.querySelector('code');
          const raw = ((codeEl ? codeEl.textContent : n.textContent) || '')
            .replace(/^\n+/, '').replace(/\n+$/, '');
          out += '```\n' + raw + '\n```\n\n';
          break;
        }
        case 'hr': out += '---\n\n'; break;
        case 'div': case 'p': {
          const inner = serializeInline(n).trim();
          if (inner) out += inner + '\n\n';
          break;
        }
        default:
          out += serializeNode(n) + '\n\n';
      }
    });
    flushInline();
    return out;
  }

  function htmlToMd(html) {
    const root = document.createElement('div');
    root.innerHTML = html || '';
    return serializeBlocks(Array.prototype.slice.call(root.childNodes))
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  function escapeHtmlText(t) {
    return String(t)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function formatInlineMarkdown(s) {
    return s
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/==(.+?)==/g, '<mark>$1</mark>')
      .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
  }

  function splitPipeRow(line) {
    let s = String(line || '').trim();
    if (s.charAt(0) === '|') s = s.slice(1);
    if (s.charAt(s.length - 1) === '|') s = s.slice(0, -1);
    return s.split('|').map(c => c.trim());
  }

  function isTableSepRow(line) {
    const cells = splitPipeRow(line);
    return cells.length > 0 && cells.every(c => /^:?-+:?$/.test(c));
  }

  function alignFromSep(cell) {
    const left = cell.charAt(0) === ':';
    const right = cell.charAt(cell.length - 1) === ':';
    if (left && right) return 'center';
    if (right) return 'right';
    if (left) return 'left';
    return '';
  }

  /** GFM pipe table → same wrapper/classes as AwesomeRenderer.table */
  function renderPipeTable(lines) {
    const header = splitPipeRow(lines[0]);
    const seps = splitPipeRow(lines[1]);
    const aligns = seps.map(alignFromSep);
    function cellHtml(text, tag, i) {
      const a = aligns[i] || '';
      const style = a ? ' style="text-align:' + a + '"' : '';
      // Empty cells need <br> in contenteditable or the caret paints between rows.
      const inner = formatInlineMarkdown(escapeHtmlText(text || ''));
      return '<' + tag + style + '>' + (inner || '<br>') + '</' + tag + '>';
    }
    let html = '<div class="h-scrollable-table"><table class="table"><thead><tr>';
    header.forEach((c, i) => { html += cellHtml(c, 'th', i); });
    html += '</tr></thead><tbody>';
    lines.slice(2).forEach((line) => {
      const cells = splitPipeRow(line);
      html += '<tr>';
      for (let i = 0; i < header.length; i++) html += cellHtml(cells[i], 'td', i);
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    return html;
  }

  function isPipeTableRow(line) {
    return /^\s*\|.+\|\s*$/.test(line);
  }

  /**
   * Collapse blank lines inside GFM pipe tables.
   * Pasted tables (Word/web) often look like:
   *   | A | B |
   *   <blank>
   *   |---|---|
   *   <blank>
   *   | 1 | 2 |
   * which mistune/GFM will NOT treat as a table.
   */
  function normalizePipeTables(md) {
    const lines = String(md || '').split('\n');
    const out = [];
    let i = 0;
    while (i < lines.length) {
      if (!isPipeTableRow(lines[i])) {
        out.push(lines[i]);
        i++;
        continue;
      }
      let j = i + 1;
      while (j < lines.length && /^\s*$/.test(lines[j])) j++;
      if (j >= lines.length || !isTableSepRow(lines[j])) {
        out.push(lines[i]);
        i++;
        continue;
      }
      const tableRows = [lines[i], lines[j]];
      j++;
      while (j < lines.length) {
        if (/^\s*$/.test(lines[j])) {
          let k = j + 1;
          while (k < lines.length && /^\s*$/.test(lines[k])) k++;
          if (k < lines.length && isPipeTableRow(lines[k]) && !isTableSepRow(lines[k])) {
            j = k;
            tableRows.push(lines[j]);
            j++;
            continue;
          }
          break;
        }
        if (isPipeTableRow(lines[j])) {
          tableRows.push(lines[j]);
          j++;
          continue;
        }
        break;
      }
      for (let t = 0; t < tableRows.length; t++) out.push(tableRows[t]);
      i = j;
    }
    return out.join('\n');
  }

  function parkPipeTables(s, blocks) {
    s = normalizePipeTables(s);
    const lines = String(s || '').split('\n');
    const out = [];
    let i = 0;
    while (i < lines.length) {
      if (i + 1 < lines.length &&
          isPipeTableRow(lines[i]) &&
          isTableSepRow(lines[i + 1])) {
        const start = i;
        i += 2;
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) i++;
        blocks.push(renderPipeTable(lines.slice(start, i)));
        out.push('\u0000B' + (blocks.length - 1) + '\u0000');
        continue;
      }
      out.push(lines[i]);
      i++;
    }
    return out.join('\n');
  }

  function tableToMd(table) {
    if (!table) return '';
    const headRow = table.querySelector('thead tr') || table.querySelector('tr');
    if (!headRow) return '';
    const headerCells = Array.prototype.filter.call(headRow.children, (c) =>
      c.tagName === 'TH' || c.tagName === 'TD');
    if (!headerCells.length) return '';
    const cellText = (c) =>
      serializeInline(c)
        .replace(/<br\s*\/?>/gi, '\n')
        .replace(/\|/g, '\\|')
        .replace(/\n+/g, ' ')
        .replace(/\u00a0/g, ' ')
        .trim();
    const head = headerCells.map(cellText);
    const lines = [
      '| ' + head.join(' | ') + ' |',
      '| ' + head.map(() => '---').join(' | ') + ' |',
    ];
    const bodyRows = table.querySelectorAll('tbody tr');
    const rows = bodyRows.length
      ? bodyRows
      : Array.prototype.filter.call(table.querySelectorAll('tr'), (tr, idx) => idx > 0);
    Array.prototype.forEach.call(rows, (tr) => {
      if (tr === headRow) return;
      const cells = Array.prototype.filter.call(tr.children, (c) =>
        c.tagName === 'TH' || c.tagName === 'TD').map(cellText);
      while (cells.length < head.length) cells.push('');
      lines.push('| ' + cells.slice(0, head.length).join(' | ') + ' |');
    });
    return lines.join('\n');
  }

  /* ---- Markdown → HTML：仅供编辑器渲染；公式保持字面量，原始 <img>/对齐块透传 ---- */
  function mdToHtml(md) {
    if (!md) return '';
    const raw = [], math = [], blocks = [], imgs = [];
    let s = (md || '')
      // Block-level raw HTML (align wrappers / tables) — parked outside <p>.
      .replace(/<div\b[^>]*class="[^"]*\bh-scrollable-table\b[^"]*"[^>]*>[\s\S]*?<\/div>/gi, (m) => {
        raw.push(m); return '\u0000K' + (raw.length - 1) + '\u0000';
      })
      .replace(/<div style="text-align:[^"]*">[\s\S]*?<\/div>/gi, (m) => {
        raw.push(m); return '\u0000K' + (raw.length - 1) + '\u0000';
      })
      .replace(/<table\b[\s\S]*?<\/table>/gi, (m) => {
        raw.push('<div class="h-scrollable-table">' + m + '</div>');
        return '\u0000K' + (raw.length - 1) + '\u0000';
      })
      .replace(/<img\b[^>]*>/gi, (m) => {
        raw.push(m); return '\u0000K' + (raw.length - 1) + '\u0000';
      })
      // Inline mark/u spans stay inside paragraphs.
      .replace(/<(mark|u)\b[^>]*>[\s\S]*?<\/\1>/gi, (m) => {
        raw.push(m); return '\u0000R' + (raw.length - 1) + '\u0000';
      });
    s = protectMath(s, math);

    // Fenced code before escaping.
    s = s.replace(/```([\s\S]*?)```/g, (m, c) => {
      const body = escapeHtmlText(String(c).replace(/^\n/, '').replace(/\n$/, ''));
      blocks.push('<pre><code>' + body + '</code></pre>');
      return '\u0000B' + (blocks.length - 1) + '\u0000';
    });

    // GFM pipe tables (same shape as server AwesomeRenderer.table).
    s = parkPipeTables(s, blocks);

    // Blockquotes before escaping `>` (otherwise they become literal arrows).
    s = s.replace(/(^|\n)((?:>[ \t]?.*(?:\n|$))+)/g, (m, lead, block) => {
      const inner = block.replace(/\n$/, '').split('\n').map(l => l.replace(/^>[ \t]?/, '')).join('\n');
      const html = '<blockquote>' + formatInlineMarkdown(escapeHtmlText(inner)).replace(/\n/g, '<br>') + '</blockquote>';
      blocks.push(html);
      return lead + '\u0000B' + (blocks.length - 1) + '\u0000';
    });

    const links = [];
    s = s.replace(/!\[[^\]]*\]\(([^)]+)\)/g, (m, url) => {
      imgs.push('<img src="' + escapeAttr(url) + '" style="max-width:100%;border-radius:4px;">');
      return '\u0000I' + (imgs.length - 1) + '\u0000';
    })
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, text, url) => {
        links.push({ text: text, url: url });
        return '\u0000L' + (links.length - 1) + '\u0000';
      })
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/^(?:-{3,}|\*{3,}|_{3,})\s*$/gm, '\u0000H\u0000')
      // Same +2 demotion as judge.jinja2.markdown.AwesomeRenderer.header
      .replace(/^### (.+)$/gm, '<h5>$1</h5>')
      .replace(/^## (.+)$/gm, '<h4>$1</h4>')
      .replace(/^# (.+)$/gm, '<h3>$1</h3>');
    s = formatInlineMarkdown(s);
    s = s.replace(/\u0000L(\d+)\u0000/g, (m, i) => {
      const L = links[+i];
      return '<a href="' + escapeAttr(L.url) + '">' +
        formatInlineMarkdown(escapeHtmlText(L.text)) + '</a>';
    });
    s = s.replace(/(?:^|\n)((?:\d+\.[ \t].*(?:\n|$))+)/g, (m) =>
      '\n<ol>' + m.trim().split('\n').map(l => '<li>' + l.replace(/^\d+\.[ \t]/, '') + '</li>').join('') + '</ol>\n');
    s = s.replace(/(?:^|\n)((?:[-*+][ \t].*(?:\n|$))+)/g, (m) =>
      '\n<ul>' + m.trim().split('\n').map(l => '<li>' + l.replace(/^[-*+][ \t]/, '') + '</li>').join('') + '</ul>\n');

    // Turn remaining prose into real <p> blocks instead of a flat text+<br> soup.
    // Flat soup made htmlToMd treat every <code>/<strong>/<em> as its own line.
    s = flowToParagraphHtml(s);
    s = s.replace(/\u0000H\u0000/g, '<hr contenteditable="false">');
    s = s.replace(/\u0000I(\d+)\u0000/g, (m, i) => imgs[+i]);
    s = s.replace(/\u0000B(\d+)\u0000/g, (m, i) => blocks[+i]);
    s = restoreMath(s, math);
    s = s.replace(/\u0000[KR](\d+)\u0000/g, (m, i) => raw[+i]);
    return s;
  }

  /** Wrap loose prose in <p>; keep block tags intact; single \\n → <br> inside a paragraph. */
  function flowToParagraphHtml(s) {
    const held = [];
    // Park block-level HTML (and existing placeholders) so we can paragraphize the rest.
    // Park block constructs + block raw (K). Leave inline math/mark (R/M) inside <p>.
    let t = String(s || '').replace(
      /<div\b[^>]*class="[^"]*\bh-scrollable-table\b[^"]*"[^>]*>[\s\S]*?<\/div>|<table\b[\s\S]*?<\/table>|<(h[1-6]|ul|ol|pre|blockquote)\b[\s\S]*?<\/\1>|<hr\b[^>]*>|\u0000[BIHK]\d*\u0000/gi,
      (m) => {
        held.push(m);
        return '\u0000X' + (held.length - 1) + '\u0000';
      });

    const pieces = t.split(/\n{2,}/);
    let html = '';
    pieces.forEach((piece) => {
      const chunk = piece.replace(/^\n+|\n+$/g, '');
      if (!chunk) return;
      // Pure parked block(s)
      if (/^(?:\u0000X\d+\u0000)+$/.test(chunk.replace(/\n/g, ''))) {
        html += chunk.replace(/\u0000X(\d+)\u0000/g, (_, i) => held[+i]).replace(/\n/g, '');
        return;
      }
      // Mixed: split around parked blocks
      const parts = chunk.split(/(\u0000X\d+\u0000)/);
      parts.forEach((part) => {
        if (!part) return;
        const m = part.match(/^\u0000X(\d+)\u0000$/);
        if (m) {
          html += held[+m[1]];
          return;
        }
        const prose = part.replace(/^\n+|\n+$/g, '');
        if (!prose) return;
        html += '<p>' + prose.replace(/\n/g, '<br>') + '</p>';
      });
    });
    return html;
  }

  const RichEditor = {
    toolbar: RTE_TOOLBAR,
    configure(opts) {
      if (!opts) return;
      if (opts.imageUploadUrl != null) config.imageUploadUrl = opts.imageUploadUrl;
      if (opts.searchUsersUrl != null) config.searchUsersUrl = opts.searchUsersUrl;
      if (opts.csrfToken != null) config.csrfToken = opts.csrfToken;
      if (opts.onChange != null) config.onChange = opts.onChange;
      if (opts.imageInputId != null) config.imageInputId = opts.imageInputId;
      if (opts.messages) Object.assign(config.messages, opts.messages);
      ensureSelectionListener();
    },
    html: richEditorTpl,
    mount(root) {
      ensureSelectionListener();
      ensureOverlays();
      mountRichEditors(root);
      initEditors(root);
    },
    init(root) {
      ensureSelectionListener();
      ensureOverlays();
      initEditors(root);
    },
    getMarkdown: getMd,
    getHtml: editorHtml,
    setMarkdown: setMarkdown,
    htmlToMd: htmlToMd,
    mdToHtml: mdToHtml,
    onImagePicked: onImagePicked,
  };

  return RichEditor;
}));
