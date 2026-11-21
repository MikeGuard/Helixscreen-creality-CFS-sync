#!/usr/data/moonraker/moonraker-env/bin/python
"""
helix_cfs_sync - copies HelixScreen's CFS slot info (Moonraker DB 'lane_data')
into Creality's backend (websocket :9999, 'modifyMaterial'), so Creality Print,
the Creality app/cloud and Orca's CrealityPrint host see the loaded filaments.

One-way: HelixScreen -> Creality. RFID slots (state 2) are never touched.
Nothing is sent while a print is running or paused.

Usage:  helix_cfs_sync.py            run forever (service)
        helix_cfs_sync.py --once     one sync pass
        helix_cfs_sync.py --dry-run  show what would be sent, send nothing
        helix_cfs_sync.py --dump     print Creality's current boxsInfo
"""
import argparse
import asyncio
import json
import logging
import sys
import time
import urllib.request

from tornado import websocket

MOONRAKER = "http://127.0.0.1:7125"
CREALITY_WS = "ws://127.0.0.1:9999"
LOG_FILE = "/usr/data/printer_data/logs/helix_cfs_sync.log"
INTERVAL_S = 15          # how often to check
RESEND_AFTER_S = 600     # if Creality ignores an update, retry at most every 10 min

# Creality "Generic" materials, ids from the printer's material_database.json:
# key -> (database id, name, minTemp, maxTemp, pressure advance)
PRESETS = {
    "PLA":      ("00001", "Generic PLA",      190, 240, "0.04"),
    "PLA-SILK": ("00002", "Generic PLA-Silk", 190, 240, "0.052"),
    "PETG":     ("00003", "Generic PETG",     220, 270, "0.08"),
    "ABS":      ("00004", "Generic ABS",      240, 280, "0.06"),
    "TPU":      ("00005", "Generic TPU",      200, 250, "0.02"),
    "PLA-CF":   ("00006", "Generic PLA-CF",   190, 240, "0.03"),
    "ASA":      ("00007", "Generic ASA",      240, 280, "0.044"),
    "PA":       ("00008", "Generic PA",       240, 260, "0.01"),
    "PA-CF":    ("00009", "Generic PA-CF",    260, 300, "0.01"),
    "BVOH":     ("00010", "Generic BVOH",     200, 220, "0.02"),
    "PVA":      ("00011", "Generic PVA",      215, 225, "0.02"),
    "HIPS":     ("00012", "Generic HIPS",     220, 250, "0.02"),
    "PET-CF":   ("00013", "Generic PET-CF",   280, 320, "0.02"),
    "PETG-CF":  ("00014", "Generic PETG-CF",  240, 260, "0.02"),
    "PA6-CF":   ("00015", "Generic PA6-CF",   280, 300, "0.02"),
    "PAHT-CF":  ("00016", "Generic PAHT-CF",  300, 320, "0.02"),
    "PPS":      ("00017", "Generic PPS",      320, 350, "0.02"),
    "PPS-CF":   ("00018", "Generic PPS-CF",   305, 320, "0.02"),
    "PP":       ("00019", "Generic PP",       220, 250, "0.02"),
    "PET":      ("00020", "Generic PET",      210, 230, "0.02"),
    "PC":       ("00021", "Generic PC",       250, 270, "0.05"),
}
ALIASES = {
    "PLA+": "PLA", "PLA PLUS": "PLA", "PLA-PLUS": "PLA", "PLA-BASIC": "PLA", "PLA BASIC": "PLA",
    "PLA MATTE": "PLA", "PLA-MATTE": "PLA", "MATTE PLA": "PLA", "HYPER PLA": "PLA", "PLA-HS": "PLA",
    "SILK": "PLA-SILK", "SILK PLA": "PLA-SILK", "PLA SILK": "PLA-SILK",
    "PETG+": "PETG", "PETG-HF": "PETG", "ABS+": "ABS", "NYLON": "PA", "PA6": "PA", "PA12": "PA",
    "PA12-CF": "PA-CF", "PET-GF": "PET-CF", "TPU 95A": "TPU", "TPU-95A": "TPU", "TPE": "TPU",
}

log = logging.getLogger("helix_cfs_sync")


def setup_logging(to_file):
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(fmt)
    log.addHandler(h)
    if to_file:
        try:
            fh = logging.FileHandler(LOG_FILE)
            fh.setFormatter(fmt)
            log.addHandler(fh)
        except OSError:
            pass


def http_json(path):
    with urllib.request.urlopen(MOONRAKER + path, timeout=5) as r:
        return json.loads(r.read().decode())


def resolve_type(material, product):
    m = (material or "").strip().upper().replace("_", "-")
    p = (product or "").strip().upper()
    if m.startswith("PLA") and ("SILK" in m or "SILK" in p):
        return "PLA-SILK"
    for cand in (m, m.replace(" ", "-")):
        if cand in PRESETS:
            return cand
        if cand in ALIASES:
            return ALIASES[cand]
    m2 = m.replace(" ", "-")
    for k in sorted(PRESETS, key=len, reverse=True):   # e.g. "PETG-TRANSLUCENT" -> PETG
        if m2.startswith(k):
            return k
    return None


def hex6(color):
    c = "".join(ch for ch in str(color or "") if ch in "0123456789abcdefABCDEF")
    return c[-6:].upper() if len(c) >= 6 else ""


def desired_for_lane(lane):
    key = resolve_type(lane.get("material") or lane.get("helix_material"), lane.get("helix_product_name"))
    if not key:
        return None, "unknown material %r" % (lane.get("material"),)
    color = hex6(lane.get("color"))
    if not color:
        return None, "no color"
    rfid, name, tmin, tmax, pressure = PRESETS[key]
    noz = lane.get("nozzle_temp")
    if isinstance(noz, (int, float)) and 150 <= noz <= 400:
        tmin, tmax = min(tmin, int(noz)), max(tmax, int(noz))
    return {
        "rfid": rfid,
        "type": "PLA" if key == "PLA-SILK" else key,
        "vendor": "Generic",
        "name": name,
        "color": "#0" + color,
        "minTemp": tmin,
        "maxTemp": tmax,
        "pressure": pressure,
    }, "%s %s %s -> %s" % (lane.get("vendor") or lane.get("vendor_name") or "?", lane.get("helix_product_name") or lane.get("material") or "?", "#" + color, name)


def matches(current, want):
    if not current or int(current.get("state") or 0) == 0:
        return False
    return (str(current.get("type", "")).upper() == want["type"]
            and str(current.get("name", "")).strip().lower() == want["name"].lower()
            and hex6(current.get("color")) == hex6(want["color"]))


class CrealityWS:
    def __init__(self):
        self.c = None

    async def __aenter__(self):
        self.c = await websocket.websocket_connect(CREALITY_WS, connect_timeout=5)
        return self

    async def __aexit__(self, *a):
        try:
            self.c.close()
        except Exception:
            pass

    def send(self, obj):
        self.c.write_message(json.dumps(obj))

    async def read_key(self, key, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                m = await asyncio.wait_for(self.c.read_message(), timeout=max(0.1, end - time.time()))
            except asyncio.TimeoutError:
                break
            if m is None:
                raise ConnectionError("websocket closed")
            if isinstance(m, bytes):
                continue
            if "heart_beat" in m:
                self.c.write_message("ok")
                continue
            try:
                d = json.loads(m)
            except ValueError:
                continue
            if isinstance(d, dict) and key in d:
                return d[key]
        return None

    async def boxs_info(self):
        self.send({"method": "get", "params": {"boxsInfo": 1}})
        return await self.read_key("boxsInfo")


def cfs_boxes(info):
    boxes = {}
    for b in (info or {}).get("materialBoxs", []) or []:
        if isinstance(b, dict) and b.get("type") == 0 and isinstance(b.get("id"), int):
            boxes[b["id"]] = {m.get("id"): m for m in (b.get("materials") or []) if isinstance(m, dict)}
    return boxes


last_sent = {}   # (box, idx) -> (json, time)
_last_note = {}


def note(key, level, msg, *args):
    """log only when the message for this key changes (avoids spamming every 15 s)"""
    text = msg % args if args else msg
    if _last_note.get(key) != text:
        _last_note[key] = text
        log.log(level, text)


async def sync_pass(dry_run=False):
    try:
        st = http_json("/printer/objects/query?print_stats=state")["result"]["status"]["print_stats"]["state"]
    except Exception as e:
        note("moon", logging.WARNING, "moonraker not reachable: %s", e)
        return
    if st in ("printing", "paused"):
        return
    try:
        lanes = http_json("/server/database/item?namespace=lane_data")["result"]["value"] or {}
    except Exception as e:
        note("lanes", logging.WARNING, "no HelixScreen lane_data yet: %s", e)
        return

    async with CrealityWS() as ws:
        boxes = cfs_boxes(await ws.boxs_info())
        if boxes:
            note("boxes", logging.INFO, "Creality backend OK: CFS boxes %s", sorted(boxes))
        if not boxes:
            note("boxes", logging.WARNING, "Creality backend returned no CFS boxes (is master-server/web-server running?)")
            return
        sent = []
        for lane in lanes.values():
            try:
                idx = int(lane.get("lane"))
            except (TypeError, ValueError):
                continue
            box, slot = idx // 4 + 1, idx % 4
            if box not in boxes:
                continue                       # external spool lane or missing box
            cur = boxes[box].get(slot)
            label = "%d%s" % (box, "ABCD"[slot])
            if cur and int(cur.get("state") or 0) == 2:
                continue                       # RFID spool: Creality already knows it
            want, desc = desired_for_lane(lane)
            if want is None:
                note(label, logging.INFO, "%s: skipped (%s)", label, desc)
                continue
            if matches(cur, want):
                continue
            key, wj = (box, slot), json.dumps(want, sort_keys=True)
            prev = last_sent.get(key)
            if prev and prev[0] == wj and time.time() - prev[1] < RESEND_AFTER_S:
                continue
            payload = {"method": "set", "params": {"modifyMaterial": dict({"boxId": box, "id": slot}, **want)}}
            if dry_run:
                log.info("%s: WOULD SEND %s | %s", label, desc, json.dumps(payload))
                continue
            ws.send(payload)
            last_sent[key] = (wj, time.time())
            sent.append(key)
            log.info("%s: sent %s", label, desc)
            await asyncio.sleep(0.5)
        if sent:
            await asyncio.sleep(4)
            after = cfs_boxes(await ws.boxs_info())
            for (box, slot) in sent:
                wj = last_sent[(box, slot)][0]
                cur = after.get(box, {}).get(slot)
                ok = matches(cur, json.loads(wj))
                log.info("%d%s: verify %s (state=%s type=%s name=%s color=%s)", box, "ABCD"[slot],
                         "OK" if ok else "pending (re-checked next pass)",
                         cur and cur.get("state"), cur and cur.get("type"),
                         cur and cur.get("name"), cur and cur.get("color"))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dump", action="store_true")
    a = ap.parse_args()
    setup_logging(to_file=not (a.dump or a.dry_run))
    if a.dump:
        async with CrealityWS() as ws:
            print(json.dumps(await ws.boxs_info(), indent=1))
        return
    if a.once or a.dry_run:
        await sync_pass(dry_run=a.dry_run)
        return
    log.info("helix_cfs_sync started")
    while True:
        try:
            await sync_pass()
        except Exception as e:
            log.warning("sync error: %s", e)
        await asyncio.sleep(INTERVAL_S)


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
