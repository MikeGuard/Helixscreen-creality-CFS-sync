#!/bin/sh
# helix-cfs-sync uninstaller - run ON THE PRINTER as root.
#   sh uninstall.sh          remove the sync service (keeps the Creality services starter)
#   sh uninstall.sh --all    also remove S99zz-creality-cloud and stop the Creality services

if [ -x /etc/init.d/S99zzz-helix-cfs-sync ] || [ -f /etc/init.d/S99zzz-helix-cfs-sync ]; then
    /etc/init.d/S99zzz-helix-cfs-sync stop 2>/dev/null
    rm -f /etc/init.d/S99zzz-helix-cfs-sync
    echo "- removed sync service"
fi

if [ "$1" = "--all" ] && [ -f /etc/init.d/S99zz-creality-cloud ]; then
    /etc/init.d/S99zz-creality-cloud stop 2>/dev/null
    rm -f /etc/init.d/S99zz-creality-cloud
    echo "- removed Creality services starter"
fi

echo "Done. Program files are still in /usr/data/helix-cfs-sync (delete that folder if you like)."
echo "Slot info already written to Creality's side is left as it is."
