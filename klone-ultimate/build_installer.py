"""Build the self-contained installer from the reviewed Python source."""
import base64
import hashlib
from pathlib import Path
import zlib

root = Path(__file__).resolve().parent
source = (root / 'klone-ultimate-merged-everything.py').read_bytes()
payload = base64.b64encode(zlib.compress(source, 9)).decode()
digest = hashlib.sha256(source).hexdigest()
script = '''#!/usr/bin/env bash
# KLTECH Klone Ultimate. Installs only this user-owned controller; no sudo or pip.
set -Eeuo pipefail
command -v python3 >/dev/null 2>&1 || { printf '%s\\n' 'Python 3.10+ is required.' >&2; exit 1; }
python3 - <<'KLONE_PAYLOAD'
import base64, hashlib, os, pathlib, sys, tempfile, zlib
if sys.version_info < (3, 10):
    raise SystemExit('Python 3.10+ is required.')
root = pathlib.Path(os.environ.get('KLONE_HOME', str(pathlib.Path.home()/'.klone-ultimate'))).expanduser().resolve()
root.mkdir(mode=0o700, parents=True, exist_ok=True)
data = zlib.decompress(base64.b64decode('__PAYLOAD__'))
if hashlib.sha256(data).hexdigest() != '__DIGEST__':
    raise SystemExit('Embedded payload checksum failed.')
target = root/'klone-ultimate-merged-everything.py'
if target.is_symlink():
    raise SystemExit('Refusing to overwrite a symbolic link.')
if target.exists() and target.read_bytes() != data:
    backup = root/('controller-backup-' + hashlib.sha256(target.read_bytes()).hexdigest()[:12] + '.py')
    if not backup.exists():
        fd = os.open(backup, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f: f.write(target.read_bytes())
fd, temp = tempfile.mkstemp(prefix='install-', dir=root)
with os.fdopen(fd, 'wb') as f:
    f.write(data)
    f.flush()
    os.fsync(f.fileno())
os.replace(temp, target)
print('Installed:', target)
print('Payload SHA-256: __DIGEST__')
KLONE_PAYLOAD
KLONE_INSTALL_DIR="${KLONE_HOME:-$HOME/.klone-ultimate}"
if [[ "${1:-}" == "--install-only" ]]; then
  printf '%s\\n' 'Install complete. Start with:' "python3 \\"$KLONE_INSTALL_DIR/klone-ultimate-merged-everything.py\\" up"
  exit 0
fi
printf '%s\\n' 'Owner key: run the same Python file with the key command in another terminal.'
if [[ $# -eq 0 ]]; then set -- up; fi
exec python3 "$KLONE_INSTALL_DIR/klone-ultimate-merged-everything.py" "$@"
'''
script = script.replace('__PAYLOAD__', payload).replace('__DIGEST__', digest)
(root / 'easy-install.sh').write_text(script)
(root / 'easy-install.sh').chmod(0o755)
print('Built easy-install.sh; embedded source SHA-256:', digest)
