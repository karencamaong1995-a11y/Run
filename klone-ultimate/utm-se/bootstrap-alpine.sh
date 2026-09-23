#!/bin/sh
# Install the KLTECH controller inside an already installed Alpine Linux guest.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  printf '%s\n' 'Run this script as root inside Alpine Linux.' >&2
  exit 1
fi

apk add --no-cache ca-certificates bash curl python3
install -d -m 700 /root/.klone-ultimate
download=/tmp/klone-ultimate-install.sh
curl --fail --show-error --silent --location \
  https://raw.githubusercontent.com/karencamaong1995-a11y/Run/main/klone-ultimate/easy-install.sh \
  --output "$download"
chmod 600 "$download"
bash "$download" --install-only
rm -f "$download"

printf '\n%s\n' 'Installed in this Alpine guest.'
printf '%s\n' 'Next: run the configure command below only if Proxmox is reachable from this guest.'
printf '%s\n' '  python3 /root/.klone-ultimate/klone-ultimate-merged-everything.py configure'
printf '%s\n' 'Read the owner key from this UTM console:'
printf '%s\n' '  python3 /root/.klone-ultimate/klone-ultimate-merged-everything.py key'
printf '%s\n' 'Start the local controller in this UTM console:'
printf '%s\n' '  python3 /root/.klone-ultimate/klone-ultimate-merged-everything.py up'
printf '\n%s\n' 'This guest binds to its own loopback address. Its dashboard is not automatically reachable from iPhone Safari.'
