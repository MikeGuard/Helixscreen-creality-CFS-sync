#!/bin/sh
# helix-cfs-sync installer - run ON THE PRINTER (over SSH) as root.
#   sh install.sh              install the sync service + the Creality services starter
#   sh install.sh --sync-only  install only the sync service
set -e

DEST=/usr/data/helix-cfs-sync
SRC="$(cd "$(dirname "$0")" && pwd)"
PY=/usr/data/moonraker/moonraker-env/bin/python
WITH_CLOUD=1
[ "$1" = "--sync-only" ] && WITH_CLOUD=0

echo "== helix-cfs-sync installer =="

[ -x "$PY" ] || { echo "ERROR: Moonraker's python not found at $PY (is Moonraker installed?)"; exit 1; }
$PY -c "import tornado.websocket" 2>/dev/null || { echo "ERROR: tornado not available in Moonraker's python"; exit 1; }
[ -d /etc/init.d ] || { echo "ERROR: /etc/init.d not found - is this a Creality K1-series printer?"; exit 1; }

mkdir -p "$DEST"
if [ "$SRC" != "$DEST" ]; then
    cp "$SRC/helix_cfs_sync.py" "$DEST/"
    cp -r "$SRC/init.d" "$DEST/" 2>/dev/null || true
fi
chmod +x "$DEST/helix_cfs_sync.py"

if [ "$WITH_CLOUD" = "1" ]; then
    cp "$SRC/init.d/S99zz-creality-cloud" /etc/init.d/
    chmod +x /etc/init.d/S99zz-creality-cloud
    echo "- installed /etc/init.d/S99zz-creality-cloud (Creality services alongside HelixScreen)"
    /etc/init.d/S99zz-creality-cloud start
fi

cp "$SRC/init.d/S99zzz-helix-cfs-sync" /etc/init.d/
chmod +x /etc/init.d/S99zzz-helix-cfs-sync
echo "- installed /etc/init.d/S99zzz-helix-cfs-sync"

# give the Creality services time to start before the first sync
if [ "$WITH_CLOUD" = "1" ]; then echo "- waiting for Creality services..."; sleep 40; fi
/etc/init.d/S99zzz-helix-cfs-sync restart
sleep 8
/etc/init.d/S99zzz-helix-cfs-sync status

echo
echo "Log: /usr/data/printer_data/logs/helix_cfs_sync.log (Mainsail/Fluidd -> logs)"
tail -n 10 /usr/data/printer_data/logs/helix_cfs_sync.log 2>/dev/null || true
echo
echo "Check what Creality sees:  $PY $DEST/helix_cfs_sync.py --dump"
