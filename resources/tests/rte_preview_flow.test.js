'use strict';

/**
 * P3 “E2E-without-browser” flow:
 *   Markdown (saved source)
 *     → RichEditor.mdToHtml (editor display)
 *     → RichEditor.htmlToMd (mode switch / save)
 *     → fragments that MarkdownPreview.renderKeyed would POST
 *
 * Asserts that quote / list / code-block markers survive the editor round-trip
 * so the server batch preview still receives real Markdown (not &gt; arrows).
 */

const { describe, it, before } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const fixturePath = path.join(__dirname, 'fixtures', 'rte_markdown.json');
const fixtures = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));

let RichEditor;
let MarkdownPreview;

before(() => {
  const dom = new JSDOM('<!doctype html><html><body></body></html>');
  global.window = dom.window;
  global.document = dom.window.document;
  global.Node = dom.window.Node;
  RichEditor = require('../rich_editor.js');
  MarkdownPreview = require('../markdown_preview.js');
});

function editorRoundTrip(md) {
  const html = RichEditor.mdToHtml(md);
  return RichEditor.htmlToMd(html);
}

describe('RTE ↔ preview flow (P3)', () => {
  it('keeps quote / list / code markers after rich↔markdown round-trip', () => {
    const samples = [
      { id: 'quote', md: '> hello quote', expect: /^>/m },
      { id: 'ul', md: '- one\n- two', expect: /^- one$/m },
      { id: 'ol', md: '1. one\n2. two', expect: /^1\. /m },
      { id: 'fence', md: '```\nint x = 1;\n```', expect: /```[\s\S]*int x = 1/ },
      { id: 'bold', md: '**bold**', expect: /\*\*bold\*\*/ },
    ];
    for (const s of samples) {
      const back = editorRoundTrip(s.md);
      assert.match(back, s.expect, `${s.id}: got ${back}`);
    }
  });

  it('buildKeyed preview payload from round-tripped fragments', async () => {
    MarkdownPreview.clearCache();
    MarkdownPreview.configure({
      url: '/widgets/preview/problem',
      batchUrl: '/widgets/preview/problem/batch',
      csrfToken: 't',
    });

    const stemMd = editorRoundTrip('Stem with **bold** and > quote line\n\n> quoted');
    // Ensure quote line still present for server
    assert.match(stemMd, />/);

    const optMd = editorRoundTrip('```\ncode()\n```');
    assert.match(optMd, /```/);

    const posted = [];
    global.fetch = async (url, opts) => {
      assert.ok(String(url).endsWith('/batch'));
      const body = JSON.parse(opts.body);
      posted.push(body);
      return {
        ok: true,
        json: async () => ({
          items: body.items.map(it => ({
            key: it.key,
            html: `<section data-key="${it.key}">${it.content}</section>`,
          })),
        }),
      };
    };

    const rows = await MarkdownPreview.renderKeyed([
      { key: 'stem', md: stemMd },
      { key: 'opt-0', md: optMd },
    ]);
    assert.equal(posted.length, 1);
    assert.equal(posted[0].items.length, 2);
    assert.equal(posted[0].items[0].key, 'stem');
    assert.match(posted[0].items[0].content, />/);
    assert.match(posted[0].items[1].content, /```/);
    assert.equal(rows[0].key, 'stem');
    assert.match(rows[1].html, /opt-0/);
  });

  it('fixture markdown still contains server-expected needles after editor round-trip when possible', () => {
    // Not every fixture is round-trip stable (server heading remap etc.);
    // focus on content tokens that create_v2 actually authors in the editor.
    const focus = fixtures.cases.filter(c =>
      ['bold_italic', 'mark_highlight', 'blank_placeholder', 'math_tilde'].includes(c.id));
    for (const c of focus) {
      const back = editorRoundTrip(c.markdown);
      if (c.id === 'bold_italic') {
        assert.match(back, /bold/);
        assert.match(back, /italic/);
      }
      if (c.id === 'blank_placeholder') assert.match(back, /\{\{blank\}\}/);
      if (c.id === 'math_tilde') assert.match(back, /a\^2/);
      if (c.id === 'mark_highlight') assert.match(back, /highlight|mark/i);
    }
  });
});
