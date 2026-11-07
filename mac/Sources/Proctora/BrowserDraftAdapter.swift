import Foundation

/// Site-specific answer adapters. The shared DOM is read in WebKit's isolated world.
/// Unsupported editors are not scraped; login controls never match these selectors.
enum BrowserDraftAdapter {
    static let collect = #"""
    const w = document.querySelector('#workspace[data-mode="exam"],#workspace[data-mode="assignment"]');
    if (w && location.origin === allowedOrigin) {
      const user = w.dataset.nativeUser;
      if (!user || !w.dataset.contextId || !w.dataset.problem || w.dataset.isFile === '1') return null;
      const fields = {};
      for (const el of w.querySelectorAll('textarea[name="source"],textarea[name="answer"],input[name^="fb_"],textarea[name^="fb_"],input[name^="blank_"],textarea[name^="blank_"],input[type="radio"],input[type="checkbox"],select[name="language"]')) {
        if (!el.name || el.type === 'password') continue;
        const key = (el.type === 'radio' || el.type === 'checkbox') ? el.name + ':' + el.value : el.name;
        fields[key] = (el.type === 'radio' || el.type === 'checkbox') ? String(el.checked) : el.value;
      }
      if (!Object.keys(fields).length) return null;
      return {origin:location.origin,user,context:w.dataset.contextId,problem:w.dataset.problem,fields};
    }
    return null;
    """#

    static let restore = #"""
    const w = document.querySelector('#workspace');
    if (!w || location.origin !== origin || w.dataset.nativeUser !== user || w.dataset.contextId !== context || w.dataset.problem !== problem) return false;
    // The site's adapter updates its actual editors and draft pipeline; never submit.
    window.dispatchEvent(new CustomEvent('fairplay-restore-draft', {detail: fields}));
    return true;
    """#
}
