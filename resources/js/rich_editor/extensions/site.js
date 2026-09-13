import { mergeAttributes } from '@tiptap/core';
import Heading from '@tiptap/extension-heading';
import Image from '@tiptap/extension-image';
import Highlight from '@tiptap/extension-highlight';
import Paragraph from '@tiptap/extension-paragraph';

const LEVEL_TO_TAG = { 1: 'h3', 2: 'h4', 3: 'h5' };
const TAG_TO_LEVEL = { H1: 1, H2: 1, H3: 1, H4: 2, H5: 3, H6: 3 };

const EMPTY_PARAGRAPH_MARKDOWN = '&nbsp;';

export const SiteParagraph = Paragraph.extend({
  renderMarkdown: (node, helpers, ctx) => {
    // Do not append \n\n here. The document renderer already joins blocks
    // with \n\n; a second suffix doubles blank lines on every round-trip.
    const content = Array.isArray(node.content) ? node.content : [];
    let inner;
    if (content.length === 0) {
      const previousContent = Array.isArray(ctx?.previousNode?.content)
        ? ctx.previousNode.content
        : [];
      const previousEmpty = ctx?.previousNode?.type === 'paragraph' && previousContent.length === 0;
      inner = previousEmpty ? EMPTY_PARAGRAPH_MARKDOWN : '';
    } else {
      inner = helpers.renderChildren(content);
    }
    const align = node.attrs?.textAlign;
    if (align && align !== 'left' && align !== 'start') {
      return `<div style="text-align:${align}">${inner}</div>`;
    }
    return inner;
  },
});

export const SiteHeading = Heading.extend({
  addOptions() {
    return { ...this.parent?.(), levels: [1, 2, 3] };
  },
  parseHTML() {
    return [
      { tag: 'h1', attrs: { level: 1 } },
      { tag: 'h2', attrs: { level: 1 } },
      { tag: 'h3', attrs: { level: 1 } },
      { tag: 'h4', attrs: { level: 2 } },
      { tag: 'h5', attrs: { level: 3 } },
      { tag: 'h6', attrs: { level: 3 } },
    ];
  },
  renderHTML({ node, HTMLAttributes }) {
    const tag = LEVEL_TO_TAG[node.attrs.level] || 'h3';
    return [tag, mergeAttributes(this.options.HTMLAttributes, HTMLAttributes), 0];
  },
});

function applyImageBox(img, attrs) {
  img.style.width = attrs.width || '';
  img.style.height = attrs.height || '';
}

function persistImageBox(editor, getPos, img, { lockHeight }) {
  const pos = typeof getPos === 'function' ? getPos() : null;
  if (typeof pos !== 'number') return;
  const current = editor.state.doc.nodeAt(pos);
  if (!current) return;
  const width = img.style.width && img.style.width !== 'auto' ? img.style.width : null;
  const height = lockHeight && img.style.height && img.style.height !== 'auto'
    ? img.style.height
    : null;
  editor.chain().command(({ tr }) => {
    tr.setNodeMarkup(pos, undefined, { ...current.attrs, width, height });
    return true;
  }).run();
}

function bindImageResize(handle, { img, editor, getPos, mode }) {
  handle.addEventListener('mousedown', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const rect = img.getBoundingClientRect();
    const startW = rect.width;
    const startH = rect.height;
    const edge = handle.dataset.edge || 'e';
    const onMove = (ev) => {
      const dx = ev.clientX - startX;
      const signed = edge === 'w' || edge === 'sw' ? -dx : dx;
      const w = Math.max(48, Math.round(startW + signed));
      img.style.width = w + 'px';
      if (mode === 'stretch') {
        img.style.height = Math.round(startH) + 'px';
      } else {
        img.style.height = 'auto';
      }
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      persistImageBox(editor, getPos, img, { lockHeight: mode === 'stretch' });
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}

function sizeFromEl(el, key) {
  const style = el.getAttribute('style') || '';
  const m = style.match(new RegExp(`(?:^|;)\\s*${key}\\s*:\\s*([^;]+)`, 'i'));
  let v = (el.style && el.style[key]) || el.getAttribute(key) || (m && m[1]) || '';
  v = String(v).trim();
  if (v === 'auto') return null;
  if (/^\d+$/.test(v)) v += 'px';
  return v || null;
}

export const SiteImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      width: {
        default: null,
        parseHTML: (el) => sizeFromEl(el, 'width'),
        renderHTML: (attrs) => (attrs.width ? { style: `width:${attrs.width};` } : {}),
      },
      height: {
        default: null,
        parseHTML: (el) => sizeFromEl(el, 'height'),
        renderHTML: (attrs) => (attrs.height ? { style: `height:${attrs.height};` } : {}),
      },
    };
  },
  renderHTML({ node, HTMLAttributes }) {
    const styleParts = [];
    if (node.attrs.width) styleParts.push('width:' + node.attrs.width);
    if (node.attrs.height) styleParts.push('height:' + node.attrs.height);
    const attrs = mergeAttributes(this.options.HTMLAttributes, HTMLAttributes);
    if (styleParts.length) attrs.style = styleParts.join(';') + (attrs.style ? ';' + attrs.style : '');
    return ['img', attrs];
  },
  renderMarkdown: (node) => {
    const src = node.attrs?.src || '';
    const alt = node.attrs?.alt || '';
    const w = node.attrs?.width;
    const h = node.attrs?.height;
    if (w || h) {
      let style = '';
      if (w) style += `width:${w};`;
      if (h) style += `height:${h};`;
      const altAttr = alt ? ` alt="${alt}"` : '';
      return `<img src="${src}"${altAttr} style="${style}">`;
    }
    return `![${alt}](${src})`;
  },
  addNodeView() {
    return ({ node, editor, getPos }) => {
      const wrap = document.createElement('span');
      wrap.className = 'rte-image-wrap';
      const img = document.createElement('img');
      img.src = node.attrs.src || '';
      img.alt = node.attrs.alt || '';
      img.draggable = false;
      applyImageBox(img, node.attrs);

      const handles = [
        { edge: 'se', mode: 'scale', title: 'Scale proportionally' },
        { edge: 'sw', mode: 'scale', title: 'Scale proportionally' },
        { edge: 'e', mode: 'stretch', title: 'Stretch width' },
        { edge: 'w', mode: 'stretch', title: 'Stretch width' },
      ].map((spec) => {
        const handle = document.createElement('span');
        handle.className = 'img-resize-handle ' + spec.edge;
        handle.dataset.edge = spec.edge;
        handle.dataset.mode = spec.mode;
        handle.title = spec.title;
        bindImageResize(handle, { img, editor, getPos, mode: spec.mode });
        return handle;
      });

      wrap.appendChild(img);
      handles.forEach((h) => wrap.appendChild(h));
      return {
        dom: wrap,
        selectNode() { wrap.classList.add('is-selected'); },
        deselectNode() { wrap.classList.remove('is-selected'); },
        update(updated) {
          if (updated.type.name !== 'image') return false;
          img.src = updated.attrs.src || '';
          img.alt = updated.attrs.alt || '';
          applyImageBox(img, updated.attrs);
          return true;
        },
      };
    };
  },
});

export const SiteHighlight = Highlight.extend({
  parseMarkdown: (token) => ({
    type: 'text',
    text: token.text || '',
    marks: [{ type: 'highlight' }],
  }),
  renderMarkdown: (node, helpers) => `<mark>${helpers.renderChildren(node)}</mark>`,
  markdownTokenizer: {
    name: 'highlight',
    level: 'inline',
    start: (src) => {
      const a = src.indexOf('==');
      const b = src.indexOf('<mark');
      if (a < 0) return b;
      if (b < 0) return a;
      return Math.min(a, b);
    },
    tokenize: (src) => {
      let m = src.match(/^==([^=]+)==/);
      if (m) return { type: 'highlight', raw: m[0], text: m[1] };
      m = src.match(/^<mark\b[^>]*>([\s\S]*?)<\/mark>/);
      if (m) return { type: 'highlight', raw: m[0], text: m[1] };
      return undefined;
    },
  },
});

export { TAG_TO_LEVEL };
