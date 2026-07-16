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

  it('serializes headings from HTML', () => {
    const md = RichEditor.htmlToMd('<h1>Title</h1><h2>Sub</h2><p>body</p>');
    assert.match(md, /^# Title$/m);
    assert.match(md, /^## Sub$/m);
    assert.match(md, /body/);
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
});

describe('RichEditor.html markup factory', () => {
  it('builds editor markup with data-editor name', () => {
    const markup = RichEditor.html('stem', { placeholder: 'hi' });
    assert.match(markup, /data-editor="stem"/);
    assert.match(markup, /contenteditable="true"/);
    assert.match(markup, /data-placeholder="hi"/);
  });
});
