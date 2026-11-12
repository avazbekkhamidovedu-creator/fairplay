from app.integrations.dkuoj.lock_sites import build_lock_sites

BOX = "https://duke.box.com/s/abcdefghij1234"


def test_empty_and_absent_are_closed_book():
    closed = {"pages": [], "resources": []}
    assert build_lock_sites([]) == closed
    assert build_lock_sites(None, None, None) == closed
    assert build_lock_sites(["not a site at all"]) == closed


def test_title_url_lines_give_the_url():
    out = build_lock_sites(["Python docs https://docs.python.org/3/library/"])
    assert out["pages"] == ["https://docs.python.org/3/library/"]


def test_plain_host_and_wildcard_expand_to_a_page():
    out = build_lock_sites(["docs.python.org", "*.mozilla.org"])
    assert out["pages"] == ["https://docs.python.org/", "https://mozilla.org/"]


def test_bad_line_is_dropped_alone():
    out = build_lock_sites(["docs.python.org", "bad line here", "https://user@evil.example/x", "*.com", "w3.org"])
    assert out["pages"] == ["https://docs.python.org/", "https://w3.org/"]


def test_denied_hosts_never_appear_from_any_source():
    lines = ["chatgpt.com", "https://chat.openai.com/", "AI https://claude.ai/new", "*.openai.com", "gemini.google.com",
             "copilot.microsoft.com", "web.whatsapp.com", "t.me", "docs.python.org"]
    links = [{"url": "https://chatgpt.com/c/1"}, {"url": "https://claude.ai/"}]
    out = build_lock_sites(lines, links)
    blob = " ".join(out["pages"] + out["resources"])
    for bad in ("chatgpt", "openai", "claude", "gemini", "copilot", "whatsapp", "t.me"):
        assert bad not in blob, bad
    assert out["pages"] == ["https://docs.python.org/"]


def test_box_link_page_appears_and_the_sign_in_chain_is_left_to_the_clients():
    out = build_lock_sites(["docs.python.org"], None, BOX)
    assert out["pages"][0] == "https://duke.app.box.com/s/abcdefghij1234"
    assert not any("*" in r for r in out["pages"])
    # The apps own the chain (Mac BoxSignIn.swift, Windows BoxSignIn.cs): none of its hosts is sent as a resource.
    joined = " ".join(out["resources"])
    for host in ("box", "duosecurity", "shib", "boxcdn", "boxcloud"):
        assert host not in joined, host
    assert "https://duke.app.box.com" not in out["resources"]


def test_box_link_other_than_a_duke_shared_link_is_ignored():
    assert build_lock_sites([], None, "https://evil.example/s/abcdefghij1234") == {"pages": [], "resources": []}
    assert build_lock_sites([], None, "https://duke.box.com/folder/123") == {"pages": [], "resources": []}


def test_cap_of_32_holds_with_resources_counted():
    lines = [f"site{i}.example.org" for i in range(60)]
    out = build_lock_sites(lines, None, BOX)
    assert len(out["pages"]) + len(out["resources"]) == 32
    assert out["pages"][0].startswith("https://duke.app.box.com/s/")
    assert len(build_lock_sites(lines)["pages"]) == 32


def test_duplicates_are_removed():
    out = build_lock_sites(["docs.python.org", "https://docs.python.org/", "DOCS.python.org"])
    assert out["pages"] == ["https://docs.python.org/"]


def test_wildcard_resources_only_for_safe_suffixes():
    from app.integrations.dkuoj import lock_sites as ls
    assert ls._origin("*.boxcloud.com", True) == "https://*.boxcloud.com"
    assert ls._origin("*.boxcloud.com") is None
    for bad in ("*.com", "*.co.uk", "*.org", "*.edu", "*.openai.com", "*.chatgpt.com", "*.*.x.org", "*.", "*.a*b.org"):
        assert ls._origin(bad, True) is None, bad


def test_wildcards_never_reach_pages():
    out = build_lock_sites(["*.python.org", "*.com"])
    assert out["pages"] == ["https://python.org/"]


NEW_SUFFIX = ("deepl.com duckduckgo.com kagi.com qwen.ai tongyi.com z.ai metaso.cn kimi.ai manus.im felo.ai msn.com "
              "github.dev vscode.dev replit.com codepen.io jsfiddle.net ideone.com onlinegdb.com godbolt.org rentry.co "
              "dpaste.org file.io gofile.io catbox.moe notion.com icloud.com proton.me outlook.office365.com "
              "teams.cloud.microsoft m365.cloud.microsoft zoom.us reddit.com chegg.com wolframalpha.com grammarly.com "
              "alipan.com quark.cn").split()
NEW_EXACT = "www.google.com google.com translate.google.com meet.google.com search.brave.com".split()


def test_new_suffix_rules_deny_host_and_subdomains():
    from app.integrations.site_policy import denied
    for d in NEW_SUFFIX:
        assert denied(d), d
        assert denied("www." + d), d
        assert denied("*." + d), d


def test_new_exact_rules_deny_only_that_host():
    from app.integrations.site_policy import denied
    for h in NEW_EXACT:
        assert denied(h), h
    for ok in ("scholar.google.com", "developers.google.com", "docs.python.org", "docs.github.com", "w3.org"):
        assert denied(ok) is None, ok


def test_box_exceptions_still_pass():
    from app.integrations.site_policy import denied
    assert denied("duke.app.box.com") is None and denied("duke.box.com") is None
    assert denied("other.box.com")


def test_new_denied_hosts_never_reach_lock_sites():
    out = build_lock_sites(["www.google.com", "translate.google.com", "reddit.com", "deepl.com", "docs.python.org"])
    assert out["pages"] == ["https://docs.python.org/"]


def test_resources_never_take_the_last_page_slot():
    from app.integrations.dkuoj import lock_sites as ls
    lines = [f"site{i}.example.org" for i in range(5)]
    out = build_lock_sites(lines, None, BOX)
    assert out["pages"] and len(out["resources"]) <= 31
    assert len(out["pages"]) + len(out["resources"]) <= 32


def test_a_link_with_a_quote_is_refused_like_the_oj_does():
    out = build_lock_sites(["Docs https://docs.python.org/it's", "w3.org"])
    assert out["pages"] == ["https://w3.org/"]


def test_no_box_sign_in_host_is_emitted_for_any_box_page():
    for out in (build_lock_sites([], None, BOX),
                build_lock_sites(["https://duke.box.com/s/abcdefghij1234", "docs.python.org"], None, None)):
        assert out["pages"] and out["pages"][0].endswith("/s/abcdefghij1234")
        assert not any(k in r for r in out["resources"] for k in ("box", "duosecurity", "shib"))


def test_box_sign_in_hosts_are_not_sites_of_their_own():
    out = build_lock_sites(["account.box.com", "api.box.com"], None, None)
    assert out == {"pages": [], "resources": []}
    out = build_lock_sites(["https://duke.account.box.com/login"], None, None)
    assert out["pages"] == []


def test_the_box_page_and_teacher_pages_are_never_cut_for_resources():
    teacher = [f"https://site{i}.example.org/page" for i in range(25)]
    out = build_lock_sites(teacher, None, BOX)
    assert out["pages"][0] == "https://duke.app.box.com/s/abcdefghij1234"
    assert all(t in out["pages"] for t in teacher)
    assert len(out["pages"]) + len(out["resources"]) <= 32
    full = [f"https://site{i}.example.org/page" for i in range(40)]
    out = build_lock_sites(full, None, BOX)
    assert out["pages"][0] == "https://duke.app.box.com/s/abcdefghij1234"
    assert len(out["pages"]) == 32 and out["resources"] == []
    assert out["pages"][1:] == full[:31]
