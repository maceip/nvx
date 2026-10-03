# NVX: An Ultra-Light Micro-VM Sandbox

NVX is an ultra-light micro-VM sandbox for running untrusted workloads with hardware-enforced
isolation. It is built on top of OpenVMM and runs Linux as a guest.

NVX was jointly developed by the [MSR Systems Research Group][msr-systems] and
[Azure Research - Systems][azure-systems], building on research results from the
[Nanvix](https://github.com/nanvix) system.

[msr-systems]: https://www.microsoft.com/en-us/research/group/systems-research-group-redmond/
[azure-systems]: https://www.microsoft.com/en-us/research/group/azure-research-systems/

This repository includes version pins for Linux and OpenVMM, along with the patches, guest source
files, build tools, and benchmarks needed to use NVX.

## Quick Start

Python 3.10 or newer is required. The commands below download the latest NVX release for the
selected platform, so no local build is required. A successful boot prints
`NVX-GUEST-BOOT-OK: alpine` and opens a root shell. Select Ubuntu userland
with the same NVX kernel by passing `--guest ubuntu`.

Exit cleanly from the guest with `/sbin/nvx-exit 0`. See the [setup](doc/setup.md) and
[run](doc/run.md) guides for detailed prerequisites and runtime options.

> ℹ️ For direct OpenVMM integration without the `scripts/nvx.py` runtime harness,
see [Run OpenVMM directly](doc/run.md#run-openvmm-directly).

### Linux / KVM

Requires [KVM configured with read/write access to `/dev/kvm`](doc/setup.md#linux--kvm).

```bash
git clone https://github.com/microsoft/nvx.git && cd nvx
python3 scripts/nvx.py download
python3 scripts/nvx.py run
```

### Linux / MSHV

Requires [MSHV configured with read/write access to `/dev/mshv`](doc/setup.md#linux--mshv).

```bash
git clone https://github.com/microsoft/nvx.git && cd nvx
python3 scripts/nvx.py download --hypervisor mshv
python3 scripts/nvx.py run --hypervisor mshv
```

### Windows / WHP

Requires [Windows Hypervisor Platform enabled](doc/setup.md#windows--whp).

```powershell
git clone https://github.com/microsoft/nvx.git; Set-Location nvx
python scripts\nvx.py download
python scripts\nvx.py run
```

## running claude & codex in an nvx guest on macos

On Apple Silicon, an NVX Alpine guest with virtio networking can install and run the Claude Code
and Codex CLIs. The one-command path is `scripts/nvx-claude-codex.sh`, which boots the guest,
installs the selected agent(s), prints each `--version`, and then hands the console to you:

```bash
./scripts/nvx-claude-codex.sh                 # both agents (default)
./scripts/nvx-claude-codex.sh --agent claude
./scripts/nvx-claude-codex.sh --agent codex
./scripts/nvx-claude-codex.sh --tcp 18080:3000  # plus a host->guest port forward
```

Verified: Claude Code `2.1.283` (`~/.local/bin/claude --version`) and Codex `codex-cli 0.157.1`
(`/usr/local/bin/codex --version`).

One-time prerequisites: build the HVF binary and the Alpine guest (Docker is required for the
guest build), then sign the binary with the Hypervisor entitlement:

```bash
python3 scripts/nvx.py build-openvmm --backend hvf
cat > /tmp/hvf.entitlements <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>com.apple.security.hypervisor</key><true/>
</dict></plist>
EOF
codesign -s - --entitlements /tmp/hvf.entitlements --force openvmm/target/release/openvmm
python3 scripts/nvx.py build-guest --guest alpine
```

The manual equivalent (what the script automates) is a boot with room for the installer payloads
plus a virtio NIC, then in the guest:

```bash
python3 scripts/nvx.py run --hypervisor hvf --memory-mib 2048 \
    --virtio-net 'consomme:192.168.127.0/24'
```

```sh
apk update && apk add bash curl npm
curl -fsSL https://claude.ai/install.sh -o /tmp/ci.sh && bash /tmp/ci.sh
~/.local/bin/claude --version
npm install -g @openai/codex
/usr/local/bin/codex --version
```

Caveats:

- No ICMP to the outside world (unprivileged NAT); TCP, UDP, DNS, and DHCP all work.
- The default 512M guest is too small for the installer payloads (tmpfs fills up); use
  `--memory-mib 2048`.
- Everything installed in this raw guest lives in RAM and vanishes when the VM exits.
  Exit cleanly with `/sbin/nvx-exit 0`; the host repairs the clock at boot and restore.
- For credentials kept on the host, use the managed image runner's
  [scoped credential proxy](doc/cookbook.md#keep-an-api-credential-on-the-host).
  Exporting API keys in this raw guest puts their values in guest RAM and any snapshot.

## Documentation

The optional native macOS app lives in [maceip/nvx-showcase](https://github.com/maceip/nvx-showcase).
Keep its checkout alongside this repository (for example, `~/nvx-showcase` beside `~/nvx`).

### Usage

- [Setup](doc/setup.md) - Instructions for setting up your environment.
- [Build](doc/build.md) - Instructions for building NVX.
- [Run](doc/run.md) - Instructions for running NVX.
- [Benchmark](doc/benchmarks.md) - Instructions for benchmarking NVX.
- [Command-line reference](doc/usage.md) - Complete `scripts/nvx.py` command and option reference.

### Development

- [Design](doc/design.md) - Current microVM architecture and ABI.
- [Project structure](doc/project-structure.md) - Overview of the NVX repository layout.
- [Continuous integration](doc/ci.md) - Instructions for running and maintaining NVX CI.
- [Copilot-driven adversarial testing](doc/design/copilot-adversarial-testing.md) - Bounded
  adaptive stress and containment campaigns.
- [Package and source delivery](doc/distribution.md) - Instructions for packaging and distributing
	NVX.
- [Contributing](doc/contribute.md) - Guidelines for contributing to NVX.

### Containment evidence

The [generated containment matrix](doc/containment-matrix.md) records eight scoped probe
families and their failing controls. Run `python3 scripts/nvx.py containment run --backend
hvf --format md` to reproduce it on Apple Silicon, or select the matching KVM/MSHV/WHP
backend. See the matrix for current backend evidence and scope.

### OCI workloads and integrations

```sh
python3 scripts/nvx.py run --image python:3.12-slim -- python -c 'print(1)'
python3 scripts/nvx.py warm --image python:3.12-slim --output build/python-warm
python3 scripts/nvx.py pool start --template build/python-warm --size 2
python3 scripts/nvx.py mcp serve
```

These commands select HVF on macOS, KVM on Linux, or WHP on Windows. Image preparation
occurs before launch; workloads default to non-root, bounded resources and denied egress.
The [cookbook](doc/cookbook.md) covers workspaces, outputs, credential injection, receipts,
pool leases and the Python/TypeScript SDKs. See [implementation evidence](doc/implementation-status.md)
for completed local checks and the external gates still required before publication.
The [image-format comparison](doc/image-formats.md) explains OCI imports, NVX's EROFS/ext4
runtime layout and Nanvix's separate ELF/FAT32 workload format.
