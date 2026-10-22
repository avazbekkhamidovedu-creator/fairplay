# FairPlay logo

`mark.svg` is the source: a white circle split into two equal halves on a blue (#2F4BD8)
rounded square (64-unit design, corner radius 12.3, circle radius 18.5, gap 4.6).

Made from it (keep them in sync when the logo changes):

| File | Used by |
|---|---|
| `FairPlay.icns` | Mac app icon (`mac/build.sh` copies it into the bundle) |
| `ipad-appicon-1024.png` | iPad app icon (`ios/FairPlay/Assets.xcassets/AppIcon.appiconset`) |
| `FairPlay.ico` | Windows app icon (copy in the fairplay-windows repo, `src/FairPlay.Shell/FairPlay.ico`) |
| `mark-1024.png`, `mark-512.png` | Store pages, documents |

The apps also draw the mark in code (web `LogoMark`, Mac/iPad `BrandMark`, Windows XAML strip),
so it stays sharp at every size.
