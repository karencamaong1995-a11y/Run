# KLTECH Klone Ultimate — portable controller 1.0.0

Python 3.10+ and the standard library. No pip, sudo, Docker, or Proxmox installation is performed by the installer.

This is a new, additive controller edition. The earlier Clone Engine NewGen v3.1 package is preserved separately. The filename `klone-ultimate-merged-everything.py` matches the requested entry point; it is not a claim that every historical KLTECH module has been merged or verified.

## Start with one command

Run this in a terminal on the **computer that will host the controller**:

```bash
curl -fsSL https://raw.githubusercontent.com/karencamaong1995-a11y/Run/main/klone-ultimate/easy-install.sh -o /tmp/klone-install.sh && bash /tmp/klone-install.sh
```

The installer contains the complete Python payload and verifies its embedded SHA-256 before installing. It creates `~/.klone-ultimate/klone-ultimate-merged-everything.py`, then runs it in the foreground. Keep that terminal open; Ctrl+C stops both listeners. The embedded checksum detects corruption; review the source and pin a reviewed Git commit when using this on a sensitive host.

Equivalent local entry point:

```bash
python3 klone-ultimate-merged-everything.py up
```

Open **http://localhost:8420** on that computer. The second API listener is **http://localhost:8423**. Both use the same owner authentication and state. `localhost` on an iPhone refers to the iPhone, not to a separate PC or cloud VM.

In another terminal, retrieve the generated owner key:

```bash
python3 "$HOME/.klone-ultimate/klone-ultimate-merged-everything.py" key
```

Enter that key in the dashboard. The key is generated per installation, has no default password, stays in memory in the browser while unlocked, and is stored locally in `owner.key` with mode 0600. No secrets belong in GitHub or chat.

Install without starting:

```bash
bash easy-install.sh --install-only
```

Repeat installs preserve the configuration, owner key and receipts. An existing different controller file is backed up. Stop an old running controller before starting the upgraded one.

## Connect a real Proxmox host

Run this on the controller computer:

```bash
python3 "$HOME/.klone-ultimate/klone-ultimate-merged-everything.py" configure
```

Enter the real HTTPS origin, node name, trusted CA PEM path if needed, token and allowed VM IDs. The token is entered through a hidden prompt. This writes a private `config.json`. Stop and restart the controller after configuration.

The original environment-variable interface also works for that process:

```bash
export PVE_HOST='https://YOUR-PROXMOX-HOST:8006'
export PVE_NODE='YOUR-NODE'
export PVE_ALLOWED_VMIDS='100,101,102'
read -r -s -p 'Proxmox API token: ' PVE_TOKEN; printf '\n'
export PVE_TOKEN
# If using the private Proxmox CA, copy the CA certificate from your own host:
# export PVE_CA_FILE='/absolute/path/to/pve-root-ca.pem'
python3 "$HOME/.klone-ultimate/klone-ultimate-merged-everything.py" up
```

Token format: `user@realm!tokenid=secret`. The adapter sends `Authorization: PVEAPIToken=...`, uses HTTPS certificate verification and refuses redirects. It never disables certificate checks. Use a dedicated account/token with only the needed permissions. Ensure both account and token ACLs permit the intended operations when token privilege separation is enabled. Start with inventory access, then grant power control on selected VMs. Cloning additionally needs permissions for the source template, destination VM and storage. Consult your installed Proxmox API viewer for endpoint-specific requirements.

VM inventory is fetched from `/api2/json/nodes/{node}/qemu`. Power operations POST to `/status/start`, `/status/shutdown` or `/status/stop`. These affect QEMU VMs, not the physical Proxmox node. Force stop is abrupt. The controller requires an exact confirmation phrase and a configured VM allowlist before sending a power request.

Full cloning uses a prepared Proxmox VM template, source and destination IDs from the allowlist, and the `/clone` endpoint. The target ID must be unused. Storage and template preparation happen in Proxmox; the controller does not download an OS or invent a boot image. LXC containers are not supported by this edition.

**ACCEPTED means Proxmox returned a task ID.** The controller polls task status and reports SUCCEEDED only when the completed task says `exitstatus=OK`. Check VM state with Refresh afterward. A running VM still needs guest/application health checks.

## Use your iPhone as the controller

The dashboard adapts to narrow screens. The service runs on Linux, macOS or WSL2 with Python 3.10+. Your iPhone can open the dashboard over a reachable secure connection. Proxmox compute runs on the actual Proxmox server.

Options:

1. Use an SSH tunnel to the controller host. For a computer with an SSH client:

   ```bash
   ssh -N -L 8420:127.0.0.1:8420 -L 8423:127.0.0.1:8423 USER@CONTROLLER_HOST
   ```

   On iPhone, use an SSH client that supports local port forwarding. Open its forwarded address in Safari.

2. Configure HTTPS on the controller with a certificate trusted by the connecting devices:

   ```bash
   python3 "$HOME/.klone-ultimate/klone-ultimate-merged-everything.py" up \
     --bind 0.0.0.0 --tls-cert /path/fullchain.pem --tls-key /path/privkey.pem
   ```

   Open `https://CONTROLLER_HOSTNAME:8420`. The hostname must match the certificate. Bind and TLS options can also be stored in private `config.json`.

Keep remote access on a trusted network or VPN. The bundled HTTP server is a private controller, not a hardened public multitenant cloud platform. No firewall, router, DNS or public cloud resources are changed by installation. In Google Cloud Shell, the process is session-bound; a persistent VM or supervised host is needed for continuous service. A cloud host needs a working private network route to reach an on-premises Proxmox address.

## Keep it running on Linux with systemd

After installation and configuration, run `bash install-user-service.sh` from this source directory as the intended non-root user. This creates and starts a user service. A user service may stop after logout unless the host administrator has enabled user lingering. The script reports this; it does not change that system policy. macOS and WSL2 service setup was not tested here. WSL2 needs systemd enabled for this helper.

```bash
systemctl --user status klone-ultimate
journalctl --user -u klone-ultimate -n 50
systemctl --user restart klone-ultimate
systemctl --user disable --now klone-ultimate
```

Configure secrets using the Python `configure` command before using a service; shell environment exports alone are not automatically inherited by systemd. The service configuration stays at `~/.klone-ultimate/config.json` unless `KLONE_HOME` is set when installing the service.

## What is measured

| Feature | Actual execution | Evidence limit |
|---|---|---|
| SHA-256 job | Bounded workload in the controller process | CPU workload timing, not cloned silicon or additional compute |
| Phone challenge | Fresh client workload with one-use server nonce | Response verification; no phone model attestation |
| VM inventory | HTTPS read from configured Proxmox node | Only what the token can see |
| Start/shutdown/stop | Proxmox API task | Must observe task completion and VM state |
| Full template clone | Proxmox storage and VM task | Consumes real host resources; guest boot/application checks are separate |
| Receipt SHA-256 | Digest of local receipt fields | Integrity check, not a signed hardware attestation |

Jobs are bounded to 1,000,000 hash rounds and one active workload. Job listing is kept in memory for this process; completed measurements persist in `evidence.jsonl`. The UI retains the newest 100 receipts. The audit rotates at approximately 2 MB and keeps one previous file, so archive it externally if you need long-term retention. The app does not execute arbitrary uploaded code or shell commands.

## API and diagnostics

`/health` is public liveness only. Every `/api/` route requires `Authorization: Bearer OWNER_KEY`.

```text
GET  /api/status
GET  /api/pve/vms
POST /api/pve/action
GET  /api/pve/task?upid=...
POST /api/jobs
GET  /api/jobs
POST /api/challenge
POST /api/anchor
GET  /api/evidence
```

`python3 klone-ultimate-merged-everything.py doctor` checks the local environment and, when configured, the Proxmox inventory. It does not start listeners. `NOT_CONFIGURED` is not a live-host success.

## Verification

```bash
python3 -m unittest discover -s tests -v
python3 build_installer.py
bash -n easy-install.sh
```

See [VERIFICATION.md](VERIFICATION.md) for what was tested and what remains unverified.

## Primary references

- [Proxmox user management and API token format](https://pve.proxmox.com/pve-docs/pveum-plain.html)
- [Proxmox staff explanation of API token authentication](https://forum.proxmox.com/threads/proxmox-api-authentication-to-create-vm.128225/)
- [Proxmox API viewer](https://pve.proxmox.com/pve-docs/api-viewer/index.html)
- [Proxmox platform overview](https://pve.proxmox.com/wiki/Introduction)

Official token documentation and staff guidance informed authentication. Some full documentation pages were inaccessible during this build. Validate permissions against the API viewer bundled with your installed host version.
