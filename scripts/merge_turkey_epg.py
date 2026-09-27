#!/usr/bin/env python3
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

PLAYLIST_URL = "https://iptv-org.github.io/iptv/countries/tr.m3u"
SOURCES = [
    ("TV+", "https://iptv-org.github.io/epg/guides/tr/tvplus.com.tr.xml.gz"),
    ("Digiturk", "https://iptv-org.github.io/epg/guides/tr/digiturk.com.tr.xml.gz"),
    ("D-Smart", "https://iptv-org.github.io/epg/guides/tr/dsmart.com.tr.xml.gz"),
]
OUTPUT = Path("TURKEY_EPG.xml")
UA = "Mozilla/5.0 Telly-Turkey-EPG-Merger/1.0"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def playlist_ids(content: str) -> set[str]:
    ids = set()
    for line in content.splitlines():
        if not line.startswith("#EXTINF:"):
            continue
        m = re.search(r'tvg-id="([^"]+)"', line)
        if m and m.group(1).strip():
            ids.add(m.group(1).strip())
    return ids


def text_of(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else ""


def main() -> int:
    wanted = playlist_ids(fetch(PLAYLIST_URL).decode("utf-8-sig", errors="replace"))
    if not wanted:
        raise RuntimeError("No tvg-id values found in Turkey playlist")

    out = ET.Element("tv", {
        "generator-info-name": "erkanz/iptv Turkey EPG merger",
        "generator-info-url": "https://github.com/erkanz/iptv",
    })

    channels: dict[str, ET.Element] = {}
    programs: dict[tuple[str, str, str, str], ET.Element] = {}
    stats = []

    for source_name, source_url in SOURCES:
        raw = fetch(source_url)
        root = ET.fromstring(raw)
        source_channels = 0
        source_programs = 0

        for ch in root.findall("channel"):
            cid = ch.get("id", "")
            if cid in wanted and cid not in channels:
                channels[cid] = ch
                source_channels += 1

        for p in root.findall("programme"):
            cid = p.get("channel", "")
            if cid not in wanted:
                continue
            key = (
                cid,
                p.get("start", ""),
                p.get("stop", ""),
                text_of(p, "title"),
            )
            if key not in programs:
                programs[key] = p
                source_programs += 1

        stats.append((source_name, source_channels, source_programs))

    for cid in sorted(channels):
        out.append(channels[cid])

    def prog_key(p: ET.Element):
        return (p.get("channel", ""), p.get("start", ""), p.get("stop", ""), text_of(p, "title"))

    for p in sorted(programs.values(), key=prog_key):
        out.append(p)

    ET.indent(out, space="  ")
    tree = ET.ElementTree(out)
    tree.write(OUTPUT, encoding="utf-8", xml_declaration=True)

    covered = len(channels)
    print(f"Turkey playlist tvg-id count : {len(wanted)}")
    print(f"EPG channel coverage         : {covered}")
    print(f"Merged programme count       : {len(programs)}")
    for name, c, p in stats:
        print(f"{name:10s}: +{c:3d} channels, +{p:6d} programmes")
    print(f"Output                       : {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
