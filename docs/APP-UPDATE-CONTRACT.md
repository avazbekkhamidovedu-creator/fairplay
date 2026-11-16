# App update contract: exam server -> Mac and Windows apps

`GET /api/app/update?platform=mac|windows[&version=<current>]` (alias `/api/app/latest`). Implemented in
`server/app/app_update.py`; the Windows client's copy of the contract is its `docs/UPDATE-CONTRACT.md`.

- No auth. **Never 426**, even for an app below the minimum. Always a `Date` header. Strong `ETag`, `304` with an
  empty body on a matching `If-None-Match`, `Cache-Control: max-age=60`.
- `404` = the server knows no release (nothing configured and GitHub unreachable or off): clients then ask GitHub.
- Fields that are empty are left out of the JSON. Every field is optional for the client.

```json
{
  "platform": "windows",
  "latest_version": "0.8.8",
  "min_version": "0.8.8",
  "feed_url": "/app/windows/",
  "setup_url": "https://dku.speakupai.uz/app/windows/FairPlay-Windows-Setup.exe",
  "setup_sha256": "64 hex",
  "package_sha256": { "FairPlay.Exam-0.8.8-full.nupkg": "64 hex" }
}
```

Mac answer: `latest_version`, `min_version`, and `setup_url` + `setup_sha256` = the `FairPlay-Mac.zip` and its SHA-256.
The Mac app accepts that URL only on the repo's GitHub release path
(`https://github.com/<owner>/fairplay-download/releases/download/v<version>/FairPlay-Mac.zip`) or on the exam server's
own host over https, checks the SHA-256 after the download, then verifies the signature and designated requirement.

Where the values come from (first one wins, field by field):

| field | environment (operator pin) | else |
|---|---|---|
| `latest_version` | `FAIRPLAY_LATEST_APP_VERSION_MAC` / `_WINDOWS` | GitHub release, cached |
| `min_version` | `MIN_APP_VERSION_MAC` / `_WINDOWS` | none |
| Mac `setup_url`, `setup_sha256` | `FAIRPLAY_APP_UPDATE_URL_MAC`, `FAIRPLAY_APP_UPDATE_SHA256_MAC` | GitHub asset `FairPlay-Mac.zip` and its digest |
| Windows `feed_url` | `FAIRPLAY_APP_UPDATE_URL_WINDOWS` | omitted |
| Windows `setup_url` | `FAIRPLAY_APP_UPDATE_SETUP_URL_WINDOWS` | omitted |
| Windows `setup_sha256` | `FAIRPLAY_APP_UPDATE_SHA256_WINDOWS` | omitted |
| Windows `package_sha256` | `FAIRPLAY_APP_UPDATE_PACKAGES_WINDOWS` (JSON object) | omitted |

Windows `feed_url` / `setup_url` are only passed on when they are a path starting with `/` or an https URL on this
server's own host (the request's host or `FAIRPLAY_PUBLIC_URL`'s), without credentials; anything else is dropped.
A hand-pinned Mac version never borrows another release's file from GitHub.

GitHub is asked at most once every 10 minutes (one worker at a time, through the store), the last good answer is kept
when GitHub fails. `FAIRPLAY_APP_UPDATE_GITHUB=owner/repo` (default: the public download repo) or `off`;
optional `GITHUB_TOKEN` for a higher GitHub limit.
