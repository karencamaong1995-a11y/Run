# Verification record — 2026-09-23

## Passed in this environment

- Python 3.12 on Linux; source compiles without third-party dependencies.
- 22 automated integration tests covering both HTTP listeners, owner authentication, bounded workload hash replay, malformed requests, origin protection, Proxmox authentication header, exact power paths, VM allowlist and confirmations, full-template clone request, duplicate target rejection, non-template rejection, task success/failure, TLS certificate rejection, redirect rejection, upstream error redaction, one-use browser challenges, private file permissions, receipt hashes, unconfigured-host state, remote HTTP refusal and partial-startup failure.
- Proxmox adapter tests used a local HTTPS fixture with a generated test certificate. They did not contact a real Proxmox server.
- Installer shell syntax, embedded payload hash, byte equality with reviewed source, repeated install and diagnostic command.
- Actual controller process started on 127.0.0.1:8420 and 127.0.0.1:8423 in the test environment.

## Not verified

- Real Proxmox host connectivity, token ACLs, VM power transitions, clone completion and guest application health: no real host credentials or route available.
- macOS, WSL2 and iPhone Safari execution: these devices were not available. The controller uses portable Python interfaces; platform compatibility remains to be confirmed on each target.
- Visual browser inspection: the available cloud browser blocked access to the local test server. No claim of completed browser rendering verification is made.
- Optional systemd helper: shell syntax checked; no live systemd user manager was available to validate service startup, reboot or logout persistence.
- Continuous cloud operation, public domain, router/firewall changes, Google Cloud bridge, hardware passthrough, physical hardware cloning, GPU acceleration or new physical capacity: not deployed or demonstrated by this build.

The intended next host check is `configure`, restart, `doctor`, then a read-only inventory refresh. Run a single explicitly selected test VM action and inspect both the task result and actual VM state before wider use.
