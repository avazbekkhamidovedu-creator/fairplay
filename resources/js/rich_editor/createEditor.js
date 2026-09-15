import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from '@tiptap/markdown';
import { Table, TableRow, TableCell, TableHeader } from '@tiptap/extension-table';
import Link from '@tiptap/extension-link';
import Placeholder from '@tiptap/extension-placeholder';
import TextAlign from '@tiptap/extension-text-align';
import Underline from '@tiptap/extension-underline';
import { SiteInlineMath, SiteBlockMath } from './extensions/math.js';
import { SiteCodeBlock } from './extensions/codeBlock.js';
import { Blank } from './extensions/blank.js';
import { Mention } from './extensions/mention.js';
import { SiteHeading, SiteParagraph, SiteImage, SiteHighlight } from './extensions/site.js';

export function editorExtensions(opts = {}) {
  return [
    Markdown,
    StarterKit.configure({
      strike: false,
      heading: false,
      paragraph: false,
      link: false,
      underline: false,
      codeBlock: false,
    }),
    SiteCodeBlock,
    SiteParagraph,
    SiteHeading,
    SiteHighlight.configure({ multicolor: false }),
    Underline,
    Link.configure({
      openOnClick: false,
      HTMLAttributes: { rel: 'noopener' },
    }),
    SiteImage.configure({ inline: true, allowBase64: false }),
    Table.configure({ resizable: false, HTMLAttributes: { class: 'table' } }),
    TableRow,
    TableHeader,
    TableCell,
    TextAlign.configure({ types: ['heading', 'paragraph'] }),
    Placeholder.configure({ placeholder: opts.placeholder || '' }),
    SiteInlineMath.configure({
      katexOptions: { throwOnError: false, displayMode: false },
      onClick: opts.onMathClick,
    }),
    SiteBlockMath.configure({
      katexOptions: { throwOnError: false, displayMode: true },
      onClick: opts.onMathClick,
    }),
    Blank,
    Mention,
  ];
}

export function createTiptapEditor({
  element, placeholder, editable = true, onUpdate, onSelectionUpdate, onMathClick, onMentionTrigger, onPaste,
}) {
  return new Editor({
    element,
    editable,
    content: '',
    contentType: 'markdown',
    extensions: editorExtensions({ placeholder, onMathClick }),
    editorProps: {
      attributes: {
        class: 'rich-editor-body',
        'data-placeholder': placeholder || '',
      },
      handleTextInput(view, from, to, text) {
        if (text === '@' && typeof onMentionTrigger === 'function') {
          const type = view.state.selection.$from.parent.type.name;
          if (type !== 'codeBlock' && type !== 'inlineMath' && type !== 'blockMath') {
            onMentionTrigger();
            return true;
          }
        }
        return false;
      },
      handlePaste(view, event) {
        return typeof onPaste === 'function' ? !!onPaste(event) : false;
      },
    },
    onUpdate,
    onSelectionUpdate,
  });
}

export function createHeadlessEditor() {
  return new Editor({
    editable: false,
    content: '',
    contentType: 'markdown',
    extensions: editorExtensions(),
  });
}

export function parseMarkdownToHtml(md) {
  const ed = createHeadlessEditor();
  ed.commands.setContent(md || '', { contentType: 'markdown' });
  const html = ed.getHTML();
  ed.destroy();
  return html;
}

export function serializeHtmlToMarkdown(html) {
  const ed = createHeadlessEditor();
  ed.commands.setContent(html || '');
  const md = ed.getMarkdown();
  ed.destroy();
  return md;
}
