#!/usr/bin/env bash
# Optional Linux user service. No sudo; no changes to lingering or firewall.
set -Eeuo pipefail
command -v systemctl >/dev/null 2>&1 || { printf '%s\n' 'systemd is required for this optional helper.' >&2; exit 1; }
systemctl --user show-environment >/dev/null
python3 - <<'PY'
import os, pathlib, shutil, sys
root=pathlib.Path(os.environ.get('KLONE_HOME',str(pathlib.Path.home()/'.klone-ultimate'))).expanduser().resolve()
app=root/'klone-ultimate-merged-everything.py'
if not app.is_file(): raise SystemExit('Run easy-install.sh --install-only first.')
exe=shutil.which('python3')
def unit_quote(s):
    if any(c in s for c in '\n\r\x00'): raise SystemExit('Unsupported newline in path.')
    return '"'+s.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
folder=pathlib.Path.home()/'.config/systemd/user'
folder.mkdir(parents=True,exist_ok=True)
path=folder/'klone-ultimate.service'
unit='\n'.join(['[Unit]','Description=KLTECH Klone Ultimate private controller','After=network.target','',
    '[Service]','Type=simple','UMask=0077','Environment='+unit_quote('KLONE_HOME='+str(root)),
    'ExecStart='+unit_quote(exe)+' '+unit_quote(str(app))+' up',
    'Restart=on-failure','RestartSec=5','NoNewPrivileges=true','',
    '[Install]','WantedBy=default.target',''])
if path.exists() and path.read_text()!=unit:
    raise SystemExit('A different service definition exists. Review it before replacing it: '+str(path))
path.write_text(unit)
path.chmod(0o600)
print('Service definition:',path)
PY
systemctl --user daemon-reload
systemctl --user enable --now klone-ultimate.service
systemctl --user is-active klone-ultimate.service
printf '%s\n' 'User service started. Logout persistence depends on the host user-lingering setting.'
if command -v loginctl >/dev/null 2>&1; then
  loginctl show-user "$(id -un)" -p Linger || true
fi
