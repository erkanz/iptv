#!/usr/bin/env python3
import json, re, unicodedata, urllib.request
from pathlib import Path

FAMELACK = "https://raw.githubusercontent.com/famelack/famelack-data/main/tv/raw/countries/tr.json"
IPTVORG = "https://iptv-org.github.io/iptv/countries/tr.m3u"
EPG = "https://raw.githubusercontent.com/erkanz/iptv/main/TURKEY_EPG.xml"
OUT = Path("FAMELACK_TR.m3u")

def get_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")

def clean_name(s):
    s = re.sub(r"\s*\([^)]*\)", "", s or "")
    s = re.sub(r"\s*\[[^\]]*\]", "", s)
    s = re.sub(r"\s+SD$", "", s, flags=re.I)
    return s.strip()

def norm(s):
    s = clean_name(s).lower()
    table = str.maketrans({"ı":"i","ğ":"g","ü":"u","ş":"s","ö":"o","ç":"c"})
    s = s.translate(table)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s)

def parse_iptvorg(text):
    meta = {}
    for line in text.splitlines():
        if not line.startswith("#EXTINF"):
            continue
        name = line.rsplit(",", 1)[-1].strip()
        def attr(k):
            m = re.search(rf'{re.escape(k)}="([^"]*)"', line)
            return m.group(1) if m else ""
        meta[norm(name)] = {
            "tvg": attr("tvg-id"),
            "logo": attr("tvg-logo"),
            "group": attr("group-title") or "Türkiye",
        }
    return meta

fam = json.loads(get_text(FAMELACK))
meta = parse_iptvorg(get_text(IPTVORG))

lines = [f'#EXTM3U x-tvg-url="{EPG}"']
count = mapped = 0
for ch in fam:
    streams = (ch.get("sources") or {}).get("streams") or []
    if ch.get("country") != "tr" or ch.get("isGeoBlocked") is True or not streams:
        continue
    url = streams[0]
    if not re.match(r"^https?://", url, re.I):
        continue
    m = meta.get(norm(ch.get("name")), {})
    attrs = []
    if m.get("tvg"):
        attrs.append(f'tvg-id="{m["tvg"]}"')
        mapped += 1
    if m.get("logo"):
        attrs.append(f'tvg-logo="{m["logo"]}"')
    attrs.append(f'group-title="{m.get("group") or "Türkiye"}"')
    lines += [f'#EXTINF:-1 {" ".join(attrs)},{ch["name"]}', url]
    count += 1

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Generated {OUT}: {count} channels, {mapped} with tvg-id mapping")
