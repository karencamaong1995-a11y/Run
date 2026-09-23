# UTM SE setup — KLTECH Klone Ultimate

This starts the Python controller inside a Linux virtual machine in UTM SE on iPhone. UTM SE runs that guest using CPU emulation without JIT; expect much slower compute than running natively on your Ryzen or a cloud VM. UTM supports an `aarch64` guest, and Alpine provides a small ARM64 virtual-machine image.

## 1. Install UTM SE

Use the official [UTM SE App Store listing](https://apps.apple.com/us/app/utm-se-retro-pc-emulator/id1564628856). This guide assumes the iPhone already has UTM SE installed.

## 2. Create an Alpine ARM64 VM

1. Download the current `alpine-virt-3.24.2-aarch64.iso` from the [official Alpine ARM64 downloads](https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/aarch64/). Verify its checksum against Alpine's `.sha256` file on that same official directory page.
2. In UTM SE, create a new **emulated** machine using ARM64 / `aarch64`. Attach the Alpine ISO as its boot CD and create a persistent disk (8 GB is sufficient for this controller and a minimal guest).
3. Configure the guest's network adapter as VirtIO if available, with emulated networking enabled. During Alpine setup, select DHCP for its network interface.
4. At the Alpine console, sign in as `root`, then run `setup-alpine`. Set a root password. For the disk question, select the VM's virtual disk (usually `vda`) and install in `sys` mode. Reboot after installation and detach the installer ISO.

UTM SE's emulated processor is slow and iOS can suspend or terminate a guest in the background. Keep UTM SE in the foreground during use, start with a modest VM memory allocation, and shut the guest down from Alpine when finished.

## 3. Install the controller in the guest

At the Alpine root console, run:

```sh
apk add --no-cache ca-certificates curl
curl -fsSL https://raw.githubusercontent.com/karencamaong1995-a11y/Run/main/klone-ultimate/utm-se/bootstrap-alpine.sh -o /tmp/klone-utm-setup.sh
sh /tmp/klone-utm-setup.sh
```

This installs Python 3, Bash, CA certificates and curl; downloads the reviewed controller installer; and installs the controller in `/root/.klone-ultimate`. It creates a new owner key in that guest. Use it only in the guest you control.

To start the controller:

```sh
python3 /root/.klone-ultimate/klone-ultimate-merged-everything.py up
```

Both listeners bind to the guest's loopback: port 8420 for the dashboard and port 8423 for its API. The interactive UI is usable from a browser running inside the VM. The UTM guest is a separate network machine: these localhost addresses are **not** the iPhone's localhost, and UTM's emulated network does not automatically publish those ports into Safari. On the console, a local health check is:

```sh
curl http://127.0.0.1:8423/health
```

## 4. Proxmox access

Run `configure` inside the guest only if that guest can reach your Proxmox server over a private, trusted HTTPS route:

```sh
python3 /root/.klone-ultimate/klone-ultimate-merged-everything.py configure
```

UTM's emulated network is not a bridge to your home LAN. A Proxmox address such as `192.168.x.x` might not be reachable from this guest. Do not publish Proxmox's port 8006 to the internet to work around that. If the guest has no private route, leave Proxmox unconfigured; this setup can still run local, low-round workloads and the local API. Actual VM control and phone-Safari dashboard access remain unverified for UTM SE.

## What this conversion means

The existing Python controller is portable to an ARM64 Alpine Linux guest because it uses Python's standard library. This setup installs it into a Linux VM; it does not convert it into an iOS app, give UTM SE direct access to iPhone hardware, or make emulated CPU capacity equivalent to the physical iPhone. UTM SE's supported guest architectures include `aarch64`, while its no-JIT mode is slower than JIT emulation.

## Official references

- [UTM iOS guide](https://docs.getutm.app/installation/ios/)
- [UTM SE guest architectures and memory guidance](https://docs.getutm.app/settings-qemu/system/)
- [UTM emulated network](https://docs.getutm.app/settings-qemu/devices/network/network/)
- [Alpine Linux downloads](https://www.alpinelinux.org/downloads/)
