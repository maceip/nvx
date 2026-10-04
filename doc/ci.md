# Continuous integration

The GitHub Actions workflow has two microVM test layers on Azure-hosted
self-hosted KVM, MSHV, and WHP virtual machines. Each backend has a pool of
three runners labeled by operating system, backend, and `virtual-machine`.
Jobs target the shared backend labels so any available matching runner can
execute them. This allows the backend lanes to execute concurrently without
binding a workload to a specific host. `openvmm-vmm-tests` downloads the NVX
guest artifacts and uses the Linux-direct kernel and Alpine initramfs to
exercise OpenVMM's Linux MP-table lifecycle, TTRPC, and snapshot contracts.
`openvmm-unit-tests` runs the OpenVMM unit and documentation tests independently
on the same backend matrix.
Failed `openvmm-vmm-tests` jobs upload Petri's `test_results` directory,
including guest and VMM logs, screenshots, and watchdog inspection data.
These seven-day artifacts are named
`openvmm-vmm-tests-<os>-<backend>-<run-id>-<run-attempt>`, so a successful rerun
does not replace the failed attempt's diagnostics. Linux collects them from
`openvmm/target/vmm_tests/test_results`; Windows uses
`<runner-temp>/<backend>/test_results`.
The `nvx-microvm-tests-{kvm,mshv,whp}` jobs consume the NVX Linux kernel and
the NVX Linux kernel plus the selected Alpine or Ubuntu initramfs and exercises
Linux, SMP, virtio, sandbox, and snapshot behavior through the public OpenVMM
CLI. Alpine-control-only scenarios remain explicit and are rejected for the
Ubuntu initramfs. Failure logs from the NVX layer are uploaded per backend.
The restore-processor scenario also rejects Linux TSC instability diagnostics,
even if the requested CPUs came online, so clock skew cannot silently pass by
falling back to a different clocksource. After the 1/2/4/8-CPU restores, it
restores the same snapshot once without `--restore-processors`. Every restore
runs with OpenVMM lifecycle profiling and must report exactly one
`startup.vp_thread_bind` record. Its `startup.vp_bind_*` records must show that
an explicit MSHV target binds exactly VPs `0..N-1`, while untargeted MSHV
restores and all KVM and WHP restores bind the full capacity.
On MSHV and WHP, the capture waits until Linux replaces its transitional
`tsc-early` clocksource. A snapshot taken earlier can fail after restore
without any cross-CPU skew, because the clocksource watchdog compares
`tsc-early` with jiffies across the restore downtime, as described in
[the benchmark guide](benchmarks.md).
A restore fails as soon as its guest prints `NVX-RESTORE-PROCESSORS-FAIL`,
rather than waiting for the phase timeout. Restore logs also record OpenVMM's
`adjusted restored vCPU TSC` event for each VP, which includes the applied
snapshot downtime, and its `aligning restored AP TSCs to the BSP` event, which
reports how many created MSHV APs were aligned. When the guest reports
`unstable-tsc`, the harness boots a never-restored eight-vCPU guest with the
same forced warp check and reactivates each AP 20 times. The error then states
whether this control also found TSC instability, which points to host or
hypervisor clock skew rather than restore alignment, and whether the host CPU
exposes an invariant TSC. The control log is kept as
`restore-processors-tsc-control.log`. The control only classifies the
failure; the restore still fails.

Linux runners must expose an invariant TSC, reported as `nonstop_tsc` in
`/proc/cpuinfo`. The `validate-runner` action prints each runner's kernel, CPU
model, clocksource, and TSC flags, and fails the job when `nonstop_tsc` is
missing. On an MSHV runner VM whose Azure host hid the invariant TSC,
never-restored guests also hit cross-vCPU TSC warps during CPU activation, and
keeping every host CPU out of idle removed them (#211). Redeploy such a VM on a
host that exposes an invariant TSC instead of retrying its jobs.

The `restore-tsc-sync` scenario repeats the restore-processor sequence with
the test-only kernel option `clearcpuid=tsc_adjust`. Linux normally skips its
cross-CPU TSC warp test when `IA32_TSC_ADJUST` is available and consistent
within a package. This scenario verifies that the feature is masked, forcing
the live CPU-online check even on those hosts, while retaining the existing
TSC-instability guard. It does not force a fallback clocksource or retry failed
restores. Its logs are kept in a separate `restore-tsc-sync` subdirectory.
Run it alone on Windows with:

```powershell
python scripts\nvx.py test-microvm --backend whp --scenario restore-tsc-sync
```

This regression targets the WHP clock instability tracked in #19; a passing
frozen-counter check is not sufficient to validate a fix.

The `console-exit` scenario delays host console reads for two seconds after
snapshot restore to exercise output backpressure. For each requested processor
count it requires byte-exact delivery of a 64 KiB payload and the final marker,
and preserves guest exit statuses 0 and 37. This checks both device and host-relay
draining without adding sleeps to the measured benchmark workloads.
The harness waits for the output reader's EOF notification even after the
process exits, so delayed final output chunks cannot create a false failure.

Shared guest artifacts are built with Docker on a GitHub-hosted Ubuntu runner.
The kernel, Alpine initramfs, Ubuntu initramfs, and Ubuntu EROFS layer use
separate cache keys. Ubuntu keys include the Canonical archive pin,
supplemental package lock, common guest sources, shared download and guest
descriptor modules, converter implementation, and Dockerfile. Artifact upload
retains the Alpine filenames and adds the distinct Ubuntu filenames. Each
backend also boots the Ubuntu initramfs and runs
`/sbin/nvx-sandbox-smoke` from the Ubuntu EROFS layer as UID/GID 65534 over a
fresh ext4 scratch copy. The same entrypoint then verifies a live virtio-fs
share inside the container: a read-write `/workspace` share with a denied
subdirectory must round-trip guest writes to the host, and a read-only
`/opt/hostedtoolcache` share must reject writes. A managed sandbox then repeats
the read-write check through `provision`, `start`, `exec`, and `stop`, and must
report a successful outcome with a cleanly unmounted scratch filesystem, which
shows that `stop` unmounted the share and overlay first. Linux/KVM runs the
broader Ubuntu SMP, managed lifecycle, network snapshot, blockless snapshot,
and workload-identity set.

OpenVMM release executables and provenance are built once by the independently
addressable `build-openvmm-linux-gnu`, `build-openvmm-linux-musl`, and
`build-openvmm-windows-msvc` producer jobs. KVM workloads and MSHV microVM tests
consume the GNU artifact, MSHV platform workloads consume the musl artifact,
and WHP workloads consume the Windows MSVC artifact. Each workload can start
after its compatible OpenVMM producer and the shared guest-artifact job finish,
without waiting for unrelated OpenVMM targets.

All three producers call the same Python build workflow, passing the validated
runner backend explicitly through `build-openvmm --backend`. The backend is
carried in `OpenVmmBuildConfig`; the build workflow maps KVM, MSHV, or WHP to
GNU, musl, or MSVC without probing runtime devices. CI therefore retains its
musl build for MSHV without maintaining a separate shell build path.

The kernel and initramfs cache keys include
[`build_config.py`](../scripts/nvx_tools/build_config.py) and
[`build_constants.py`](../scripts/nvx_tools/build_constants.py), so shared build
configuration or constant changes invalidate cached guest artifacts and their
provenance. The Ubuntu distro layer shares the Ubuntu input hash.

The producer handoff uses one-day workflow artifacts rather than caches. Each
consumer downloads both the normalized executable and its build provenance,
then restores executable permissions on Linux. Once the required artifacts are
ready, benchmarks run in parallel with the NVX test layer and use any available
runner in the matching backend pool. All three use virtual-machine performance
series and the constrained eight-CPU affinity policy. Development releases and
performance baseline updates still require every applicable test and benchmark
lane to pass. The workflow uses the read-only OpenVMM deploy key stored in the
`OPENVMM_DEPLOY_KEY` Actions secret to fetch the private submodule at its pinned
commit. Shared guest binaries and development release packages move through
short-lived workflow artifacts alongside the OpenVMM handoff and benchmark
results. Caches only accelerate reproducible build inputs and outputs; consumers
do not depend on them as a handoff.
Pull requests gate regressions against recent matching-platform history, and
successful pushes to `dev` append their p50 values under `data/`. Metadata-only
performance jobs use GitHub-hosted Ubuntu runners. Provisioning instructions
are in the [runner bootstrap guide](../scripts/setup/README.md).

Persistent runners accept pushes and same-repository pull requests only. Fork
pull requests run the GitHub-hosted validation jobs but do not execute code on
the Azure runner fleet. A maintainer must stage an external contribution on a
trusted repository branch before running the backend matrices.

## Adversarial campaigns

The separate
[`adversarial.yml`](../.github/workflows/adversarial.yml) workflow runs
Copilot-driven campaigns only on trusted manual dispatches or schedules from
`dev`. It is not part of pull-request CI. The workflow's dedicated
`nvx-adversarial-controller` runner must already have an authenticated Copilot
CLI and an administrator-owned executor wrapper named by the
`NVX_ADVERSARIAL_EXECUTOR` repository variable. The workflow does not install
Copilot or initiate login.

The wrapper provisions a distinct disposable KVM, MSHV, or WHP target with no
production or GitHub credentials and forwards only the typed executor
protocol. Existing persistent microVM and performance runners are not valid
adversarial targets. Loss of the target heartbeat, a policy oracle, or a
teardown/post-campaign boot failure fails the job and requires quarantine and
reimage.

Normal Actions artifacts contain only the guest-text-free public summary,
catalogued case identifiers, and replay manifest. The external provisioner
must collect controller transcripts and complete target logs into
access-controlled security storage. See
[Copilot-driven adversarial testing](design/copilot-adversarial-testing.md)
for the architecture and operational contract.

## Apple Silicon manual pre-release gate

The [NVX microVM tests / HVF workflow](../.github/workflows/nvx-microvm-tests-hvf.yml)
is triggered manually with GitHub Actions **Run workflow** (`workflow_dispatch`).
It selects a trusted Apple Silicon
runner labeled `nvx-hvf`. Until that runner is enrolled, run this gate locally
and attach the entire `build/test-results/microvm-hvf` directory to the release:

```sh
python3 scripts/nvx.py setup
python3 scripts/nvx.py doctor --backend hvf
python3 scripts/nvx.py test-microvm --backend hvf --output-dir build/test-results/microvm-hvf
```

Docker, the Rust build tools, a matching ARM kernel/initramfs, and the signed
OpenVMM executable must be installed. The HVF harness builds its EROFS and ext4
fixtures from this checkout. It checks real guest TLS with a current certificate
and repeats with clock sampling disabled; the latter must fail certificate
validity, rather than merely lose network access. Both captures are retained.

The workflow checks out this repository and its pinned OpenVMM submodule, installs
`requirements-dev.txt`, then runs the offline tests, pyright, builds, doctor, all
HVF scenarios and the warm benchmark. It has no dependency on `showcase/`; the
optional Swift app now lives in `maceip/nvx-showcase` and its tests belong there.
This manual workflow supplies **no automatic macOS pull-request coverage**. Until
a persistent trusted runner is enrolled, every release must attach its complete
local `build/test-results/microvm-hvf` directory, generated containment matrix,
warm benchmark JSON, collected CSVs and gate logs. A dispatched run uploads the
scenario directory as `microvm-hvf-evidence`; benchmark and gate files are included
in the same artifact. Promote this to the trusted PR lane when that runner exists.

| Scenario | KVM | MSHV | WHP | HVF |
| --- | --- | --- | --- | --- |
| sandbox-blocks | automated lane | automated lane | automated lane | manual gate |
| structured-outcome | automated lane | automated lane | automated lane | manual gate |
| managed-lifecycle | automated lane | automated lane | automated lane | manual gate |
| workload-identity | automated lane | automated lane | automated lane | manual gate |
| hvf-parity | not applicable | not applicable | not applicable | manual gate with clock negative control |

This table describes gate wiring, not a claim that every backend has passed a
particular checkout. The fixed x86 machine profile and ARM direct-boot platform
use the same sandbox agent and authenticated managed control protocol. HVF
currently supports the listed scenarios; requesting an unsupported scenario is
an error. Snapshots remain architecture and backend bound.


## Plan completion gates and recorded local evidence

The common correctness dispatcher includes `showcase-simulants`, `workspace-lifecycle`, `secret-isolation`,
`warm-clone`, and `mcp-portable`; the Alpine-only set rejects these for Ubuntu. The same
registered scenarios run on the existing KVM/MSHV/WHP lanes. ARM KVM uses the ARM direct
boot device layout and the ARM scenario adapter. No remote result is inferred from wiring.
The configured-host resolver currently reports no `.nvx-hosts.json` profiles in this checkout.

The HVF lane runs a 20-run warm/cold benchmark. Its
three metrics go through `performance collect-warm` and the existing regression gate against
`data/warm`. The first local measured point starts baseline history; `--minimum-history 1`
is an explicit bootstrap guard with 20% relative and 1 ms absolute tolerance. A second
gate always requests the normal ten-point history and reports **Warmup** until enough
independent points exist; it must not be quoted as a measured trend before then.
Request timing starts against an already-resumed ready clone and ends at first workload
stdout; preparation, full-file admission hashing and refill are excluded. This is not the
older x86 bare-guest boot metric. The gate's offline control triples latency and must fail.

`nvx-arm-release.yml` is a manual strict packaging/signing gate for `darwin-arm64` and
`linux-arm64`. It requires enrolled `nvx-hvf`/`nvx-kvm` ARM64 runners. macOS requires the
runner's `NVX_DEVELOPER_ID` and existing `NVX_NOTARY_PROFILE`; Linux requires a private-key
file identified by `NVX_RELEASE_SIGNING_KEY` and signs the inventory as a separate artifact.
Private key material stays on the runner. The workflow packages the CLI and both SDKs,
checks complete artifact inventory, installs into a new directory, runs doctor and a guest
smoke, and retains evidence. Publishing a release and testing remote `download` are separate
gates; the workflow uploads reviewable artifacts and does not publish automatically.

The local Mac has no Developer ID Application identity. Ad-hoc development signing and a
successful local archive install are reported as local evidence only. A Linux ARM executable,
other backend matrices, an accepted Apple notarization ticket, signed release downloads, and
an integration built by another person remain required external results.

## Signed publication pipeline

`nvx-release.yml` builds architecture-specific guests and validates their matching Linux,
Alpine and (on x86) Ubuntu sources. It then requires all six package platforms:
darwin-arm64, darwin-x86_64, linux-arm64, linux-kvm, linux-mshv and windows-whp. Each runtime
lane runs the entire correctness suite, all eight containment families with opposite controls,
OCI determinism, 20 warm/cold measurements, the SDK tests and strict clean-source packaging.
`release-proof.py` records command exits and hashes; `verify-release-matrix.py` rejects missing
platforms, stale commits, changed logs, contained controls, missing sources and zero-metric
performance Warmup. New platforms need a separately measured baseline before promotion.

The checkout action resolves its fork from `.gitmodules` and its revision from the Git index.
Public submodules need no deploy key. Every PR quality job runs all `scripts/test_*.py` tests.

Tag pushes sign the complete archives with [GitHub/Sigstore build attestations](https://github.com/actions/attest).
The selected signature binds the archive digest to this repository, release workflow and tag.
Offline bundles ship alongside each archive. A complete matrix publishes a prerelease candidate;
new jobs download it from the public release, verify the expected workflow/tag/commit, install
into an empty directory and cache, run doctor and boot a real workload. Only successful public
download smoke promotes the candidate to a stable release. A failed candidate stays prerelease.
Workflow dispatch builds and records evidence without publishing.

`nvx-hosted-validation.yml` probes actual `/dev/kvm` access on hosted x86 and ARM Linux runners.
It then builds and runs the full acceptance battery. An unavailable hypervisor fails the lane;
it never becomes a skipped runtime pass. The native Windows and Intel macOS workflows independently build and exercise WHP and
Intel Hypervisor.framework with a Linux OCI converter. ARM KVM runtime evidence requires
a host that exposes nested virtualization; the hosted ARM runner currently does not. The full
publication pipeline still requires the documented runner labels and the Mac's Developer
ID/notary profiles. Hardware admission alone never counts as a guest scenario pass.

The Windows hosted lane keeps its image cache and proof logs under the checkout's
`build/` directory, avoiding cross-drive artifact paths. Failed gates print a bounded,
credential-redacted log tail and upload the complete available evidence. Its native
core binary and provenance are cached by core revision and builder source before
the scenario gate. Restored binaries still require matching clean-source provenance
and a verified digest before acceptance.
