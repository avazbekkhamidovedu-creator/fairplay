'use strict';

const { describe, it, before, after } = require('node:test');
const assert = require('node:assert/strict');

const MarkdownPreview = require('../markdown_preview.js');

describe('MarkdownPreview', () => {
  let originalFetch;

  before(() => {
    originalFetch = global.fetch;
    MarkdownPreview.clearCache();
    MarkdownPreview.configure({
      url: '/widgets/preview/problem',
      csrfToken: 'test-csrf',
    });
  });

  after(() => {
    global.fetch = originalFetch;
    MarkdownPreview.clearCache();
  });

  it('posts content and caches the result', async () => {
    let calls = 0;
    global.fetch = async (url, opts) => {
      calls += 1;
      assert.equal(url, '/widgets/preview/problem');
      assert.equal(opts.method, 'POST');
      assert.equal(opts.headers['X-CSRFToken'], 'test-csrf');
      assert.ok(String(opts.body).includes('content='));
      return {
        ok: true,
        text: async () => '<p>rendered</p>',
      };
    };

    const a = await MarkdownPreview.render('**x**');
    const b = await MarkdownPreview.render('**x**');
    assert.equal(a, '<p>rendered</p>');
    assert.equal(b, '<p>rendered</p>');
    assert.equal(calls, 1);
    assert.equal(MarkdownPreview.getCached('**x**'), '<p>rendered</p>');
  });

  it('renderMany preserves order', async () => {
    MarkdownPreview.clearCache();
    global.fetch = async (_url, opts) => {
      const body = String(opts.body);
      const md = decodeURIComponent(body.replace(/^content=/, '').replace(/\+/g, ' '));
      return { ok: true, text: async () => `<p>${md}</p>` };
    };
    const out = await MarkdownPreview.renderMany(['a', 'b', 'c']);
    assert.deepEqual(out, ['<p>a</p>', '<p>b</p>', '<p>c</p>']);
  });
});
