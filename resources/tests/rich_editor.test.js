'use strict';

const { describe, it, before } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');

let RichEditor;

before(() => {
  const dom = new JSDOM('<!doctype html><html><body></body></html>');
  global.window = dom.window;
  global.document = dom.window.document;
  global.Node = dom.window.Node;
  RichEditor = require('../rich_editor.js');
});

describe('RichEditor.htmlToMd / mdToHtml', () => {
  it('round-trips basic bold/italic via markdown path', () => {
    const md = 'Hello **bold** and *italic*.';
    const html = RichEditor.mdToHtml(md);
    assert.match(html, /<strong>bold<\/strong>/);
    assert.match(html, /<em>italic<\/em>/);
    const back = RichEditor.htmlToMd(html);
    assert.match(back, /\*\*bold\*\*/);
    assert.match(back, /\*italic\*/);
  });

  it('preserves math delimiters through md→html→md', () => {
    const md = 'Formula ~a^2 + b^2 = c^2~ and $$x=1$$.';
    const html = RichEditor.mdToHtml(md);
    assert.match(html, /~a\^2 \+ b\^2 = c\^2~/);
    assert.match(html, /\$\$x=1\$\$/);
    const back = RichEditor.htmlToMd(`<p>${html}</p>`);
    assert.match(back, /~a\^2 \+ b\^2 = c\^2~/);
    assert.match(back, /\$\$x=1\$\$/);
  });

  it('preserves {{blank}} placeholder', () => {
    const md = 'Fill {{blank}} here.';
    const html = RichEditor.mdToHtml(md);
    assert.match(html, /\{\{blank\}\}/);
    const back = RichEditor.htmlToMd(`<p>${html}</p>`);
    assert.match(back, /\{\{blank\}\}/);
  });

  it('serializes headings from HTML using site demotion mapping', () => {
    // Preview renderer: #→h3, ##→h4, ###→h5 — serialize back the same way
    const md = RichEditor.htmlToMd('<h3>Title</h3><h4>Sub</h4><h5>Small</h5><p>body</p>');
    assert.match(md, /^# Title$/m);
    assert.match(md, /^## Sub$/m);
    assert.match(md, /^### Small$/m);
    assert.match(md, /body/);
  });

  it('projects markdown headings with +2 demotion like the server', () => {
    const html = RichEditor.mdToHtml('# A\n\n## B\n\n### C');
    assert.match(html, /<h3>A<\/h3>/);
    assert.match(html, /<h4>B<\/h4>/);
    assert.match(html, /<h5>C<\/h5>/);
    assert.doesNotMatch(html, /<h1>/);
  });

  it('keeps sized images as HTML img tags', () => {
    const html = '<p><img src="https://example.com/a.png" style="width:120px;"></p>';
    const md = RichEditor.htmlToMd(html);
    assert.match(md, /<img src="https:\/\/example\.com\/a\.png"/);
    assert.match(md, /width:120px/);
  });

  it('keeps plain images as markdown image syntax', () => {
    const html = '<p><img src="https://example.com/a.png" alt="x"></p>';
    const md = RichEditor.htmlToMd(html);
    assert.equal(md.trim(), '![x](https://example.com/a.png)');
  });

  it('preserves text-align blocks as HTML div', () => {
    const html = '<div style="text-align: center;">Centered</div>';
    const md = RichEditor.htmlToMd(html);
    assert.match(md, /text-align:center/);
    assert.match(md, /Centered/);
  });

  it('keeps heading tag when text-align is applied', () => {
    const md = RichEditor.htmlToMd('<h3 style="text-align: center;">Order Check</h3>');
    assert.match(md, /text-align:\s*center/);
    assert.match(md, /<h3>/);
    assert.match(md, /Order Check/);
    assert.doesNotMatch(md, /^# Order Check$/m); // must not demote to bare heading text
    const html = RichEditor.mdToHtml(md);
    assert.match(html, /text-align:\s*center/);
    assert.match(html, /<h3>/);
    assert.match(html, /Order Check/);
  });

  it('keeps image inside align wrapper', () => {
    const md = RichEditor.htmlToMd(
      '<div style="text-align: center;"><img src="https://example.com/a.png" style="width:120px;"></div>');
    assert.match(md, /text-align:\s*center/);
    assert.match(md, /<img[^>]+src="https:\/\/example\.com\/a\.png"/);
    const html = RichEditor.mdToHtml(md);
    assert.match(html, /text-align:\s*center/);
    assert.match(html, /<img/);
  });

  it('preserves resized image width/height through align round-trip', () => {
    const md = RichEditor.htmlToMd(
      '<div style="text-align:center">' +
      '<img src="https://example.com/a.png" style="max-width:100%;border-radius:4px;width:220px;height:110px;">' +
      '</div>');
    assert.match(md, /width:\s*220px/);
    assert.match(md, /height:\s*110px/);
    const html = RichEditor.mdToHtml(md);
    assert.match(html, /width:\s*220px/);
    assert.match(html, /height:\s*110px/);
    assert.match(html, /text-align:\s*center/);
  });

  it('preserves <mark> through md→html', () => {
    const html = RichEditor.mdToHtml('A <mark>highlight</mark> word.');
    assert.match(html, /<mark>highlight<\/mark>/);
  });

  it('renders lists from markdown', () => {
    const html = RichEditor.mdToHtml('- a\n- b\n\n1. one\n2. two');
    assert.match(html, /<ul>/);
    assert.match(html, /<ol>/);
    assert.match(html, /<li>a<\/li>/);
  });

  it('converts blockquotes instead of escaping > to an arrow', () => {
    const html = RichEditor.mdToHtml('> quoted text');
    assert.match(html, /<blockquote>/);
    assert.match(html, /quoted text/);
    assert.doesNotMatch(html, /&gt;/);
    const back = RichEditor.htmlToMd(html);
    assert.match(back, /^>/m);
  });

  it('round-trips fenced code blocks into styled pre', () => {
    const md = '```\nint x = 1;\n```';
    const html = RichEditor.mdToHtml(md);
    assert.match(html, /<pre><code>/);
    assert.match(html, /int x = 1;/);
    const back = RichEditor.htmlToMd(html);
    assert.match(back, /```/);
    assert.match(back, /int x = 1;/);
  });

  it('round-trips lists through html→md→html', () => {
    const md = RichEditor.htmlToMd('<ul><li>a</li><li>b</li></ul>');
    assert.match(md, /^- a$/m);
    const html = RichEditor.mdToHtml(md);
    assert.match(html, /<ul>/);
    assert.match(html, /<li>a<\/li>/);
  });

  it('keeps inline code/bold on one line across rich↔markdown (Order Check repro)', () => {
    const md = [
      '# Order Check',
      '',
      '## Description',
      '',
      'Write a program that takes three `double` values `x`, `y`, and `z`.',
      'It should return `true` if they are **strictly ascending**.',
      '',
      '## Input Format',
      '',
      '```',
      'x y z',
      '```',
      '',
      '- `x < y < z`',
      '- `x > y > z`',
    ].join('\n');

    const once = RichEditor.htmlToMd(RichEditor.mdToHtml(md));
    const twice = RichEditor.htmlToMd(RichEditor.mdToHtml(once));

    assert.match(once, /Write a program that takes three `double` values `x`, `y`, and `z`\./);
    assert.doesNotMatch(once, /values `x`\n/);
    assert.doesNotMatch(once, /pr\*ogram/);
    assert.doesNotMatch(once, /<mark>/);
    assert.match(once, /```\nx y z\n```/);
    assert.match(once, /\*\*strictly ascending\*\*/);
    // Stable under repeated mode switches
    assert.equal(twice, once);
  });
});

describe('RichEditor.html markup factory', () => {
  it('builds editor markup with data-editor name', () => {
    const markup = RichEditor.html('stem', { placeholder: 'hi' });
    assert.match(markup, /data-editor="stem"/);
    assert.match(markup, /contenteditable="true"/);
    assert.match(markup, /data-placeholder="hi"/);
    assert.match(markup, /data-cmd="link"/);
    assert.doesNotMatch(markup, /data-cmd="hr"/);
    assert.match(markup, /data-cmd="math"/);
    assert.match(markup, /data-cmd="mention"/);
    assert.match(markup, /data-cmd="help"/);
    assert.doesNotMatch(markup, /data-cmd="displayMath"/);
  });
});

describe('link / hr / math projection', () => {
  it('round-trips markdown links', () => {
    const md = 'see [DKUOJ](https://example.com) docs';
    const html = RichEditor.mdToHtml(md);
    assert.match(html, /<a href="https:\/\/example\.com">DKUOJ<\/a>/);
    const back = RichEditor.htmlToMd('<p>' + html + '</p>');
    assert.match(back, /\[DKUOJ\]\(https:\/\/example\.com\)/);
  });

  it('round-trips horizontal rules', () => {
    const md = 'above\n\n---\n\nbelow';
    const html = RichEditor.mdToHtml(md);
    assert.match(html, /<hr/);
    const back = RichEditor.htmlToMd(html);
    assert.match(back, /---/);
    assert.match(back, /above/);
    assert.match(back, /below/);
  });

  it('preserves display math delimiters', () => {
    const md = '$$ a^2 + b^2 = c^2 $$';
    const html = RichEditor.mdToHtml(md);
    assert.match(html, /\$\$ a\^2 \+ b\^2 = c\^2 \$\$/);
    const back = RichEditor.htmlToMd('<p>' + html + '</p>');
    assert.match(back, /\$\$/);
    assert.match(back, /a\^2/);
  });
});

describe('Markdown sole source of truth', () => {
  function mountNamed(name) {
    document.body.innerHTML = '<div id="root"><div data-rich-mount="' + name + '"></div></div>';
    RichEditor.mount(document.getElementById('root'));
    return document.querySelector('.rich-editor[data-editor="' + name + '"]');
  }

  function clickMode(editor, mode) {
    const btn = editor.querySelector('.mode-btn[data-mode="' + mode + '"]');
    btn.click();
  }

  it('getMarkdown equals textarea after rich DOM edits', () => {
    const ed = mountNamed('stem');
    const body = ed.querySelector('.rich-editor-body');
    const ta = ed.querySelector('textarea');
    body.innerHTML = '<p><strong>bold</strong> text</p>';
    body.dispatchEvent(new window.Event('input', { bubbles: true }));
    assert.equal(RichEditor.getMarkdown('stem'), ta.value);
    assert.match(ta.value, /\*\*bold\*\*/);
  });

  it('setMarkdown is returned by getMarkdown in rich and markdown modes', () => {
    const ed = mountNamed('stem');
    const src = '> quoted\n\n- item';
    RichEditor.setMarkdown('stem', src);
    assert.equal(RichEditor.getMarkdown('stem'), src);
    clickMode(ed, 'markdown');
    assert.equal(RichEditor.getMarkdown('stem'), src);
    assert.ok(ed.classList.contains('mode-markdown'));
    clickMode(ed, 'rich');
    assert.equal(RichEditor.getMarkdown('stem'), src);
  });

  it('mode round-trip keeps quote / list / fence markers in source', () => {
    const ed = mountNamed('stem');
    const src = '> hello\n\n- one\n- two\n\n```\nint x = 1;\n```';
    RichEditor.setMarkdown('stem', src);
    clickMode(ed, 'rich');
    clickMode(ed, 'markdown');
    clickMode(ed, 'rich');
    const back = RichEditor.getMarkdown('stem');
    assert.match(back, /^>/m);
    assert.match(back, /^- one$/m);
    assert.match(back, /```/);
    assert.match(back, /int x = 1/);
  });

  it('getHtml is derived from Markdown source via mdToHtml(getMarkdown)', () => {
    mountNamed('stem');
    RichEditor.setMarkdown('stem', '**bold**');
    const html = RichEditor.getHtml('stem');
    assert.match(html, /<strong>bold<\/strong>/);
    // Markdown mode: dirty contenteditable must not affect getHtml/getMarkdown
    const ed = document.querySelector('.rich-editor[data-editor="stem"]');
    clickMode(ed, 'markdown');
    ed.querySelector('.rich-editor-body').innerHTML = '<p>STALE</p>';
    assert.equal(RichEditor.getMarkdown('stem'), '**bold**');
    assert.doesNotMatch(RichEditor.getHtml('stem'), /STALE/);
  });
});
