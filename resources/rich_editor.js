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

  // Icons aligned with Martor (Semantic → Font Awesome 4 equivalents).
  // Headings: site markdown demotes by +2 (# → h3), so labels match preview.
  const RTE_BUTTONS = {
    heading: '<select class="rte-heading" title="文字等级"><option value="p">正文</option><option value="h3">标题1</option><option value="h4">标题2</option><option value="h5">标题3</option></select>',
    sep: '<span class="sep"></span>',
    bold: '<button type="button" data-cmd="bold" title="加粗 (Ctrl+B)"><i class="fa fa-bold"></i></button>',
    italic: '<button type="button" data-cmd="italic" title="斜体 (Ctrl+I)"><i class="fa fa-italic"></i></button>',
    highlight: '<button type="button" data-cmd="highlight" class="hl-btn" title="高亮"><i class="fa fa-paint-brush"></i></button>',
    code: '<button type="button" data-cmd="code" title="代码"><i class="fa fa-code"></i></button>',
    math: '<button type="button" data-cmd="math" title="公式 (Ctrl+Alt+D)"><i class="fa fa-superscript"></i></button>',
    quote: '<button type="button" data-cmd="quote" title="引用 (Ctrl+Q)"><i class="fa fa-quote-left"></i></button>',
    ul: '<button type="button" data-cmd="ul" title="无序列表 (Ctrl+U)"><i class="fa fa-list-ul"></i></button>',
    ol: '<button type="button" data-cmd="ol" title="有序列表 (Ctrl+Shift+O)"><i class="fa fa-list-ol"></i></button>',
    link: '<button type="button" data-cmd="link" title="链接 (Ctrl+L)"><i class="fa fa-link"></i></button>',
    image: '<button type="button" data-cmd="image" title="图片"><i class="fa fa-image"></i></button>',
    mention: '<button type="button" data-cmd="mention" title="提及用户 (Ctrl+M)"><i class="fa fa-at"></i></button>',
    help: '<button type="button" data-cmd="help" title="Markdown 帮助"><i class="fa fa-question-circle"></i></button>',
    alignLeft: '<button type="button" data-cmd="alignLeft" title="左对齐"><i class="fa fa-align-left"></i></button>',
    alignCenter: '<button type="button" data-cmd="alignCenter" title="居中"><i class="fa fa-align-center"></i></button>',
    alignRight: '<button type="button" data-cmd="alignRight" title="右对齐"><i class="fa fa-align-right"></i></button>',
    blank: '<button type="button" data-cmd="blank" class="blank-insert" title="插入填空位" style="display:none;"><i class="fa fa-square-o"></i></button>',
  };
  // Groups: 等级 | 加粗/高亮 | 代码/公式/引用 | 列表 | 对齐 | 链接等
  const RTE_TOOLBAR = [
    'heading', 'sep',
    'bold', 'highlight', 'italic', 'sep',
    'code', 'math', 'quote', 'sep',
    'ul', 'ol', 'sep',
    'alignLeft', 'alignCenter', 'alignRight', 'sep',
    'link', 'image', 'mention', 'help', 'blank',
  ];

  function richEditorTpl(name, opts) {
    opts = opts || {};
    const buttons = RTE_TOOLBAR.map(k => RTE_BUTTONS[k] || '').join('');
    const placeholder = opts.placeholder || '请输入内容，支持 $$公式$$…';
    const mdPlaceholder = opts.mdPlaceholder || '支持 Markdown 与 $$公式$$…';
    const compact = opts.compact ? ' compact' : '';
    return `<div class="rich-editor mode-rich${compact}" data-editor="${name}">
      <div class="rich-editor-toolbar-row">
        <div class="editor-mode-tabs">
          <button type="button" class="mode-btn active" data-mode="rich">富文本</button>
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
    parts.ta.value = htmlToMd(parts.rich.innerHTML);
  }

  /** Rebuild contenteditable projection from Markdown textarea. */
  function projectRichFromMarkdown(editor) {
    const parts = editorParts(editor);
    if (!parts.rich || !parts.ta) return;
    parts.rich.innerHTML = mdToHtml(parts.ta.value);
  }

  function getMd(name) {
    const ed = findEditor(name);
    if (!ed) return '';
    const ta = ed.querySelector('.rich-editor-markdown textarea');
    if (!ta) return '';
    if (!ed.classList.contains('mode-markdown')) syncMarkdownFromRich(ed);
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
    ta.value = md || '';
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
    const floor = editor.classList.contains('compact') ? 80 : 200;
    // Shrink as well as grow: re-measure natural content height against floor.
    const h = Math.max(measureContentHeight(rich, floor), measureContentHeight(ta, floor), floor);
    rich.style.minHeight = h + 'px';
    ta.style.minHeight = h + 'px';
  }

  // Block-level cmds reproject from MD so rich view matches source (lists/quotes/fences).
  // Inline cmds (bold/italic/highlight/inline code) only sync, to keep the caret.
  const BLOCK_CMDS = {
    heading: 1, h3: 1, h4: 1, h5: 1, p: 1,
    ul: 1, ol: 1, quote: 1,
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
        });
        rich.addEventListener('mouseup', () => {
          saveSelection(editor);
          updateToolbarState(editor);
        });
        rich.addEventListener('keydown', (e) => handleEditorShortcut(editor, e));
      }
      if (ta) {
        ta.addEventListener('input', () => {
          syncPaneHeight(editor);
          notifyChange();
        });
        ta.addEventListener('mouseup', () => {
          saveSelection(editor);
          syncPaneHeight(editor);
        });
        ta.addEventListener('keyup', () => saveSelection(editor));
        ta.addEventListener('keydown', (e) => handleEditorShortcut(editor, e));
      }
      editor.querySelectorAll('.rich-toolbar button[data-cmd]').forEach(btn => {
        // Preserve caret before toolbar steals focus (link / mention / align / image).
        btn.addEventListener('mousedown', (e) => {
          const cmd = btn.dataset.cmd;
          if (cmd === 'link' || cmd === 'mention' || cmd === 'image' ||
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
      if (area) updateToolbarState(area.closest('.rich-editor'));
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

  function applyFormat(editor, cmd) {
    if (cmd === 'image') { pickImage(editor); return; }
    if (cmd === 'help') { openHelpModal(); return; }
    if (cmd === 'link') { openLinkPopover(editor); return; }
    if (cmd === 'mention') { openMentionPopover(editor); return; }

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
      case 'bold': wrap('**', '**', '加粗'); break;
      case 'italic': wrap('*', '*', '斜体'); break;
      case 'highlight': wrap('<mark>', '</mark>', '高亮'); break;
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
        <div class="rte-popover-title">插入链接</div>
        <label class="rte-field"><span>文字</span><input type="text" class="rte-link-text" placeholder="链接文字"></label>
        <label class="rte-field"><span>地址</span><input type="url" class="rte-link-url" placeholder="https://"></label>
        <div class="rte-popover-actions">
          <button type="button" class="rte-btn-cancel">取消</button>
          <button type="button" class="rte-btn-ok">插入</button>
        </div>
      </div>
      <div id="rte-mention-popover" class="rte-popover" hidden>
        <div class="rte-popover-title">提及用户</div>
        <label class="rte-field"><span>用户名</span><input type="text" class="rte-mention-input" placeholder="搜索用户…" autocomplete="off"></label>
        <ul class="rte-mention-results"></ul>
        <div class="rte-popover-actions">
          <button type="button" class="rte-btn-cancel">取消</button>
          <button type="button" class="rte-btn-ok">插入</button>
        </div>
      </div>
      <div id="rte-help-modal" class="rte-help-modal" hidden>
        <div class="rte-help-dialog">
          <div class="rte-help-head">
            <h2><i class="fa fa-question-circle"></i> Markdown 帮助</h2>
            <button type="button" class="rte-help-close" aria-label="关闭">×</button>
          </div>
          <div class="rte-help-body">
            <p>本站题目描述使用 Markdown。完整语法见 <a href="https://commonmark.org/help/" target="_blank" rel="noopener">CommonMark</a>。</p>
            <table class="rte-help-table">
              <thead><tr><th>写法</th><th>快捷键</th><th>效果</th></tr></thead>
              <tbody>
                <tr><td><code>**粗体**</code></td><td>Ctrl+B</td><td><strong>粗体</strong></td></tr>
                <tr><td><code>*斜体*</code></td><td>Ctrl+I</td><td><em>斜体</em></td></tr>
                <tr><td><code># 大标题</code></td><td>Ctrl+Alt+1</td><td>一级标题（预览为较大标题）</td></tr>
                <tr><td><code>## 中标题</code></td><td>Ctrl+Alt+2</td><td>二级标题</td></tr>
                <tr><td><code>### 小标题</code></td><td>Ctrl+Alt+3</td><td>三级标题</td></tr>
                <tr><td><code>[文字](https://)</code></td><td>Ctrl+L</td><td>链接</td></tr>
                <tr><td><code>![说明](url)</code></td><td>—</td><td>图片</td></tr>
                <tr><td><code>&gt; 引用</code></td><td>Ctrl+Q</td><td>引用块</td></tr>
                <tr><td><code>- 列表</code> / <code>1. 列表</code></td><td>Ctrl+U / Ctrl+Shift+O</td><td>列表</td></tr>
                <tr><td><code>\`code\`</code> / 围栏代码块</td><td>—</td><td>代码</td></tr>
                <tr><td><code>$$ a^2+b^2 $$</code></td><td>Ctrl+Alt+D</td><td>公式</td></tr>
                <tr><td><code>[user:用户名]</code></td><td>Ctrl+M</td><td>提及用户</td></tr>
                <tr><td><code>&lt;mark&gt;高亮&lt;/mark&gt;</code></td><td>—</td><td>高亮</td></tr>
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
    const text = (o.link.querySelector('.rte-link-text').value || '').trim() || '链接文字';
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
      list.innerHTML = '<li class="muted">输入用户名片段以搜索</li>';
      return;
    }
    list.innerHTML = '<li class="muted">搜索中…</li>';
    const url = config.searchUsersUrl + (config.searchUsersUrl.indexOf('?') >= 0 ? '&' : '?') +
      'username=' + encodeURIComponent(username);
    fetch(url, { credentials: 'same-origin' })
      .then(r => r.json())
      .then(body => {
        if (!body || body.status !== 200 || !body.data || !body.data.length) {
          list.innerHTML = '<li class="muted">无匹配用户，可直接插入</li>';
          return;
        }
        list.innerHTML = body.data.map(u =>
          '<li data-username="' + escapeAttr(u.username) + '">@' + escapeHtmlText(u.username) + '</li>'
        ).join('');
      })
      .catch(() => { list.innerHTML = '<li class="muted">搜索失败</li>'; });
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
        out += '<div style="text-align:' + align + '">' + serializeInline(n) + '</div>\n\n';
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

  /* ---- Markdown → HTML：仅供编辑器渲染；公式保持字面量，原始 <img>/对齐块透传 ---- */
  function mdToHtml(md) {
    if (!md) return '';
    const raw = [], math = [], blocks = [], imgs = [];
    let s = (md || '')
      // Block-level raw HTML (align wrappers) — parked outside <p> during flow wrap.
      .replace(/<div style="text-align:[^"]*">[\s\S]*?<\/div>/gi, (m) => {
        raw.push(m); return '\u0000K' + (raw.length - 1) + '\u0000';
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

    // Blockquotes before escaping `>` (otherwise they become literal arrows).
    s = s.replace(/(^|\n)((?:>[ \t]?.*(?:\n|$))+)/g, (m, lead, block) => {
      const inner = block.replace(/\n$/, '').split('\n').map(l => l.replace(/^>[ \t]?/, '')).join('\n');
      const html = '<blockquote>' + formatInlineMarkdown(escapeHtmlText(inner)).replace(/\n/g, '<br>') + '</blockquote>';
      blocks.push(html);
      return lead + '\u0000B' + (blocks.length - 1) + '\u0000';
    });

    const links = [];
    s = s.replace(/!\[[^\]]*\]\(([^)]+)\)/g, (m, url) => {
      imgs.push('<img src="' + url + '" style="max-width:100%;border-radius:4px;">');
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
      /<(h[1-6]|ul|ol|pre|blockquote)\b[\s\S]*?<\/\1>|<hr\b[^>]*>|\u0000[BIHK]\d*\u0000/gi,
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
