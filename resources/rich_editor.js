/**
 * Reusable rich-text / Markdown editor (contenteditable ↔ Markdown).
 *
 * Preferred editor for new authoring UIs (create_v2 and future pages).
 * Martor (Ace + single /widgets/preview/*) remains for legacy forms/admin;
 * both share the server markdown pipeline — create_v2 also uses the batch
 * endpoint via MarkdownPreview.renderKeyed.
 *
 * Browser: window.RichEditor
 * Node (tests): module.exports = RichEditor
 *
 * API:
 *   RichEditor.configure({ imageUploadUrl, csrfToken, onChange, messages })
 *   RichEditor.html(name, opts) / mount(root) / init(root)
 *   RichEditor.getMarkdown(name) / getHtml(name) / setMarkdown(name, md)
 *   RichEditor.htmlToMd(html) / mdToHtml(md)   // editor-display only, not authoritative
 *   RichEditor.onImagePicked(event)            // wire to <input type=file>
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

  const RTE_BUTTONS = {
    heading: '<select class="rte-heading" title="标题大小"><option value="p">正文</option><option value="h1">标题 H1</option><option value="h2">标题 H2</option><option value="h3">标题 H3</option></select>',
    sep: '<span class="sep"></span>',
    bold: '<button type="button" data-cmd="bold" title="加粗"><i class="fa fa-bold"></i></button>',
    italic: '<button type="button" data-cmd="italic" title="斜体"><i class="fa fa-italic"></i></button>',
    highlight: '<button type="button" data-cmd="highlight" class="hl-btn" title="高亮"><i class="fa fa-paint-brush"></i></button>',
    ul: '<button type="button" data-cmd="ul" title="无序列表"><i class="fa fa-list-ul"></i></button>',
    ol: '<button type="button" data-cmd="ol" title="有序列表"><i class="fa fa-list-ol"></i></button>',
    quote: '<button type="button" data-cmd="quote" title="引用"><i class="fa fa-quote-right"></i></button>',
    code: '<button type="button" data-cmd="code" title="代码"><i class="fa fa-code"></i></button>',
    alignLeft: '<button type="button" data-cmd="alignLeft" title="左对齐"><i class="fa fa-align-left"></i></button>',
    alignCenter: '<button type="button" data-cmd="alignCenter" title="居中"><i class="fa fa-align-center"></i></button>',
    alignRight: '<button type="button" data-cmd="alignRight" title="右对齐"><i class="fa fa-align-right"></i></button>',
    image: '<button type="button" data-cmd="image" title="图片"><i class="fa fa-picture-o"></i></button>',
    math: '<button type="button" data-cmd="math" title="插入公式"><i class="fa fa-superscript"></i></button>',
    blank: '<button type="button" data-cmd="blank" class="blank-insert" title="插入填空位" style="display:none;"><i class="fa fa-square-o"></i></button>',
  };
  const RTE_TOOLBAR = ['heading', 'sep', 'bold', 'italic', 'highlight', 'code', 'sep',
    'ul', 'ol', 'quote', 'sep', 'alignLeft', 'alignCenter', 'alignRight', 'sep', 'image', 'math', 'blank'];

  function richEditorTpl(name, opts) {
    opts = opts || {};
    const buttons = RTE_TOOLBAR.map(k => RTE_BUTTONS[k] || '').join('');
    const placeholder = opts.placeholder || '请输入内容，支持 ~公式~…';
    const mdPlaceholder = opts.mdPlaceholder || '支持 Markdown 与 ~公式~…';
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

  function getMd(name) {
    const ed = findEditor(name);
    if (!ed) return '';
    return ed.classList.contains('mode-markdown')
      ? ed.querySelector('textarea').value
      : htmlToMd(ed.querySelector('.rich-editor-body').innerHTML);
  }

  function editorHtml(name) {
    const ed = findEditor(name);
    if (!ed) return '';
    if (ed.classList.contains('mode-markdown')) return mdToHtml(ed.querySelector('textarea').value);
    return ed.querySelector('.rich-editor-body').innerHTML || '';
  }

  function setMarkdown(name, md) {
    const ed = findEditor(name);
    if (!ed) return;
    const body = ed.querySelector('.rich-editor-body');
    const ta = ed.querySelector('.rich-editor-markdown textarea');
    if (body) body.innerHTML = mdToHtml(md || '');
    if (ta) ta.value = md || '';
  }

  function syncPaneHeight(editor) {
    const rich = editor.querySelector('.rich-editor-body');
    const ta = editor.querySelector('.rich-editor-markdown textarea');
    if (!rich || !ta) return;
    const floor = editor.classList.contains('compact') ? 80 : 200;
    const h = Math.max(rich.offsetHeight || 0, ta.offsetHeight || 0, floor);
    rich.style.minHeight = h + 'px';
    ta.style.minHeight = h + 'px';
  }

  function initEditors(root) {
    const scope = root || document;
    scope.querySelectorAll('.rich-editor').forEach(editor => {
      if (editor.dataset.bound) return;
      editor.dataset.bound = '1';
      const rich = editor.querySelector('.rich-editor-body');
      const ta = editor.querySelector('.rich-editor-markdown textarea');
      editor.querySelectorAll('.mode-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const md = btn.dataset.mode === 'markdown';
          syncPaneHeight(editor);
          if (md) ta.value = htmlToMd(rich.innerHTML);
          else rich.innerHTML = mdToHtml(ta.value);
          editor.classList.toggle('mode-markdown', md);
          editor.classList.toggle('mode-rich', !md);
          editor.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b === btn));
          syncPaneHeight(editor);
          notifyChange();
        });
      });
      if (rich) {
        rich.addEventListener('input', () => {
          ta.value = htmlToMd(rich.innerHTML);
          notifyChange();
        });
        rich.addEventListener('keyup', () => updateToolbarState(editor));
        rich.addEventListener('mouseup', () => updateToolbarState(editor));
      }
      if (ta) {
        ta.addEventListener('input', notifyChange);
        ta.addEventListener('mouseup', () => syncPaneHeight(editor));
      }
      editor.querySelectorAll('.rich-toolbar button[data-cmd]').forEach(btn => {
        btn.addEventListener('click', () => applyFormat(editor, btn.dataset.cmd, btn));
      });
      const headSel = editor.querySelector('.rte-heading');
      if (headSel) headSel.addEventListener('change', () => applyFormat(editor, headSel.value));
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
      }
      btn.classList.toggle('active', !!active);
    });
    const headSel = editor.querySelector('.rte-heading');
    if (headSel) {
      const block = selectionNode(area) && selectionNode(area).closest('h1,h2,h3,p,blockquote');
      if (block) {
        const map = { H1: 'h1', H2: 'h2', H3: 'h3', P: 'p', BLOCKQUOTE: 'quote' };
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

  function applyFormat(editor, cmd) {
    if (cmd === 'image') { pickImage(editor); return; }
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
    switch (cmd) {
      case 'bold': document.execCommand('bold'); break;
      case 'italic': document.execCommand('italic'); break;
      case 'highlight':
        if (selectionInTag(area, 'mark')) unwrapTagFromSelection(area, 'mark');
        else wrapInline('mark');
        break;
      case 'heading': case 'h3': document.execCommand('formatBlock', false, 'h3'); break;
      case 'h1': document.execCommand('formatBlock', false, 'h1'); break;
      case 'h2': document.execCommand('formatBlock', false, 'h2'); break;
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
        } else if (selectionInTag(area, 'code') && !selectionInTag(area, 'pre')) {
          unwrapTagFromSelection(area, 'code');
        } else {
          const sel = window.getSelection();
          const text = (sel && sel.rangeCount) ? sel.toString() : '';
          if (!text || text.indexOf('\n') !== -1) {
            const body = (text || 'code').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            document.execCommand('insertHTML', false, '<pre><code>' + body + '</code></pre><p><br></p>');
          } else {
            wrapInline('code');
          }
        }
        break;
      case 'alignLeft': document.execCommand('justifyLeft'); break;
      case 'alignCenter': document.execCommand('justifyCenter'); break;
      case 'alignRight': document.execCommand('justifyRight'); break;
      case 'math': document.execCommand('insertText', false, '~a^2 + b^2 = c^2~'); break;
      case 'blank': document.execCommand('insertText', false, '{{blank}}'); break;
    }
    area.dispatchEvent(new Event('input'));
    updateToolbarState(editor);
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
      const s = ta.selectionStart;
      ta.value = ta.value.slice(0, s) + '![](' + url + ')' + ta.value.slice(ta.selectionEnd);
      ta.dispatchEvent(new Event('input'));
    } else {
      const area = ed.querySelector('.rich-editor-body');
      area.focus();
      document.execCommand('insertImage', false, url);
      area.dispatchEvent(new Event('input'));
    }
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
      case 'heading': case 'h3': linePrefix('### '); break;
      case 'h1': linePrefix('# '); break;
      case 'h2': linePrefix('## '); break;
      case 'ul': linePrefix('- '); break;
      case 'ol': linePrefix('1. '); break;
      case 'alignLeft': case 'alignCenter': case 'alignRight': break;
      case 'math': wrap('~', '~', 'a^2+b^2=c^2'); break;
      case 'blank': ta.value = ta.value.slice(0, s) + '{{blank}}' + ta.value.slice(e); break;
    }
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
    if (w && /^\d+$/.test(w)) w += 'px';
    if (h && /^\d+$/.test(h)) h += 'px';
    let style = '';
    if (w) style += 'width:' + w + ';';
    if (h) style += 'height:' + h + ';';
    if (!style) return '![' + alt + '](' + src + ')';
    return '<img src="' + src + '"' + (alt ? ' alt="' + alt + '"' : '') + ' style="' + style + '">';
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
    const isMark = tag === 'mark' || (bg && bg !== 'transparent' && bg !== 'rgba(0, 0, 0, 0)');
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
    nodes.forEach(n => {
      if (n.nodeType === 3) { out += n.nodeValue; return; }
      if (n.nodeType !== 1) return;
      const tag = n.tagName.toLowerCase();
      const align = blockAlign(n);
      if (align && (tag === 'p' || tag === 'div' || /^h[1-6]$/.test(tag) || tag === 'blockquote')) {
        out += '\n<div style="text-align:' + align + '">' + n.innerHTML + '</div>\n\n';
        return;
      }
      switch (tag) {
        case 'h1': out += '\n# ' + serializeInline(n).trim() + '\n\n'; break;
        case 'h2': out += '\n## ' + serializeInline(n).trim() + '\n\n'; break;
        case 'h3': case 'h4': case 'h5': case 'h6':
          out += '\n### ' + serializeInline(n).trim() + '\n\n'; break;
        case 'ul': case 'ol':
          Array.prototype.forEach.call(n.children, (li, i) => {
            if (li.tagName && li.tagName.toLowerCase() === 'li')
              out += (tag === 'ol' ? (i + 1) + '. ' : '- ') + serializeInline(li).trim() + '\n';
          });
          out += '\n';
          break;
        case 'blockquote':
          out += '\n> ' + serializeInline(n).trim().replace(/\n/g, '\n> ') + '\n\n';
          break;
        case 'pre': {
          const codeEl = n.querySelector && n.querySelector('code');
          const raw = (codeEl ? codeEl.textContent : n.textContent) || '';
          out += '\n```\n' + raw.replace(/\n$/, '') + '\n```\n\n';
          break;
        }
        case 'img': out += imgToMarkup(n) + '\n'; break;
        case 'div': case 'p': {
          const inner = serializeInline(n).trim();
          out += inner + '\n\n';
          break;
        }
        case 'br': out += '\n'; break;
        default: out += serializeNode(n) + '\n';
      }
    });
    return out;
  }

  function htmlToMd(html) {
    const root = document.createElement('div');
    root.innerHTML = html || '';
    return serializeBlocks(Array.prototype.slice.call(root.childNodes)).replace(/\n{3,}/g, '\n\n').trim();
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
      .replace(/<div style="text-align:[^"]*">[\s\S]*?<\/div>/gi, (m) => {
        raw.push(m); return '\u0000R' + (raw.length - 1) + '\u0000';
      })
      .replace(/<img\b[^>]*>/gi, (m) => {
        raw.push(m); return '\u0000R' + (raw.length - 1) + '\u0000';
      })
      .replace(/<\/?(?:mark|u)>/gi, (m) => {
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

    s = s.replace(/!\[[^\]]*\]\(([^)]+)\)/g, (m, url) => {
      imgs.push('<img src="' + url + '" style="max-width:100%;border-radius:4px;">');
      return '\u0000I' + (imgs.length - 1) + '\u0000';
    })
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/^### (.+)$/gm, '<h3>$1</h3>')
      .replace(/^## (.+)$/gm, '<h2>$1</h2>')
      .replace(/^# (.+)$/gm, '<h1>$1</h1>');
    s = formatInlineMarkdown(s);
    s = s.replace(/(?:^|\n)((?:\d+\.[ \t].*(?:\n|$))+)/g, (m) =>
      '\n<ol>' + m.trim().split('\n').map(l => '<li>' + l.replace(/^\d+\.[ \t]/, '') + '</li>').join('') + '</ol>\n');
    s = s.replace(/(?:^|\n)((?:[-*+][ \t].*(?:\n|$))+)/g, (m) =>
      '\n<ul>' + m.trim().split('\n').map(l => '<li>' + l.replace(/^[-*+][ \t]/, '') + '</li>').join('') + '</ul>\n');
    s = s.replace(/\n/g, '<br>')
      .replace(/<br>(<ul>|<\/ul>|<ol>|<\/ol>|<h1>|<h2>|<h3>|<pre>|<blockquote>|<\/blockquote>)/g, '$1')
      .replace(/(<\/ul>|<\/ol>|<\/pre>|<\/blockquote>|<pre>|<blockquote>)<br>/g, '$1');
    s = s.replace(/\u0000I(\d+)\u0000/g, (m, i) => imgs[+i]);
    s = s.replace(/\u0000B(\d+)\u0000/g, (m, i) => blocks[+i]);
    s = restoreMath(s, math);
    s = s.replace(/\u0000R(\d+)\u0000/g, (m, i) => raw[+i]);
    return s;
  }

  const RichEditor = {
    toolbar: RTE_TOOLBAR,
    configure(opts) {
      if (!opts) return;
      if (opts.imageUploadUrl != null) config.imageUploadUrl = opts.imageUploadUrl;
      if (opts.csrfToken != null) config.csrfToken = opts.csrfToken;
      if (opts.onChange != null) config.onChange = opts.onChange;
      if (opts.imageInputId != null) config.imageInputId = opts.imageInputId;
      if (opts.messages) Object.assign(config.messages, opts.messages);
      ensureSelectionListener();
    },
    html: richEditorTpl,
    mount(root) {
      ensureSelectionListener();
      mountRichEditors(root);
      initEditors(root);
    },
    init(root) {
      ensureSelectionListener();
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
