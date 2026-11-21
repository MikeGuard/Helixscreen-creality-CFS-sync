# Helixscreen-creality-CFS-sync

Keep **Creality Print, the Creality app/cloud and OrcaSlicer** in sync with the CFS filaments you set in **[HelixScreen](https://github.com/prestonbrown/helixscreen)** on a Creality K1-series printer.

## The problem

HelixScreen is a great replacement touchscreen UI for Klipper printers, but on a K1 / K1 Max with a CFS:

1. **The printer goes offline in your Creality account.** HelixScreen disables Creality's stock startup script, and that also stops the backend services Creality Print and the cloud talk to.
2. **Your CFS slots show up empty in Creality Print / Orca.** HelixScreen stores your spools (brand, material, color) on the Klipper/Moonraker side. Creality identifies materials by its own codes and HelixScreen only writes a code back when the firmware already knows it, so profiles like *Elegoo ABS* or *Bambu PLA Basic* never reach Creality's side. Slots stay "empty" or "unknown".

## What this does

- **`S99zz-creality-cloud`** starts Creality's backend services (`master-server`, `app-server`, `web-server`, `wifi-server`, …) at boot, alongside HelixScreen. The stock screen processes (`display-server`, `Monitor`) are deliberately left off, so they don't fight HelixScreen for the display.
- **`helix_cfs_sync.py`** is a small service that checks your HelixScreen slots every 15 seconds. When one differs from Creality's side, it sends Creality's own `modifyMaterial` message over the local websocket on port 9999, the same channel Creality Print uses. Each material is mapped to Creality's matching *Generic* profile (or the closest base type), with your color.

| HelixScreen slot | What Creality sees |
|---|---|
| Elegoo ABS, teal | Generic ABS, #00BCD4 |
| eSUN PLA-Basic, dark gray | Generic PLA, #1A1A1A |
| Bambu PLA Basic, white | Generic PLA, #FFFFFF |
| Any brand PLA Silk | Generic PLA-Silk |

HelixScreen keeps your exact brand profiles. In your slicer you can still pick the brand-specific filament profile, since the CFS only needs the type and color to match.

### Safety rules built in

- **One-way only:** HelixScreen → Creality. Nothing is ever written back to HelixScreen.
- **Never during a print:** nothing is sent while a print is running or paused.
- **RFID spools are left alone:** Creality already reads those itself.
- **Unknown materials are skipped and logged,** never guessed.
- **No hammering:** if Creality doesn't accept an update, it's retried at most every 10 minutes.

## Requirements

- Creality **K1 / K1C / K1 Max / K1 SE** with a **CFS** (tested on a K1 Max, firmware 2.3.5.34, CFS firmware 1.1.3)
- Root access plus Moonraker, e.g. via [Guilouz's Creality Helper Script](https://github.com/Guilouz/Creality-K1-and-K1-Max)
- [HelixScreen](https://github.com/prestonbrown/helixscreen) installed (tested with 1.0.9)

K2-series printers use a different CFS implementation and are **not** supported.

## Install

SSH into the printer (`ssh root@<printer-ip>`) and run:

```sh
git clone https://github.com/MikeGuard/Helixscreen-creality-CFS-sync.git /usr/data/helix-cfs-sync
sh /usr/data/helix-cfs-sync/install.sh
```

The installer sets up both boot scripts, starts everything and shows the first log lines. It takes about a minute.

If your Creality services already run some other way, install only the sync service:

```sh
sh /usr/data/helix-cfs-sync/install.sh --sync-only
```

After installing, refresh the device page in Creality Print. Your slots should show up within about 15 seconds.

### Using it with OrcaSlicer

Orca 2.4 and later can read the CFS slots directly. Add the printer with **Host Type: CrealityPrint** and **Printer Agent: CrealityPrint**, using your printer's IP address.

## Usage

```sh
/etc/init.d/S99zzz-helix-cfs-sync status     # is it running?
/etc/init.d/S99zzz-helix-cfs-sync restart

PY=/usr/data/moonraker/moonraker-env/bin/python
$PY /usr/data/helix-cfs-sync/helix_cfs_sync.py --dump      # what Creality currently sees
$PY /usr/data/helix-cfs-sync/helix_cfs_sync.py --dry-run   # what would be sent, sends nothing
$PY /usr/data/helix-cfs-sync/helix_cfs_sync.py --once      # one sync pass
```

The log is at `/usr/data/printer_data/logs/helix_cfs_sync.log`, which you can also open from Mainsail or Fluidd under logs. It only writes a line when something changes.

## Uninstall

```sh
sh /usr/data/helix-cfs-sync/uninstall.sh          # remove the sync service
sh /usr/data/helix-cfs-sync/uninstall.sh --all    # also remove the Creality services starter
```

## How it works

| Where | What |
|---|---|
| Moonraker DB, namespace `lane_data` | HelixScreen's slot data: `material`, `vendor`, `helix_product_name`, `color`, `nozzle_temp`, and `lane` (0 = 1A … 3 = 1D, 4 = 2A …) |
| `ws://127.0.0.1:9999` | Creality's backend. `{"method":"get","params":{"boxsInfo":1}}` reads the slots; `{"method":"set","params":{"modifyMaterial":{boxId,id,rfid,type,vendor,name,color,…}}}` writes one |
| `/usr/data/creality/userdata/box/material_database.json` | Creality's material list; `rfid` is the 5-digit database id (`00001` = Generic PLA, `00004` = Generic ABS, …) |
| `/usr/data/creality/userdata/box/tn_data.json` | Klipper's CFS slot file. Creality updates it itself after `modifyMaterial` (`material_type` = `0` + id) |

Colors are sent as `#0RRGGBB`, Creality's format. Slot state in `boxsInfo` is `0` = empty, `1` = manually set, `2` = RFID.

Writing Klipper's slot data directly with `BOX_MODIFY_TN_DATA` updates `tn_data.json` but **not** what Creality Print sees, which is why this goes through the websocket instead.

### Material mapping

Base types map to Creality's Generic profiles: PLA, PLA-Silk, PLA-CF, PETG, PETG-CF, ABS, ASA, TPU, PA, PA-CF, PA6-CF, PAHT-CF, PET, PET-CF, PC, PP, PPS, PPS-CF, PVA, BVOH, HIPS.
Common variants are folded into their base type: `PLA+`, `PLA Matte`, `PLA-Basic`, `PETG-HF`, `ABS+`, `Nylon`, `TPU 95A`, … Anything else that starts with a known type (e.g. `PETG-Translucent`) maps to that type. To add more, edit `PRESETS` / `ALIASES` at the top of `helix_cfs_sync.py`.

## Troubleshooting

- **Log says "Creality backend returned no CFS boxes":** Creality's `web-server` / `master-server` aren't running. Run `/etc/init.d/S99zz-creality-cloud status`.
- **Mainsail briefly can't reach Moonraker right after boot:** starting the Creality services causes a short CPU spike. The 3-second stagger in the boot script avoids it. If you start them by hand, don't start them all at once.
- **After a Creality firmware update or a HelixScreen reinstall, slots stop syncing:** check the log first. The `modifyMaterial` format or HelixScreen's `lane_data` layout may have changed.

## Credits

- [HelixScreen](https://github.com/prestonbrown/helixscreen) by Preston Brown. Its reverse-engineering notes on the K1 CFS (`docs/devel/printer-research/CREALITY_CFS_K1_INTERNALS.md`) explained Creality's slot data and material codes.
- [creality-cfs-mainsail-integration](https://github.com/HimAndRobot/creality-cfs-mainsail-integration) by HimAndRobot. Its Mainsail panel showed the `boxsInfo` / `modifyMaterial` websocket messages.

Not affiliated with Creality or HelixScreen. Use at your own risk.

## License

MIT
