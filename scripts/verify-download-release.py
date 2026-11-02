#!/usr/bin/env python3
import json
import re
import sys


def verify(release, feed, tag):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Invalid release tag")
    if release.get("tag_name") != tag or release.get("draft") is not True:
        raise ValueError("The target must be the matching draft release")
    version = tag[1:]
    package = f"FairPlay.Exam-{version}-full.nupkg"
    required = {
        "FairPlay-Mac.dmg", "FairPlay-Mac.zip", "FairPlay-iPad.ipa",
        "FairPlay-Windows-Setup.exe", "FairPlay-Windows.zip",
        "releases.win.json", package,
    }
    assets = release.get("assets", [])
    by_name = {asset["name"]: asset for asset in assets}
    if len(by_name) != len(assets) or not required.issubset(by_name):
        raise ValueError("Release assets are incomplete or duplicated")
    for name in required:
        asset = by_name[name]
        if asset.get("state") != "uploaded" or asset.get("size", 0) <= 0:
            raise ValueError(f"Asset is not ready: {name}")
        if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", asset.get("digest") or ""):
            raise ValueError(f"Asset digest is unavailable: {name}")
    matches = [asset for asset in feed.get("Assets", [])
               if asset.get("PackageId") == "FairPlay.Exam" and asset.get("Type") == "Full"]
    if len(matches) != 1:
        raise ValueError("Windows feed must name one full package")
    entry = matches[0]
    uploaded = by_name[package]
    if entry.get("Version") != version or entry.get("FileName") != package:
        raise ValueError("Windows feed version or package name does not match")
    if entry.get("Size") != uploaded["size"]:
        raise ValueError("Windows package size does not match its feed")
    if str(entry.get("SHA256", "")).lower() != uploaded["digest"].split(":", 1)[1].lower():
        raise ValueError("Windows package digest does not match its feed")
    return sorted(required)


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as file:
            release = json.load(file)
        with open(sys.argv[2]) as file:
            feed = json.load(file)
        names = verify(release, feed, sys.argv[3])
        print(f"Verified {len(names)} uploaded assets and the Windows update feed.")
        by_name = {asset["name"]: asset for asset in release["assets"]}
        for name in names:
            asset = by_name[name]
            print(f"{name}: {asset['size']} bytes, {asset['digest']}")
    except (ValueError, KeyError, IndexError, OSError) as error:
        raise SystemExit(f"Release verification failed: {error}")
