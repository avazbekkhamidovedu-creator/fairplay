import Foundation

/// 2026-10-24: students add their own files (open-book materials) in the normal, unlocked app,
/// in a window of a few days before the exam. Picking a file (the open panel, a file dropped
/// on the page) is allowed only then: never during the pre-exam lock or the exam lock, and
/// only for the exam server's own page. The server and the page check size and type.
/// Same rule as the iPad's FairPlayCore FileAccess.swift and Windows' FileAccess.cs.
enum FilePicker {

    /// May a file be picked now? `locked` is true for the pre-exam lock and the exam lock
    /// alike; `fromServer`: asked by the exam server's own page (main frame).
    static func allowed(locked: Bool, fromServer: Bool) -> Bool { !locked && fromServer }

    /// What the app sends the page when it refuses a file ({type:"fileRefused"}).
    static var refusedMessage: [String: Any] { ["type": "fileRefused", "reason": "locked"] }

    /// The exam server's origin as the page sees it ("https://host" or "http://host:8000"),
    /// only the characters an origin has (it goes into a script).
    static func origin(_ server: URL) -> String {
        let scheme = (server.scheme ?? "https").lowercased()
        let host = (server.host ?? "").lowercased()
        let defaultPort = scheme == "https" ? 443 : 80
        let port = server.port.map { $0 == defaultPort ? "" : ":\($0)" } ?? ""
        return "\(scheme)://\(host)\(port)".filter { $0.isLetter || $0.isNumber || ":/.-[]".contains($0) }
    }

    /// The page guard for files, in every frame at document start. A file input click (also
    /// `input.click()` and `showPicker()`) and a file dropped on the page are refused unless
    /// the app said files are allowed (`window.__fairplayFiles(true)`, main frame of the exam
    /// server only, see `setScript`). Refused by default: a page that loads during a lock never
    /// gets a moment where files work. Frames of other sites never pick or drop files. A
    /// refusal in the exam page asks the app to send {type:"fileRefused"} (`post`: how the page
    /// posts to the app). The Mac's open panel refuses on its own too (runOpenPanel).
    static func guardScript(origin: String, post: String) -> String {
        """
        (function () {
          var top = true;
          try { top = window.top === window.self; } catch (e) { top = false; }
          var server = location.origin === '\(origin)';
          var allowed = false;
          if (top && server) {
            try {
              Object.defineProperty(window, '__fairplayFiles', { value: function (on) { allowed = on === true; },
                configurable: false, enumerable: false, writable: false });
            } catch (e) {}
          }
          function ok() { return allowed && top && server; }
          function tell() { if (top && server) { try { \(post)({ action: 'fileRefused' }); } catch (e) {} } }
          function isFile(t) { return !!t && t.tagName === 'INPUT' && String(t.type).toLowerCase() === 'file'; }
          window.addEventListener('click', function (e) {
            if (isFile(e.target) && !ok()) { e.preventDefault(); e.stopImmediatePropagation(); tell(); }
          }, true);
          try {
            var click = HTMLInputElement.prototype.click;
            HTMLInputElement.prototype.click = function () {
              if (isFile(this) && !ok()) { tell(); return; }
              return click.apply(this, arguments);
            };
            var pick = HTMLInputElement.prototype.showPicker;
            if (pick) HTMLInputElement.prototype.showPicker = function () {
              if (isFile(this) && !ok()) { tell(); throw new DOMException('Files cannot be added now', 'NotAllowedError'); }
              return pick.apply(this, arguments);
            };
          } catch (e) {}
          function hasFiles(e) {
            var t = e.dataTransfer && e.dataTransfer.types;
            if (!t) return false;
            for (var i = 0; i < t.length; i++) if (t[i] === 'Files') return true;
            return false;
          }
          ['dragenter', 'dragover', 'drop'].forEach(function (k) {
            window.addEventListener(k, function (e) {
              if (!hasFiles(e) || ok()) return;
              e.preventDefault(); e.stopImmediatePropagation();
              try { e.dataTransfer.dropEffect = 'none'; } catch (x) {}
              if (k === 'drop') tell();
            }, true);
          });
        })();
        """
    }

    /// Turns files on or off in the exam page on screen (main frame). Harmless on a page
    /// without the guard.
    static func setScript(allowed: Bool) -> String {
        "if (window.__fairplayFiles) { window.__fairplayFiles(\(allowed ? "true" : "false")); }"
    }
}

/// 2026-10-24: links in the allowed-site pane (an <iframe> of the exam page) that would open a
/// new window or tab, or replace the exam page, open in the pane instead: a click on a link or
/// a form with target _blank / _new / _top, and window.open(url). Where they go is then checked
/// like any frame navigation (AllowedSites.frameAllowed): an allowed site opens, anything else
/// is blocked. Only in frames of other sites (https, not the exam server); the exam page itself
/// keeps window.open returning null and no window ever opens.
enum SiteFrameScript {

    static func make(origin: String) -> String {
        """
        (function () {
          var top = true;
          try { top = window.top === window.self; } catch (e) { top = false; }
          var site = !top && location.protocol === 'https:' && location.origin !== '\(origin)';
          window.open = function (u) {
            if (site && u !== undefined && u !== null && String(u) !== '') {
              try { location.assign(new URL(String(u), location.href).href); } catch (e) {}
            }
            return null;
          };
          if (!site) return;
          function here(el) {
            var t = String(el.getAttribute('target') || '').toLowerCase();
            if (t === '_blank' || t === '_new' || t === '_top') el.setAttribute('target', '_self');
          }
          document.addEventListener('click', function (e) {
            var a = e.target && e.target.closest ? e.target.closest('a[target], area[target]') : null;
            if (a) here(a);
          }, true);
          document.addEventListener('submit', function (e) {
            if (e.target && e.target.getAttribute) here(e.target);
          }, true);
        })();
        """
    }
}

/// 2026-10-24: sign-ins on allowed sites (e.g. Duke Box) live in the app's web data, so a
/// student who signs in before the lock is still signed in during the exam. They are kept for
/// the day and cleared when the student signs out of FairPlay. Only the other sites' data is
/// cleared: the exam server's (its localStorage keeps offline answers) is never touched.
enum SiteData {

    /// UserDefaults key: the day ("yyyy-MM-dd") the other sites' data was last cleared.
    static let dayKey = "SiteDataClearedDay"

    static func isLoginPath(_ path: String) -> Bool {
        let p = path.lowercased()
        return p == "/student/login" || p == "/student/login/"
    }

    /// A student page shown only to a signed-in student (not the sign-in page).
    static func isSignedInPath(_ path: String) -> Bool {
        let p = path.lowercased()
        return (p == "/student" || p.hasPrefix("/student/")) && !isLoginPath(path)
    }

    /// The sign-in page shown after a signed-in page, while unlocked: the student signed out
    /// (or the FairPlay sign-in ran out). Never while locked.
    static func isSignOut(sawSignedInPage: Bool, path: String, locked: Bool) -> Bool {
        sawSignedInPage && !locked && isLoginPath(path)
    }

    /// At launch: once per day, and never when the last run ended during a lock (that exam goes
    /// on and keeps its sign-ins).
    static func shouldClearAtLaunch(lastDay: String?, today: String, endedLocked: Bool) -> Bool {
        !endedLocked && lastDay != today
    }

    /// Data of `domain` (a cookie domain or WebKit's record name, the registrable domain) is
    /// kept: it is the exam server's host or a parent domain of it.
    static func keeps(domain: String, serverHost: String) -> Bool {
        var d = domain.trimmingCharacters(in: .whitespaces).lowercased()
        if d.hasPrefix(".") { d.removeFirst() }
        let s = serverHost.lowercased()
        guard !d.isEmpty, !s.isEmpty else { return false }
        return s == d || s.hasSuffix("." + d)
    }

    static func day(_ date: Date, calendar: Calendar = .current) -> String {
        let c = calendar.dateComponents([.year, .month, .day], from: date)
        func two(_ n: Int) -> String { n < 10 ? "0\(n)" : "\(n)" }
        return "\(c.year ?? 0)-\(two(c.month ?? 0))-\(two(c.day ?? 0))"
    }
}
