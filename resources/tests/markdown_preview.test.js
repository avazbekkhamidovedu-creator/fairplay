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
      batchUrl: '/widgets/preview/problem/batch',
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

    MarkdownPreview.configure({
      url: '/widgets/preview/problem',
      batchUrl: '',
      csrfToken: 'test-csrf',
    });
    MarkdownPreview.clearCache();

    const a = await MarkdownPreview.render('**x**');
    const b = await MarkdownPreview.render('**x**');
    assert.equal(a, '<p>rendered</p>');
    assert.equal(b, '<p>rendered</p>');
    assert.equal(calls, 1);
    assert.equal(MarkdownPreview.getCached('**x**'), '<p>rendered</p>');
  });

  it('renderMany preserves order via keyed batch when configured', async () => {
    MarkdownPreview.clearCache();
    MarkdownPreview.configure({
      url: '/widgets/preview/problem',
      batchUrl: '/widgets/preview/problem/batch',
      csrfToken: 'test-csrf',
    });
    let batchCalls = 0;
    global.fetch = async (url, opts) => {
      if (url.endsWith('/batch')) {
        batchCalls += 1;
        const payload = JSON.parse(opts.body);
        assert.ok(Array.isArray(payload.items));
        return {
          ok: true,
          json: async () => ({
            items: payload.items.map(it => ({ key: it.key, html: `<p>${it.content}</p>` })),
          }),
        };
      }
      throw new Error('unexpected url ' + url);
    };
    const out = await MarkdownPreview.renderMany(['a', 'b', 'c']);
    assert.deepEqual(out, ['<p>a</p>', '<p>b</p>', '<p>c</p>']);
    assert.equal(batchCalls, 1);
  });

  it('renderKeyed uses one batch POST for uncached keys', async () => {
    MarkdownPreview.clearCache();
    MarkdownPreview.configure({
      url: '/widgets/preview/problem',
      batchUrl: '/widgets/preview/problem/batch',
      csrfToken: 'test-csrf',
    });
    let batchCalls = 0;
    global.fetch = async (url, opts) => {
      assert.equal(url, '/widgets/preview/problem/batch');
      batchCalls += 1;
      const payload = JSON.parse(opts.body);
      return {
        ok: true,
        json: async () => ({
          items: payload.items.map(it => ({
            key: it.key,
            html: `<div class="frag">${it.key}:${it.content}</div>`,
          })),
        }),
      };
    };
    const rows = await MarkdownPreview.renderKeyed([
      { key: 'stem', md: 'S' },
      { key: 'opt-0', md: 'A' },
      { key: 'opt-1', md: 'B' },
    ]);
    assert.equal(batchCalls, 1);
    assert.equal(rows.length, 3);
    assert.equal(rows[0].html, '<div class="frag">stem:S</div>');
    assert.equal(rows[2].key, 'opt-1');

    // Second call should hit cache only (no fetch).
    global.fetch = async () => { throw new Error('should not fetch'); };
    const again = await MarkdownPreview.renderKeyed([
      { key: 'stem', md: 'S' },
      { key: 'opt-0', md: 'A' },
    ]);
    assert.equal(again[0].html, '<div class="frag">stem:S</div>');
  });
});
