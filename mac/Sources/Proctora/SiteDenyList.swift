import Foundation

/// Client copy of server/app/integrations/site_policy.py: a host here is refused even when
/// the signed list carries it. Keep the two lists identical.
enum SiteDenyList {
    private static let suffixes: [String] = [
        "openai.com", "chatgpt.com", "oaiusercontent.com", "claude.ai", "anthropic.com",
        "gemini.google.com", "bard.google.com", "aistudio.google.com", "notebooklm.google.com",
        "copilot.microsoft.com", "copilot.cloud.microsoft", "bing.com", "perplexity.ai", "poe.com",
        "deepseek.com", "kimi.moonshot.cn", "kimi.com", "moonshot.cn", "doubao.com", "tongyi.aliyun.com",
        "qianwen.aliyun.com", "yiyan.baidu.com", "chatglm.cn", "yuanbao.tencent.com", "character.ai",
        "you.com", "phind.com", "mistral.ai", "groq.com", "x.ai", "grok.com", "meta.ai", "pi.ai",
        "cohere.com", "huggingface.co", "hf.co", "githubcopilot.com", "copilot.github.com",
        "blackbox.ai", "chatbot.theb.ai", "quillbot.com",
        "whatsapp.com", "whatsapp.net", "telegram.org", "telegram.me", "t.me", "wechat.com", "weixin.qq.com",
        "wx.qq.com", "qq.com", "messenger.com", "facebook.com", "instagram.com", "discord.com",
        "discord.gg", "discordapp.com", "slack.com", "teams.microsoft.com", "teams.live.com",
        "signal.org", "line.me", "skype.com", "mail.google.com", "outlook.live.com",
        "outlook.office.com", "chat.google.com", "x.com", "twitter.com",
        "drive.google.com", "docs.google.com", "sheets.google.com", "slides.google.com",
        "keep.google.com", "dropbox.com", "onedrive.live.com", "1drv.ms", "sharepoint.com",
        "box.com", "notion.so", "notion.site", "evernote.com", "pastebin.com", "paste.ee",
        "hastebin.com", "gist.github.com", "gist.githubusercontent.com", "raw.githubusercontent.com",
        "wetransfer.com", "mega.nz", "pan.baidu.com", "weiyun.com", "kdocs.cn", "docs.qq.com",
        "shimo.im", "yuque.com", "feishu.cn", "larksuite.com", "codeshare.io",
        "deepl.com", "duckduckgo.com", "kagi.com", "qwen.ai", "tongyi.com", "z.ai", "metaso.cn", "kimi.ai",
        "manus.im", "felo.ai", "msn.com", "github.dev", "vscode.dev", "replit.com", "codepen.io", "jsfiddle.net",
        "ideone.com", "onlinegdb.com", "godbolt.org", "rentry.co", "dpaste.org", "file.io", "gofile.io",
        "catbox.moe", "notion.com", "icloud.com", "proton.me", "outlook.office365.com", "teams.cloud.microsoft",
        "m365.cloud.microsoft", "zoom.us", "reddit.com", "chegg.com", "wolframalpha.com", "grammarly.com",
        "alipan.com", "quark.cn"
    ]
    private static let exact: Set<String> = [
        "github.com", "www.github.com", "www.google.com", "google.com", "translate.google.com",
        "meet.google.com", "search.brave.com"
    ]
    /// The university's own Box: exact hosts the server's deny-list lets through.
    private static let exceptions: Set<String> = ["duke.app.box.com", "duke.box.com"]

    /// Denied domains a wildcard resource suffix could reach (the suffix itself, or one under it).
    static func deniedDomains(under suffix: String) -> [String] {
        suffixes.filter { $0 == suffix || $0.hasSuffix("." + suffix) }
    }

    /// True when `suffix` equals, or is a parent of, a host denied by exact name (www.google.com, search.brave.com):
    /// a wildcard on it would cover that host.
    static func exactHostIsUnder(_ suffix: String) -> Bool {
        exact.contains { $0 == suffix || $0.hasSuffix("." + suffix) }
    }

    static func isDenied(host rawHost: String) -> Bool {
        let host = rawHost.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        guard !host.isEmpty, !exceptions.contains(host) else { return false }
        if exact.contains(host) { return true }
        return suffixes.contains { host == $0 || host.hasSuffix("." + $0) }
    }
}
