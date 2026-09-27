#!/usr/bin/env python3
import concurrent.futures
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PLAYLIST_URL = "https://iptv-org.github.io/iptv/countries/tr.m3u"
CHANNELS_URL = "https://raw.githubusercontent.com/iptv-org/epg/master/sites/tvplus.com.tr/tvplus.com.tr.channels.xml"
AUTH_URL = "https://izmaottvsc14.tvplus.com.tr:33207/EPG/JSON/Authenticate"
EPG_URL = "https://izmaottvsc14.tvplus.com.tr:33207/EPG/JSON/PlayBillList"
OUTPUT = Path("TURKEY_EPG.xml")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
ISTANBUL = ZoneInfo("Europe/Istanbul")
DAYS = 2
WORKERS = 8


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def post_json(url: str, payload: dict, cookie: str | None = None) -> tuple[bytes, list[str]]:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
    }
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(), r.headers.get_all("Set-Cookie") or []


def playlist_ids(content: str) -> set[str]:
    ids = set()
    for line in content.splitlines():
        if not line.startswith("#EXTINF:"):
            continue
        m = re.search(r'tvg-id="([^"]+)"', line)
        if m and m.group(1).strip():
            ids.add(m.group(1).strip())
    return ids


def authenticate() -> str:
    _, cookies = post_json(
        AUTH_URL,
        {
            "terminaltype": "webtv",
            "terminalvendor": UA,
            "osversion": "Win32",
            "userType": "3",
            "utcEnable": "1",
            "timezone": "Europe/Istanbul",
        },
    )
    if not cookies:
        raise RuntimeError("TV+ authentication returned no Set-Cookie header")
    return "; ".join(c.split(";", 1)[0] for c in cookies)


def parse_api_time(value: str) -> datetime:
    # Example: 2026-04-22 02:30:00 UTC+03:00
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S UTC%z")


def xmltv_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S %z")


def add_text(parent: ET.Element, tag: str, value) -> None:
    if value is None:
        return
    if isinstance(value, list):
        value = ", ".join(str(x) for x in value if x)
    value = str(value).strip()
    if not value:
        return
    ET.SubElement(parent, tag).text = value


def load_channels(wanted: set[str]) -> list[dict]:
    root = ET.fromstring(fetch(CHANNELS_URL))
    channels = []
    for ch in root.findall("channel"):
        xmltv_id = (ch.get("xmltv_id") or "").strip()
        site_id = (ch.get("site_id") or "").strip()
        if xmltv_id not in wanted or not site_id:
            continue
        channels.append(
            {
                "xmltv_id": xmltv_id,
                "site_id": site_id,
                "name": (ch.text or xmltv_id).strip(),
                "logo": (ch.get("logo") or "").strip(),
            }
        )
    return channels


def grab_channel(channel: dict, cookie: str, start_day: datetime) -> tuple[dict, list[dict], list[str]]:
    programs = []
    errors = []
    for offset in range(DAYS):
        begin = (start_day + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = begin + timedelta(days=1)
        payload = {
            "type": "2",
            "channelid": channel["site_id"],
            "begintime": begin.strftime("%Y%m%d%H%M%S"),
            "endtime": end.strftime("%Y%m%d%H%M%S"),
            "isFillProgram": 1,
        }
        try:
            raw, _ = post_json(EPG_URL, payload, cookie)
            data = json.loads(raw.decode("utf-8", errors="replace"))
            items = data.get("playbilllist") if isinstance(data, dict) else None
            if isinstance(items, list):
                programs.extend(items)
        except Exception as exc:
            errors.append(f"{channel['xmltv_id']} day+{offset}: {type(exc).__name__}: {exc}")
    return channel, programs, errors


def main() -> int:
    wanted = playlist_ids(fetch(PLAYLIST_URL).decode("utf-8-sig", errors="replace"))
    if not wanted:
        raise RuntimeError("No tvg-id values found in Turkey playlist")

    channels = load_channels(wanted)
    if not channels:
        raise RuntimeError("No TV+ channels match the Turkey playlist")

    cookie = authenticate()
    today = datetime.now(ISTANBUL)

    out = ET.Element(
        "tv",
        {
            "generator-info-name": "erkanz/iptv live TV+ Turkey EPG",
            "generator-info-url": "https://github.com/erkanz/iptv",
        },
    )

    channel_nodes: dict[str, ET.Element] = {}
    for ch in channels:
        node = ET.SubElement(out, "channel", {"id": ch["xmltv_id"]})
        ET.SubElement(node, "display-name").text = ch["name"]
        if ch["logo"]:
            ET.SubElement(node, "icon", {"src": ch["logo"]})
        channel_nodes[ch["xmltv_id"]] = node

    all_programs = []
    all_errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(grab_channel, ch, cookie, today) for ch in channels]
        for future in concurrent.futures.as_completed(futures):
            ch, items, errors = future.result()
            all_errors.extend(errors)
            for item in items:
                try:
                    start = parse_api_time(item.get("starttime", ""))
                    stop = parse_api_time(item.get("endtime", ""))
                    if stop <= start:
                        continue
                    all_programs.append((ch["xmltv_id"], start, stop, item))
                except Exception as exc:
                    all_errors.append(f"{ch['xmltv_id']} parse: {type(exc).__name__}: {exc}")

    # De-duplicate programs returned across day boundaries.
    unique = {}
    for cid, start, stop, item in all_programs:
        key = (cid, start.isoformat(), stop.isoformat(), str(item.get("name", "")))
        unique[key] = (cid, start, stop, item)

    programs = sorted(unique.values(), key=lambda x: (x[0], x[1], x[2]))
    for cid, start, stop, item in programs:
        p = ET.SubElement(
            out,
            "programme",
            {
                "start": xmltv_time(start),
                "stop": xmltv_time(stop),
                "channel": cid,
            },
        )
        add_text(p, "title", item.get("name"))
        add_text(p, "desc", item.get("introduce"))
        add_text(p, "category", item.get("genres"))
        picture = item.get("picture") or {}
        if isinstance(picture, dict):
            icon = picture.get("icon")
            if isinstance(icon, str) and icon.strip():
                ET.SubElement(p, "icon", {"src": icon.split(",", 1)[0].strip()})

    if not programs:
        raise RuntimeError("TV+ returned zero current programmes")

    ET.indent(out, space="  ")
    ET.ElementTree(out).write(OUTPUT, encoding="utf-8", xml_declaration=True)

    starts = [p[1] for p in programs]
    stops = [p[2] for p in programs]
    covered = len({p[0] for p in programs})

    print(f"Turkey playlist tvg-id count : {len(wanted)}")
    print(f"TV+ matched channels         : {len(channels)}")
    print(f"Channels with programme data : {covered}")
    print(f"Programme count              : {len(programs)}")
    print(f"EPG start range              : {min(starts).isoformat()} -> {max(starts).isoformat()}")
    print(f"EPG stop max                 : {max(stops).isoformat()}")
    print(f"Output                       : {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
    if all_errors:
        print(f"Non-fatal grab/parse errors  : {len(all_errors)}")
        for err in all_errors[:20]:
            print(f"WARN {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
