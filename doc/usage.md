# Command-line usage

`scripts/nvx.py` is the supported command-line entry point for building,
running, benchmarking, and packaging NVX. Run it from the repository root with
Python 3.10 or newer:

```text
python3 scripts/nvx.py COMMAND [OPTIONS]
```

The examples on this page use the POSIX spelling. On Windows, use
`python scripts\nvx.py` instead. Every command accepts `-h` or `--help`,
including nested commands:

```console
python3 scripts/nvx.py --help
python3 scripts/nvx.py run --help
python3 scripts/nvx.py performance gate --help
```

## Commands

| Command | Description |
| --- | --- |
| `init` | Initialize the OpenVMM submodule and its nested submodules. |
| `build-guest` | Build the Linux kernel and selected guest artifacts. |
| `build-kernel` | Build the pinned and patched Linux kernel natively. |
| `build-initramfs` | Build the selected Alpine or Ubuntu initramfs natively. |
| `build-distro-layer` | Build a deterministic Ubuntu EROFS distro layer. |
| `verify-guest-determinism` | Rebuild Ubuntu guest artifacts twice and compare SHA-256 values. |
| `build-openvmm` | Build the OpenVMM release binary. |
| `record-openvmm-provenance` | Bind an existing OpenVMM binary to the pinned source revision. |
| `materialize-kernel-provenance-inputs` | Write kernel provenance inputs from raw run-head blobs. |
| `setup-cross-os-cache` | Install GNU tar and zstd for GitHub Actions cross-OS caches. |
| `check-required-ci` | Validate required GitHub Actions job results. |
| `test-openvmm-unit` | Run the OpenVMM workspace unit and documentation tests. |
| `test-openvmm` | Run self-contained OpenVMM microVM control-plane tests. |
| `test-microvm` | Run NVX Linux and device correctness tests through OpenVMM. |
| `test-adversarial` | Run a brokered Copilot-driven adversarial campaign. |
| `build` | Build the guest artifacts and OpenVMM. |
| `download` | Download and install the latest matching GitHub release. |
| `run` | Run an OpenVMM microVM. |
| `sandbox` | Run or manage workloads over EROFS layers and private ext4 scratch. |
| `benchmark` | Run the OpenVMM-native benchmark coordinator. |
| `performance` | Collect, gate, and persist CI performance results. |
| `collect-sources` | Materialize verified Linux, Alpine, and Ubuntu release sources. |
| `collect-alpine-sources` | Collect exact Alpine recipes and upstream sources. |
| `collect-ubuntu-sources` | Collect exact Ubuntu source packages. |
| `create-linux-source-archive` | Create a Linux corresponding-source archive. |
| `package` | Stage a binary distribution. |
| `archive-release` | Create a deterministic archive from a staged distribution. |
| `verify` | Verify source and submodule inputs. |
| `snapshot` | Inspect and validate saved VM snapshots. |

## Initialization and verification

### `init`

```console
python3 scripts/nvx.py init
```

Initializes and recursively updates the OpenVMM Git submodule.

### `verify`

```console
python3 scripts/nvx.py verify
```

Verifies the repository's pinned source inputs and submodule state.

### `setup-cross-os-cache`

```console
python3 scripts/nvx.py setup-cross-os-cache
```

Installs the GNU tar and zstd tools used by GitHub Actions cross-OS caches.

See [Setup](setup.md) for host prerequisites.

## Build commands

### `build-guest`

```text
python3 scripts/nvx.py build-guest
    [--guest {alpine,ubuntu,all}]
    [--native]
```

By default, builds the guest kernel and initramfs with Docker. `--native`
builds the selected artifacts directly on Linux instead. Alpine is the
default. `--guest all` also builds the Ubuntu EROFS distro layer.

### `build-kernel`

```console
python3 scripts/nvx.py build-kernel
```

Fetches, verifies, patches, and builds the pinned kernel directly on Linux.

### `build-initramfs`

```console
python3 scripts/nvx.py build-initramfs [--guest {alpine,ubuntu}]
```

Builds the selected initramfs directly on Linux. Alpine is the default.

### `build-distro-layer`

```text
python3 scripts/nvx.py build-distro-layer
    --guest ubuntu
    [--output PATH]
    [--replace]
```

Builds the immutable Ubuntu EROFS `distro` layer directly on Linux. The default
output is `build/ubuntu-distro.erofs`. Existing output or manifest files are
rejected unless `--replace` is present.

### `verify-guest-determinism`

```text
python3 scripts/nvx.py verify-guest-determinism
    --guest ubuntu
    [--work-dir PATH]
```

Builds the Ubuntu initramfs and EROFS layer twice from separate roots and
compares every artifact SHA-256. A mismatch reports the first differing
normalized rootfs entry when one exists.

### `build-openvmm`

```text
python3 scripts/nvx.py build-openvmm [--skip-restore] [--backend {kvm,mshv,whp}]
```

Builds the `openvmm` release binary. Before building, the command runs
`cargo xflowey restore-packages`; use `--skip-restore` when those packages are
already restored. Without `--backend`, Windows builds the native MSVC target
and Linux builds the native GNU target. On Linux, `--backend kvm` selects GNU
and `--backend mshv` selects musl; Windows accepts `--backend whp`. Build-target
selection does not probe `/dev/kvm` or `/dev/mshv`, so compilation also works
on build-only hosts and hosts exposing both devices. Unsupported OS/backend
combinations are rejected.

### `build`

```text
python3 scripts/nvx.py build
    [--guest {alpine,ubuntu,all}]
    [--native]
    [--skip-restore]
    [--backend {kvm,mshv,whp}]
```

Runs `build-guest` followed by `build-openvmm`. The options have the same
meaning as on those individual commands.

See [Build](build.md) for dependencies, outputs, and native build details.

## Test commands

### `test-openvmm-unit`

```console
python3 scripts/nvx.py test-openvmm-unit
```

Runs the OpenVMM workspace's unit-test binaries with cargo-nextest's `agent`
profile and the `ci` feature. Packages that require specialized test harnesses
are excluded, along with all fuzz crates reported by OpenVMM's `xtask`.
Afterward, runs the workspace doctests with Cargo.

### `test-openvmm`

```text
python3 scripts/nvx.py test-openvmm --backend {kvm,mshv,whp}
```

Builds and runs OpenVMM's checkout-owned VMM tests. The Linux-direct microVM
TTRPC test boots NVX's `build/vmlinux` and `build/initramfs.cpio.gz`, so build
the guest first; the remaining test artifacts are produced by OpenVMM itself.

### `test-microvm`

```text
python3 scripts/nvx.py test-microvm
    --backend {kvm,mshv,whp}
    [--guest {alpine,ubuntu}]
    [--scenario SCENARIO]...
    [--processors {1,2,4,8} ...]
    [--memory-mib MIB]
    [--timeout SECONDS]
    [--output-dir PATH]
```

Runs NVX-owned Linux, SMP, virtio, sandbox, and snapshot correctness scenarios
against the public OpenVMM CLI. Repeat `--scenario` to select a subset; without
it, every scenario supported by the selected guest runs. Alpine remains the
default. Ubuntu rejects the Alpine-control-only `sandbox-blocks` and
`scratch-snapshot` scenarios, the Alpine-prompt-specific `console-snapshot`
scenario, and the sandbox-control-dependent `snapshot-tiers` scenario. The
command requires `build/vmlinux`, the selected initramfs, and
`openvmm/target/release/openvmm[.exe]`.

### `test-adversarial`

```text
python3 scripts/nvx.py test-adversarial
    --backend {kvm,mshv,whp}
    --campaign {workload-isolation,guest-isolation,snapshot-isolation}
    [--budget-seconds SECONDS]
    [--budget-actions COUNT]
    [--budget-ai-credits CREDITS]
    [--seed SEED]
    [--output-dir PATH]
    [--replay ACTIONS.jsonl]
    [--model MODEL]
    [--host-type {baremetal,virtual-machine}]
    [--memory-mib MIB]
    [--phase-timeout SECONDS]
    [--action-timeout SECONDS]
    [--executor-command PATH]
    [--no-minimize]
    [--minimize-attempts COUNT]
```

Runs a bounded adaptive campaign in which an already installed and
authenticated Copilot CLI selects one deterministic primitive at a time.
Copilot has no tools or direct NVX access. The typed broker validates and
records every action, while a credential-free executor and independent
watchdog own VM operation, canaries, teardown checks, and the clean
post-campaign boot.

| Option | Default | Description |
| --- | --- | --- |
| `--backend` | required | Select KVM or MSHV on Linux, or WHP on Windows. |
| `--campaign` | required | Select workload, privileged-guest, or snapshot boundary probes. |
| `--budget-seconds` | `900` | Bound preflight, actions, canary boot, and minimization wall time. |
| `--budget-actions` | `8` | Bound accepted and executed broker actions. |
| `--budget-ai-credits` | `300` | Bound charged Copilot usage; the minimum is 60 credits so authentication preflight and at least one action each retain a 30-credit CLI cap. |
| `--seed` | `0` | Seed deterministic candidate ordering and synthetic canary data. |
| `--output-dir` | backend-specific path under `build/test-results` | Parent for a unique campaign run directory. |
| `--replay` | none | Replay an `actions.jsonl` bound to its sibling `replay-manifest.json`, without invoking Copilot. |
| `--model` | Copilot auto routing | Select the strategist model. |
| `--host-type` | `NVX_HOST_TYPE` or `unspecified` | Record whether the executor is bare metal or a virtual machine. |
| `--memory-mib` | `256` | Set memory for deterministic microVM scenarios. |
| `--phase-timeout` | `60` | Set each underlying deterministic scenario phase timeout. |
| `--action-timeout` | `600` | Set the outer limit for one action or canary boot. |
| `--executor-command` | local child executor | Select one trusted no-argument wrapper for a separate disposable target. |
| `--no-minimize` | off | Disable fresh-target shorter-prefix replay after an anomaly. |
| `--minimize-attempts` | `3` | Bound shorter-prefix attempts within the campaign time budget. |

Missing or unauthenticated Copilot CLI is a preflight failure in adaptive
mode. Replay mode has no Copilot prerequisite. Production and CI campaigns
must use a separate disposable executor through `--executor-command`; local
mode cannot reliably classify a target-host crash. Local target logs are kept
under the short `build/adv` state root to avoid Windows path-length failures;
the run summary records their absolute location. See
[Copilot-driven adversarial testing](design/copilot-adversarial-testing.md)
for the trust boundary, wrapper contract, artifacts, and CI policy.

## Download and run

### `download`

```text
python3 scripts/nvx.py download
    [--repository OWNER/REPOSITORY]
    [--hypervisor {auto,whp,kvm,mshv}]
```

| Option | Default | Description |
| --- | --- | --- |
| `--repository OWNER/REPOSITORY` | `microsoft/nvx` | GitHub repository from which to download the latest release. |
| `--hypervisor {auto,whp,kvm,mshv}` | `auto` | Select the release platform. `auto` chooses WHP on Windows and KVM on Linux. |

Windows release downloads support WHP. Linux release downloads support KVM
and MSHV. `download` first uses `GH_TOKEN` or `GITHUB_TOKEN` when configured.
If GitHub rejects that token with HTTP 401 or 403, NVX reports the failure and
retries without credentials so public releases remain downloadable. Private
repositories require a token with read access to the repository contents that
is authorized for the organization when it enforces single sign-on.

### `run`

```text
python3 scripts/nvx.py run
    [--guest {alpine,ubuntu}]
    [--hypervisor {auto,whp,kvm,mshv,hvf}]
    [--machine {microvm}]
    [--memory-mib MIB]
    [--memory-capacity-mib MIB]
    [--memory-backing-file PATH]
    [--processors {1,2,4,8}]
    [--mount GUEST_TARGET,HOST_PATH[,ro|rw]]
    [--mount-deny HOST_PATH]...
    [--net IPV4/PREFIX]
    [--network-profile {portable}]
    [--network-egress {allow,deny}]
    [--network-ingress {allow,deny}]
    [--network-egress-allow CIDR[:PROTOCOL:PORT]]...
    [--network-egress-deny CIDR[:PROTOCOL:PORT]]...
    [--host-loopback {allow,deny}]
    [--network-proxy IPV4:TCP-PORT]
    [--host-loopback-forward PROTOCOL:HOST_PORT:GUEST_PORT]...
    [--virtio-net BACKEND]...
    [--share PORT:MNTPOINT[:ro|rw]]...
    [--serve HOSTDIR:MNTPOINT[:ro|rw]]...
    [--kernel PATH]
    [--initrd PATH]
    [--disk [PATH]]
    [--disk-mount MNTPOINT]
    [--outcome-report PATH]
    [--cmdline TEXT]
    [--save-snapshot DIR]
    [--save-on MARKER]
    [--save-exec CMD]
    [--save-ready MARKER]
    [--save-timeout SECONDS]
    [--restore-snapshot PATH]
    [--restore-processors {1,2,4,8}]
    [--restore-memory-mib MIB]
    [--restore-ready-path PATH]
    [--dry-run]
```

| Option | Default | Description |
| --- | --- | --- |
| `--guest {alpine,ubuntu}` | `alpine` | Select Alpine or Ubuntu userland with the same NVX kernel. This option is not used for snapshot restore. |
| `--hypervisor {auto,whp,kvm,mshv}` | `auto` | Select the OpenVMM hypervisor. `auto` chooses WHP on Windows and KVM elsewhere. |
| `--machine {microvm}` | `microvm` | Select the fixed-topology microVM with shared-status edge interrupts. |
| `--memory-mib MIB` | guest-specific | Set guest memory in MiB. Defaults to 128 for Alpine and 256 for Ubuntu. |
| `--memory-capacity-mib MIB` | none | Reserve an immutable, 128 MiB-aligned RAM capacity for a fresh microVM snapshot. |
| `--processors {1,2,4,8}` | `1` | Select the microVM processor count. |
| `--mount GUEST_TARGET,HOST_PATH[,ro\|rw]` | none | Expose one host directory to the absolute guest target. Active snapshot restore requires the same canonical path, target, and mode; a dormant-slot restore may attach a new mapping that the resumed guest mounts explicitly. |
| `--mount-deny HOST_PATH` | none | Hide one existing file or directory inside the mounted host root; repeat to deny multiple paths. |
| `--net IPV4/PREFIX` | none | Enable virtio-net with the static guest IPv4 address and prefix. |
| `--network-profile {portable}` | none | Select the required cross-platform network behavior contract; must be specified with `--net`. |
| `--network-egress {allow,deny}` | `allow` | Set the default guest egress policy. |
| `--network-ingress {allow,deny}` | `deny` | Set the default host ingress policy. The portable profile currently supports only `deny`; `allow` is rejected before launch. |
| `--network-egress-allow CIDR[:PROTOCOL:PORT]` | none | Allow matching guest egress; repeat to add rules. |
| `--network-egress-deny CIDR[:PROTOCOL:PORT]` | none | Deny matching guest egress; repeat to add rules. Deny rules take precedence. |
| `--host-loopback {allow,deny}` | existing mapping | Control guest access to host loopback services. |
| `--network-proxy IPV4:TCP-PORT` | none | Allow one explicit host TCP proxy endpoint. |
| `--host-loopback-forward PROTOCOL:HOST_PORT:GUEST_PORT` | none | Publish one TCP or UDP localhost port to the guest; repeat to add forwards. |
| `--virtio-net BACKEND` | none | Expose a virtio NIC (e.g. `consomme` or `consomme:192.168.127.0/24`); repeat to add NICs. On hvf the guest configures it via DHCP unless an egress policy selects a static identity. |
| `--share PORT:MNTPOINT[:ro|rw]` | none | Mount an already-running host 9P server in the guest; needs a consomme `--virtio-net` (gwloopback is added automatically). |
| `--serve HOSTDIR:MNTPOINT[:ro|rw]` | none | Self-serve `--share`: start an in-process 9P server for HOSTDIR on an ephemeral loopback port for exactly the run. Under `--network-egress deny` the gateway hole for that port is punched automatically. |
| `--kernel PATH` | built artifact | Custom direct-boot kernel (fresh boot only). |
| `--initrd PATH` | built artifact | Custom initramfs (fresh boot only). |
| `--disk [PATH]` | none | Persistent virtio-blk disk (hvf only); a missing image is created sparse (4 GiB). Bare `--disk` uses `nvx-disk.raw` in the repo root. Repeat the same `--disk` on restore. |
| `--disk-mount MNTPOINT` | `/data` | Guest mountpoint for `--disk` (fresh boot; baked into the snapshot on restore). |
| `--outcome-report PATH` | none | Write a bounded local JSON outcome report. |
| `--cmdline TEXT` | empty | Append kernel parameters; `nvx_*` and `tsc=` tokens are reserved. |
| `--memory-backing-file PATH` | none | Fresh hvf boot only: file-backed guest RAM, required for `--save-snapshot` scripted capture (the NIC `snapshot` option is added automatically). |
| `--save-snapshot DIR` | none | Scripted capture: wait for `--save-on`, run `--save-exec`, then send `snap DIR` to the openvmm REPL (Ctrl-Q + probe sync), wait for `snapshot saved`, shut down. The leaf must not exist; when the parent is a symlink, pass the real path (`/private/tmp/x`, not `/tmp/x` on macOS). |
| `--save-on MARKER` | none | Output marker that starts `--save-snapshot` (e.g. `VIRTDISK-OK`). |
| `--save-exec CMD` | none | Guest shell line after `--save-on`, before capture. Requires `--save-ready`; needs a console shell (Alpine direct boot). |
| `--save-ready MARKER` | none | Marker that `--save-exec` finished; capture starts here. |
| `--save-timeout SECONDS` | `600` | Per-wait timeout for the save marker and the snapshot-saved marker. |
| `--restore-snapshot PATH` | none | Restore the immutable machine contract and saved state from a snapshot directory. |
| `--restore-processors {1,2,4,8}` | none | Bring this contiguous processor prefix online before restore readiness. Requires an opt-in microVM snapshot and cannot exceed `--processors` capacity. |
| `--restore-memory-mib MIB` | none | Select the 128 MiB-aligned RAM target for an expansion-capable snapshot restore. |
| `--restore-ready-path PATH` | none | Publish one restore-readiness event to an existing Unix socket or Windows named pipe. |
| `--dry-run` | off | Print the generated OpenVMM command without running it. |

The command requires the OpenVMM release binary, `build/vmlinux`, and the
selected `build/initramfs*.cpio.gz`. Ubuntu selection never falls back to
Alpine. See [Run](run.md) for host setup, guest shutdown, networking, and
virtio-fs examples.

### `sandbox`

```text
python3 scripts/nvx.py sandbox
    [{run,provision,start,exec,stop,deprovision}]
    [--layer ROLE,PATH,EROFS_UUID]...
    [--scratch PATH]
    [--state-dir PATH]
    [--entrypoint PATH]
    [--arg VALUE]...
    [--hostname NAME]
    [--workload-user UID:GID]
    [--memory-max BYTES]
    [--pids-max COUNT]
    [--memory-mib MIB]
    [--timeout SECONDS]
    [--exec-timeout-ms MILLISECONDS]
    [--hypervisor {auto,whp,kvm,mshv}]
    [--mount GUEST_TARGET,HOST_PATH[,ro|rw]]
    [--mount-deny HOST_PATH]...
    [--net IPV4/PREFIX]
    [--network-profile {portable}]
    [--network-egress {allow,deny}]
    [--network-ingress {allow,deny}]
    [--network-egress-allow CIDR[:PROTOCOL:PORT]]...
    [--network-egress-deny CIDR[:PROTOCOL:PORT]]...
    [--host-loopback {allow,deny}]
    [--network-proxy IPV4:TCP-PORT]
    [--host-loopback-forward PROTOCOL:HOST_PORT:GUEST_PORT]...
    [--outcome-report PATH]
    [--cmdline TEXT]
    [--dry-run]
```

Network policy and live-share options configure only the `run` and `provision`
launches.

| Option | Default | Description |
| --- | --- | --- |
| `{run,provision,start,exec,stop,deprovision}` | `run` | Select a one-shot run or a managed lifecycle operation. |
| `--layer ROLE,PATH,EROFS_UUID` | required for `run` and `provision` | Attach a `distro`, `runtime`, or `custom` EROFS layer. Repeat once per distinct role. |
| `--scratch PATH` | required for `run` and `provision` | Attach a preformatted ext4 scratch image as the writable overlay. |
| `--state-dir PATH` | required for managed operations | Select persistent sandbox state. One-shot `run` rejects this option. |
| `--entrypoint PATH` | `/bin/sh` | Select an absolute workload entrypoint without whitespace. |
| `--arg VALUE` | none | Append one whitespace-free entrypoint argument. Repeat to pass multiple arguments. |
| `--hostname NAME` | `nvx-sandbox` | Set the workload UTS hostname. |
| `--workload-user UID:GID` | `65534:65534` | Select the fixed non-root workload identity for `run` or `provision`. |
| `--memory-max BYTES` | none | Set the workload cgroup memory limit. |
| `--pids-max COUNT` | none | Set the workload cgroup process limit. |
| `--memory-mib MIB` | `256` | Set guest memory in MiB. |
| `--timeout SECONDS` | `60` | Set the control response timeout for managed `start`, `exec`, and `stop`. |
| `--exec-timeout-ms MILLISECONDS` | `0` | Set the managed `exec` guest workload timeout; zero disables it. |
| `--hypervisor {auto,whp,kvm,mshv}` | `auto` | Select the host hypervisor. |
| `--mount GUEST_TARGET,HOST_PATH[,ro\|rw]` | none | Live-share one host directory at the absolute target inside the container rootfs for `run` or `provision`; defaults to `ro`. `/`, `/etc`, and the `/proc`, `/sys`, `/dev`, and `/.nvx-agent` trees are reserved. |
| `--mount-deny HOST_PATH` | none | Hide one existing file or directory inside the `--mount` host directory; relative paths are resolved inside it. Repeat to deny multiple paths. |
| `--net IPV4/PREFIX` | none | Enable virtio-net with a static guest address. |
| `--network-profile {portable}` | none | Select the required cross-platform network behavior contract; must be specified with `--net`. |
| `--network-egress {allow,deny}` | `allow` | Set the default guest egress policy for `run` or `provision`. |
| `--network-ingress {allow,deny}` | `deny` | Set the host ingress policy for `run` or `provision`. The portable profile supports only `deny`. |
| `--network-egress-allow CIDR[:PROTOCOL:PORT]` | none | Allow matching guest egress; repeat to add rules. Requires explicit `--network-egress`. |
| `--network-egress-deny CIDR[:PROTOCOL:PORT]` | none | Deny matching guest egress; repeat to add rules. Requires explicit `--network-egress`; deny rules take precedence. |
| `--host-loopback {allow,deny}` | existing mapping | Control guest access to host loopback services for `run` or `provision`. |
| `--network-proxy IPV4:TCP-PORT` | none | Allow one explicit host TCP proxy endpoint; the IPv4 address must match the guest gateway. |
| `--host-loopback-forward PROTOCOL:HOST_PORT:GUEST_PORT` | none | Publish one TCP or UDP localhost port to the guest; repeat to add forwards and set `--host-loopback allow`. |
| `--outcome-report PATH` | none | Write a bounded local JSON outcome report for one-shot `run` or managed `exec`. |
| `--cmdline TEXT` | empty | Append non-sandbox kernel parameters; `nvx_*`, `virtfs_*`, and `tsc=` tokens are reserved. |
| `--dry-run` | off | Print the generated OpenVMM microVM command without running it. |

See [Run](run.md) for artifact preparation, the security boundary, and current
snapshot/configuration limitations.

### `snapshot`

```text
python3 scripts/nvx.py snapshot verify SNAPSHOT_DIR
```

| Subcommand | Description |
| --- | --- |
| `verify SNAPSHOT_DIR` | Validate a snapshot directory before restoring it: all three artifacts are present as regular files (never symlinks), the manifest parses with a supported version, and the recorded memory/state lengths match the files on disk. Prints the manifest summary (version, architecture, guest RAM, vCPU count, boot mode, device-state root) and `snapshot OK`. |

See [Run](run.md) for the macOS/HVF save/restore flow, snapshot chaining,
and restore-time disk requirements.

## Benchmarking

### `benchmark`

```text
python3 scripts/nvx.py benchmark [OPTIONS]
```

| Option | Default | Description |
| --- | --- | --- |
| `--suite {boot,snapshot,restore,e2e,phase2,snapshot-profile,all,cold-start,device-io,device-restore-profile,network-snapshot,performance,shell-snapshot,shell-snapshot-restore,snapshot-restore-memory,snapshot-restore-vcpu,virtfs}` | `boot` | Select an acceptance, diagnostic, or workload suite. |
| `--backend {whp,kvm,mshv,both}` | `both` on Windows; `kvm` elsewhere | Select the hypervisor backend. |
| `--platform NAME` | inferred OS/backend | Record the host-typed performance series. |
| `--openvmm-dir PATH` | `openvmm/` | Select the OpenVMM repository. |
| `--nvx-dir PATH` | repository root | Select the NVX repository containing guest artifacts. |
| `--warmups N` | `5` for `device-io`; `1` for `device-restore-profile`; `3` otherwise | Set the number of excluded warmup attempts; zero is allowed. |
| `--runs N` | `30` for `device-io`; `5` for `device-restore-profile`; `11` otherwise | Set the number of retained attempts. |
| `--memory-mib MIB` | `128` | Set guest memory for the general suites. |
| `--processors {1,2,4,8}` | `1` | Run every cold, capture, restore, and workload launch with this microVM count. |
| `--virtfs-runs N` | `3` | Set the number of virtio-fs workload samples. |
| `--virtfs-memory-mib MIB` | `512` | Set guest memory for the virtio-fs workload. |
| `--payload-mib MIB` | `64` | Set the virtio-fs sequential I/O payload size. |
| `--shell-memories MIB [MIB ...]` | `128 256 512` (`128 256 512 1024` for `snapshot-profile`) | Set the guest memory sizes for shell snapshot measurements. |
| `--network-memory-mib MIB` | `256` | Set guest memory for the network snapshot workload. |
| `--restore-devices {console,net,virtiofs} [...]` | all three devices | Select devices for the `device-restore-profile` suite. |
| `--restore-modes {active,deferred} [...]` | both modes | Select activation modes for the `device-restore-profile` suite. |
| `--device-io-duration-seconds SECONDS` | `10` | Set each storage-operation or UDP round-trip measurement window. |
| `--device-io-size-mib MIB` | `512` | Set the virtio-blk and virtio-fs backing-object size. |
| `--device-io-port PORT` | `5201` | Set the same-host UDP echo port. |
| `--net IPV4/PREFIX` | none | Enable virtio-net with a static guest address. |
| `--network-profile {portable}` | none | Required with `--net`; selects the portable KVM/MSHV/WHP contract. |
| `--cpus CPUSET` | one logical CPU per physical core | Set process affinity in `taskset` syntax. |
| `--host-cpu-reserve N` | `2` | Require this many affinity CPUs beyond the guest vCPU count for VMM/device work. |
| `--timeout SECONDS` | `10` | Set the time allowed for each boot marker. |
| `--teardown-mode {guest-exit,host-terminate,host-sigterm}` | `guest-exit` | Select how to stop a measured VM; `host-sigterm` is a deprecated alias. |
| `--skip-build` | off | Reuse existing release binaries. |
| `--snapshot-profile` | off | Retain OpenVMM lifecycle phase samples and host counters; implied by the `snapshot-profile` suite. |
| `--cache-state {warm,cold,both}` | `both` | Select artifact cache states for the `snapshot-profile` suite. |
| `--output PATH` | none | Write the benchmark result as JSON. |
| `--output-dir PATH` | none | Write canonical workload logs to a directory. |
| `--scratch-dir PATH` | system temporary directory | Select an existing directory for temporary snapshots, guest RAM backing, and workload files. |
| `--keep-kvm-stage` | off | Keep temporary staged KVM benchmark binaries. |

Measured counts must be at least 1; warmups may be zero, and timeouts must be greater than zero.
The `e2e` suite uses the general memory size and measures cold start, snapshot
generation, snapshot restore, guest-exit teardown, and peak RSS against the
shell-ready markers. CI uses the default 128 MiB baseline.
See [Benchmark](benchmarks.md) for suite semantics, platform support, metric
definitions, and complete examples.

### `performance`

`performance` processes benchmark outputs for CI. It requires one nested
command.

#### `performance collect`

```text
python3 scripts/nvx.py performance collect
    --platform PLATFORM
    --commit COMMIT
    --input-dir PATH
    --output-dir PATH
    [--require-network]
    [--require-shell-snapshot]
    [--require-shared-suite]
    [--require-shell-snapshot-restore-512]
    [--lifecycle-input PATH]
    [--summary PATH]
```

Parses canonical benchmark logs into p50 CSV files. The `--require-*` flags
reject incomplete inputs for their respective workload sets.
`--require-shell-snapshot-restore-512` accepts only the canonical 512 MiB
restore metric from a 2-, 4-, or 8-vCPU run.
`--lifecycle-input` validates and merges a 128 MiB, guest-exit `e2e` JSON
result, producing the 31-metric microVM CI result. A directory whose metadata
selects `device-io` is collected as five additional ABI-2, one-vCPU `ops/s`
metrics; CI merges them into a 36-metric one-vCPU result.
`--summary` writes the p50 table plus lifecycle min/max/sample-count and RSS diagnostics.

#### `performance validate-openvmm`

```text
python3 scripts/nvx.py performance validate-openvmm
    --platform PLATFORM
    --input PATH
```

Validates a complete 128 MiB, guest-exit OpenVMM `e2e` result without
writing a CSV. Snapshot-generation instability exits with status 75 so
callers can remeasure the temporary host condition selectively; other
malformed or incomplete inputs exit with status 2.

#### `performance collect-openvmm`

```text
python3 scripts/nvx.py performance collect-openvmm
    --platform PLATFORM
    --commit COMMIT
    --input PATH
    --output-dir PATH
    [--summary PATH]
```

Converts a complete 128 MiB, guest-exit OpenVMM `e2e` result into an
eight-metric lifecycle p50 CSV.

#### `performance gate`

```text
python3 scripts/nvx.py performance gate
    --baseline-dir PATH
    --target-dir PATH
    [--window N]
    [--minimum-history N]
    [--threshold PERCENT]
    [--absolute-tolerance-ms MILLISECONDS]
    [--summary PATH]
```

Checks target p50 values for regressions against rolling baseline histories.
`--window` defaults to `10`, `--minimum-history` to `10`, `--threshold` to
`40`, and `--absolute-tolerance-ms` to `5`. The gate uses the median of the
available window and treats metrics with insufficient history as warmups.
`--summary` writes a Markdown summary.

#### `performance persist`

```text
python3 scripts/nvx.py performance persist
    --source-dir PATH
    --history-dir PATH
    [--exclude-metric NAME]...
```

Appends current p50 values to branch history. Repeat `--exclude-metric` to omit
more than one metric.

## Source and package commands

### `collect-sources`

```console
python3 scripts/nvx.py collect-sources
```

Materializes the verified Linux, Alpine, and Ubuntu source artifacts needed
for a source-inclusive release.

### `collect-alpine-sources`

```text
python3 scripts/nvx.py collect-alpine-sources MANIFEST [MANIFEST ...]
    [--output PATH]
    [--cache PATH]
    [--skip-upstream]
```

| Option | Default | Description |
| --- | --- | --- |
| `MANIFEST` | required | One or more Alpine package manifests to collect. |
| `--output PATH` | `build/sources/alpine` | Select the output directory. |
| `--cache PATH` | `.cache/aports` | Select the aports cache directory. |
| `--skip-upstream` | off | Collect exact aports recipes without running `abuild fetch`. |

### `collect-ubuntu-sources`

```text
python3 scripts/nvx.py collect-ubuntu-sources MANIFEST [MANIFEST ...]
    [--output PATH]
    [--cache PATH]
```

| Option | Default | Description |
| --- | --- | --- |
| `MANIFEST` | required | One or more Ubuntu package or EROFS manifests to collect. |
| `--output PATH` | `build/sources/ubuntu` | Select the output directory. |
| `--cache PATH` | `.cache/ubuntu-source-indexes` | Select the downloaded source-index cache. |

The collector requires `gpgv`, deduplicates source package name/version pairs,
authenticates live or historical Ubuntu source indexes through signed
`InRelease` metadata and the pinned Ubuntu archive keyring, validates each
`.dsc`, and downloads every referenced source member.

### `create-linux-source-archive`

```text
python3 scripts/nvx.py create-linux-source-archive
    --config PATH
    --output PATH
```

Creates the Linux corresponding-source archive using the required kernel
configuration and output paths.

### `package`

```text
python3 scripts/nvx.py package
    [--version VERSION]
    [--destination PATH]
    (--include-source | --binary-only)
    [--force]
```

| Option | Description |
| --- | --- |
| `--version VERSION` | Override the packaged version. |
| `--destination PATH` | Override the staging destination. |
| `--include-source` | Include the corresponding source artifacts in the package. |
| `--binary-only` | Stage binaries only; publish corresponding source separately. |
| `--force` | Replace a legacy staging directory beneath `dist/`. Self-contained packages require a new destination. |

Exactly one of `--include-source` and `--binary-only` is required. See
[Package and source delivery](distribution.md) for release procedures and
source-publication requirements.

### `archive-release`

```text
python3 scripts/nvx.py archive-release
    --source PATH
    --destination PATH
```

Validates the staged distribution against its `SHA256SUMS`, snapshots the
accepted inventory, and creates a deterministic archive at the destination.
The destination must be outside the source directory and end in `.tar.gz` or
`.zip`.

## Diagnostics and ARM sandbox parity

| Command or option | Behavior |
| --- | --- |
| `doctor [--backend kvm\|mshv\|whp\|hvf] [--json]` | Check artifacts, host hypervisor access, entitlement, Docker, cache, and provenance; any failed check exits nonzero. |
| `setup` | Apply and verify the macOS hypervisor entitlement with ad-hoc signing for local development. |
| `explain STATE_DIR` | Explain recorded policy denials and print the specific enabling option. |
| `run --events PATH` | Write bounded, redacted, host-side JSON-lines lifecycle events. |
| `sandbox --hypervisor hvf` | Run EROFS layers over a private ext4 overlay on Apple Silicon. Supports managed provision/start/exec/stop. |
| `sandbox --memory-mib N` | Default RAM is 1024 MiB on HVF and 256 MiB on x86 sandbox backends. |
| `run --cmdline nvx_host_clock=off` | Disable boot clock repair for the clock negative control. Normal boots sample the current host clock. |

The host resolves snapshot parent paths before passing them to OpenVMM, so the
macOS `/tmp` alias is accepted. File members of a snapshot are still checked
without following symlinks. See [cookbook](cookbook.md) for complete commands.

### OCI images and resolved policy

The image front door runs through the authenticated managed agent. Conversion happens
before launch, using a networkless Linux tools container; it never executes the image's
entrypoint. `image pull` contacts the registry through Docker. The manifest records the
resolved OCI config/layer digests and the generated EROFS/scratch digests.

| Command / option | Behavior |
| --- | --- |
| `image pull REF` / `image convert REF` | Pull then convert, or convert an existing Docker image |
| `--curated-base` on conversion | Register an exact ordered layer-digest prefix as a distro base |
| `image ls` / `image verify REF` / `image rm REF` | List, rehash, or remove an unreferenced image; live/snapshot leases prevent removal |
| `run --image REF -- COMMAND ARG...` | Use implicit read-only layers and private preformatted scratch; preserves argv and image environment defaults |
| `--profile default` | UID/GID 65534, empty capabilities, NNP, allowlist seccomp, device filter, masked paths, deny egress, 128 workload PIDs, 256 MiB workload memory and 60 s wall cap |
| `--profile ci` | Same isolation defaults; permits explicit egress allow rules and `--cap-add MKNOD` for device-filter testing |
| `--profile risky` | Root, all capabilities, no seccomp/device/mask/NNP/resource restrictions, allow egress; explicit negative-control profile |
| `--seccomp unconfined` | Disable seccomp while retaining the other selected-profile restrictions |
| `--config FILE` / `policy show` / `policy lint` | Resolve `[policy]` in TOML; explicit CLI settings override file settings, which override the selected profile |
| `--pids-max N` / `--memory-max BYTES` / `--exec-timeout-ms MS` | Override workload resource caps; zero wall timeout disables that cap |
| `verify-guest-determinism --image REF` | Reconvert twice and compare the complete manifest identity; repeat for two or more images |

The high-level image path defaults to portable networking on `192.168.127.0/24` and
records packet decisions. Denied canonical TCP/UDP traffic gets a local TCP reset or
ICMP administrative prohibition. Other malformed/spoofed traffic is dropped. The
`NVX_NETWORK_LEGACY_DROP` host environment variable is a test control that restores
blackhole behavior; the fast-fail scenario requires that control to time out.

Use numeric IPv4 policy destinations with the existing
`--network-egress-allow IPV4:tcp:PORT` form. `default` rejects allow rules; use `ci`.
A live-share or gateway-loopback opt-in remains an explicit authorization.

`nvx-default` also filters arguments: `clone` rejects new user, mount, network,
PID, UTS, IPC and cgroup namespace flags. `clone3` returns `ENOSYS`, allowing libc
to fall back to filtered `clone`, because classic seccomp cannot inspect its
pointed-to argument structure. `socket` permits only Unix, IPv4 and IPv6 domains;
netlink and packet sockets return `EPERM`. A kernel without IPv6 support can still
return `EAFNOSUPPORT` for that permitted domain. Ordinary forks and threads are
tested alongside the denials.

### Run receipts and containment

| Command / option | Behavior |
| --- | --- |
| `run --image REF --receipt FILE` | Publish receipt v1 after confirmed teardown; the instance retains its receipt and evidence |
| `--receipt-signing-key FILE` | Sign canonical receipt body bytes with an Ed25519 PEM private key in process using `cryptography` |
| `receipt verify FILE [--evidence-dir DIR]` | Verify body integrity and every referenced local evidence file |
| `receipt verify FILE --public-key FILE` | Additionally verify an Ed25519 signature against the caller's trusted public key |
| `containment run --backend BACKEND --format md` | Run all eight probe families; each requires an observed failing negative control |
| `containment render RESULT.json --format md` | Render a completed, validated result document |

An unsigned digest detects changed content; authenticity requires a signature and a
trusted key. Receipt flow verdicts describe endpoint policy decisions, not remote
application success. Resource numbers come from the outer agent's cgroup counters.
The existing `--outcome-report` schema and separated exec streams remain available.

Install the optional pinned signing dependency with
`python3 -m pip install -r requirements-signing.txt` in the NVX installation.
Signing and verification read the supplied PEM key but create no temporary message,
signature or key files and invoke no external crypto command. Development and CI
dependencies include this library, so signing tests run rather than skip on macOS.

New snapshot format 6 verifies SHA-256 of RAM and device state on restore, both in the
CLI and in OpenVMM's opened-artifact admission. Versions 2–5 retain their previous
structural compatibility contract; `snapshot verify` labels that limitation. The
containment control deliberately downgrades an isolated test snapshot to version 5 and
shows that its same-length RAM change is admitted. Snapshots are not portable across
architecture or hypervisor. Full RAM hashing adds restore work. Warm-pool measurements
state when admission occurs; this pool admits and hashes clones before marking them ready,
and separately measures requests against those ready clones.


### Workspaces, files, retained instances, and credentials

| Command / option | Behavior |
| --- | --- |
| `--workspace HOST:GUEST[:ro|rw]` | Export one directory through virtio-fs; defaults to read-only. Reserved guest roots and symlink escapes are rejected. |
| `--out NEW_DIR` | Publish successful private `/out` regular files after teardown; failed workloads publish no outputs. The destination must be new. |
| `--keep-alive` | Retain the managed VM after the first command; use its printed `NVX-ID` for later operations. |
| `exec ID -- COMMAND ARG...` | Execute with separate stdout/stderr and the guest's numeric exit status; `--stream` forwards frames immediately. |
| `cp LOCAL ID:/guest/path` / `cp ID:/guest/path LOCAL` | Transfer regular files byte-exact, with a 256 MiB bound and new destinations. |
| `logs ID --json [--follow]` / `ps` / `stop ID` | Ordered events, actual process/RSS/status, and idempotent confirmed teardown with a final receipt. |
| `bundle ID --output FILE` / `bundle verify FILE` | Reproducible evidence archive with checked inventory; excludes capabilities and VM RAM. |
| `--secret NAME` | Bind a host environment value to a disposable host proxy. API values never enter argv or guest environment. |
| `--egress-allow HOST:PORT` with `--secret` | Admit an exact TLS destination to credential injection; request Authorization/Cookie headers are stripped. |
| `--secret-header NAME:HEADER` / `--proxy-log FILE` / `--proxy-ca FILE` | Override injection header, retain redacted proxy decisions, or add a trusted upstream CA. |
| `--env NAME=VALUE` | Ordinary guest environment data; values are visible in guest RAM and snapshots. |

Mac exports pin their root directory and map the admitted workload identity to the host
export owner. They do not impersonate Linux process credentials. Special files and host
symlink escapes are denied. File outputs are bounded to 256 MiB and 10,000 entries.
Credential bindings are excluded from warm snapshots; cloning a credential-bound template
requires a fresh host binding and is currently rejected rather than reusing stale state.

### Warm templates, pools, and performance

| Command / option | Behavior |
| --- | --- |
| `warm --image REF [--backend BACKEND] --output DIR` | Capture a version-6 snapshot and frozen private scratch, bound to image, policy, artifact versions, architecture and backend. The default backend is HVF on macOS, KVM on Linux, or WHP on Windows. |
| `--runtime auto\|python\|dispatcher` | Python images retain a single-threaded initialized Python process behind a command barrier; other images retain the managed dispatcher. |
| `--profile ci --egress-allow IP:PORT` on `warm` | Bake an explicit default-deny network policy into the template. |
| `pool start --template DIR --size N` | Admit and restore N private clones before reporting readiness. Quota admission precedes VM creation. |
| `pool start --image REF --backend BACKEND --size N` | Prepare a template and then populate the pool. |
| `run --pool ID -- COMMAND ARG...` | Lease one ready clone, execute, retire it, write its receipt, and refill with a fresh clone. |
| `pool status ID` / `pool stop ID` | Inspect ready/leased/retired accounting, or stop all owned clones and wait for refill cleanup. |
| `benchmark --suite warm-pool --backend BACKEND --template DIR --runs 20 --output FILE` | Measure client request to first workload stdout, full completion, and the same workload's cold path. |
| `performance collect-warm --platform NAME --commit SHA --input FILE --output-dir DIR` | Validate sample medians and collect the three metrics as ordinary gated CSV results. |

Each restored clone repairs its trusted generation ID, machine ID, hostname, host clock,
and CRNG before admitting work. Python workload children close the private control FDs;
the parent is non-dumpable. A pool lease is never returned for reuse. RAM/state hashing and
clone initialization occur before a clone enters the ready pool. The benchmark explicitly
excludes pool preparation and refill from request timing.

`test-microvm --scenario showcase-simulants` runs ransomware, identity, fork spawning,
and exfiltration under the default policy, then requires each risky control to fail containment.

### Portable MCP and SDKs

`mcp serve [--backend BACKEND]` selects the host backend and uses stdio by default. `--transport http` binds only
127.0.0.1, prints its URL and a private capability-file path, and requires bearer
authentication. It implements MCP **2025-06-18**, including initialization, tool discovery,
streaming progress and `notifications/cancelled`. HTTP disconnect alone does not cancel work.

The six tools are `nvx_run`, `nvx_exec`, `nvx_status`, `nvx_snapshot`, `nvx_files`, and
`nvx_secrets`. The server owns only instances it created. File transfers stay beneath its
private `files` directory; secret discovery returns names and availability only.
`--secret-name NAME` explicitly admits a host binding. Duplicate idempotence handles replay
the same result; reuse with different arguments fails. Default limits are four concurrent
sandboxes, one vCPU per VM/four aggregate, 1024 MiB per VM/2048 MiB aggregate, 60 s wall,
and 4096 MiB retained snapshot storage. CLI quota options can lower or raise these bounds.
Python and TypeScript clients are in `sdk/`; installed packages include both clients and
compiled TypeScript output. Python and Swift consume the committed MCP contract vectors.

### ARM packages and installation

`package --platform darwin-arm64|linux-arm64 --binary-only --destination DIR` stages a
self-contained CLI, both SDKs, core executable, architecture-specific guest artifacts,
licenses, provenance and SHA256SUMS. Strict packages require a clean pinned core build.
macOS additionally requires Developer ID signing, its hypervisor entitlement and a
successful notarization assessment. `--development` labels a local preview explicitly.
Use `--include-source` when matching corresponding sources have been collected.

`archive-release --source DIR --destination FILE.tar.gz` snapshots the accepted inventory.
`install --archive FILE.tar.gz --destination NEW_DIR` verifies and installs the whole
runtime without overwriting another installation. Its entry point is `NEW_DIR/bin/nvx`.
`download` selects the host's ARM asset and retains the complete runtime alongside installed
artifacts. A published release and signing credentials are required for the remote
`download → doctor → run` release gate; a local archive smoke is reported separately.
