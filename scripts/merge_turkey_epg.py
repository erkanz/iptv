#!/usr/bin/env python3
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

PLAYLIST_URL = "https://iptv-org.github.io/iptv/countries/tr.m3u"
SOURCES = [
    ("GlobeTV Turkey1", "https://raw.githubusercontent.com/globetvapp/epg/main/Turkey/turkey1.xml"),
    ("GlobeTV Turkey2", "https://raw.githubusercontent.com/globetvapp/epg/main/Turkey/turkey2.xml"),
    ("GlobeTV Turkey3", "https://raw.githubusercontent.com/globetvapp/epg/main/Turkey/turkey3.xml"),
    ("GlobeTV Turkey4", "https://raw.githubusercontent.com/globetvapp/epg/main/Turkey/turkey4.xml"),
    ("GlobeTV Turkey5", "https://raw.githubusercontent.com/globetvapp/epg/main/Turkey/turkey5.xml"),
]
OUTPUT = Path("TURKEY_EPG.xml")
UA = "Mozilla/5.0 Telly-Turkey-EPG-Merger/1.0"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def norm(value: str) -> str:
    value = value.casefold()
    value = value.replace("ı", "i").replace("ş", "s").replace("ğ", "g")
    value = value.replace("ü", "u").replace("ö", "o").replace("ç", "c")
    value = re.sub(r"\\b(?:hd|sd|uhd|4k|turkiye|turkey)\\b", " ", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def playlist_channels(content: str) -> tuple[set[str], dict[str, set[str]]]:
    ids = set()
    names: dict[str, set[str]] = {}
    for line in content.splitlines():
        if not line.startswith("#EXTINF:"):
            continue
        mid = re.search(r'tvg-id="([^"]+)"', line)
        if not mid or not mid.group(1).strip():
            continue
        tvg_id = mid.group(1).strip()
        ids.add(tvg_id)
        candidates = set()
        mname = re.search(r'tvg-name="([^"]+)"', line)
        if mname and mname.group(1).strip():
            candidates.add(mname.group(1).strip())
        if "," in line:
            candidates.add(line.rsplit(",", 1)[1].strip())
        # tvg-id itself often contains a useful base name (e.g. TRT1.tr).
        candidates.add(tvg_id.split(".", 1)[0])
        for candidate in candidates:
            key = norm(candidate)
            if key:
                names.setdefault(key, set()).add(tvg_id)
    return ids, names


def channel_names(ch: ET.Element) -> list[str]:
    values = []
    for node in ch.findall("display-name"):
        if node.text and node.text.strip():
            values.append(node.text.strip())
    return values


def target_id_for(ch: ET.Element, wanted: set[str], by_name: dict[str, set[str]]) -> str | None:
    cid = ch.get("id", "")
    if cid in wanted:
        return cid
    matches = set()
    for name in channel_names(ch):
        matches.update(by_name.get(norm(name), set()))
    return next(iter(matches)) if len(matches) == 1 else None


def text_of(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else ""


def main() -> int:
    wanted, wanted_by_name = playlist_channels(fetch(PLAYLIST_URL).decode("utf-8-sig", errors="replace"))
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
        try:
            raw = fetch(source_url)
            root = ET.fromstring(raw.lstrip())
        except Exception as exc:
            print(f"SKIP {source_name}: {type(exc).__name__}: {exc}")
            continue
        source_channels = 0
        source_programs = 0

        source_map: dict[str, str] = {}
        for ch in root.findall("channel"):
            source_id = ch.get("id", "")
            target_id = target_id_for(ch, wanted, wanted_by_name)
            if not target_id:
                continue
            source_map[source_id] = target_id
            if target_id not in channels:
                ch.set("id", target_id)
                channels[target_id] = ch
                source_channels += 1

        for p in root.findall("programme"):
            source_id = p.get("channel", "")
            target_id = source_map.get(source_id)
            if not target_id:
                continue
            p.set("channel", target_id)
            key = (
                target_id,
                p.get("start", ""),
                p.get("stop", ""),
                text_of(p, "title"),
            )
            if key not in programs:
                programs[key] = p
                source_programs += 1

        stats.append((source_name, source_channels, source_programs))

    if not channels or not programs:
        raise RuntimeError("No usable Turkey EPG data from configured sources")

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
