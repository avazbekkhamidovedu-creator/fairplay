/**
 * Server-authoritative Markdown preview helper.
 * Posts to a Django markdown preview endpoint (same pipeline as problem pages).
 *
 * Browser: window.MarkdownPreview
 * Node (tests): module.exports
 *
 *   MarkdownPreview.configure({ url, csrfToken })
 *   MarkdownPreview.render(md) -> Promise<html>
 *   MarkdownPreview.renderMany(mds) -> Promise<html[]>
 *   MarkdownPreview.getCached(md) -> html|undefined
 *   MarkdownPreview.clearCache()
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.MarkdownPreview = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const state = {
    url: '',
    csrfToken: '',
    cache: new Map(),
    inflight: new Map(),
  };

  function configure(opts) {
    if (!opts) return;
    if (opts.url != null) state.url = opts.url;
    if (opts.csrfToken != null) state.csrfToken = opts.csrfToken;
  }

  function getCached(md) {
    return state.cache.get(md == null ? '' : String(md));
  }

  function clearCache() {
    state.cache.clear();
    state.inflight.clear();
  }

  function render(md) {
    const key = md == null ? '' : String(md);
    if (state.cache.has(key)) return Promise.resolve(state.cache.get(key));
    if (state.inflight.has(key)) return state.inflight.get(key);
    if (!state.url) {
      return Promise.reject(new Error('MarkdownPreview: url not configured'));
    }
    const body = new URLSearchParams();
    body.set('content', key);
    const p = fetch(state.url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'X-CSRFToken': state.csrfToken,
      },
      body: body.toString(),
    })
      .then(r => {
        if (!r.ok) throw new Error('MarkdownPreview HTTP ' + r.status);
        return r.text();
      })
      .then(html => {
        const out = html || '';
        state.cache.set(key, out);
        state.inflight.delete(key);
        return out;
      })
      .catch(err => {
        state.inflight.delete(key);
        throw err;
      });
    state.inflight.set(key, p);
    return p;
  }

  function renderMany(mds) {
    const list = Array.isArray(mds) ? mds : [];
    return Promise.all(list.map(md => render(md)));
  }

  return {
    configure: configure,
    render: render,
    renderMany: renderMany,
    getCached: getCached,
    clearCache: clearCache,
  };
}));
