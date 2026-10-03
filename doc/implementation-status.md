# Implementation evidence

The canonical planning documents under `doc/` retain their original acceptance requirements;
their status banners now point here. The two original root paths are relative symlinks
to those same documents. The local implementation covers P0–P5; full plan completion
also requires the external gates below.
Implemented source, local tests, CI wiring, and published release proof are recorded separately.

| Phase | Implemented and exercised locally on Apple Silicon/HVF | External gates still required |
| --- | --- | --- |
| P0 | Doctor/setup/explain and bounded events; ARM EROFS/overlay managed sandbox; separate streams and status 37; guest exit; boot/restore clock and actual TLS, with sampling-disabled controls | Clean public installation and KVM/MSHV/WHP results |
| P1 | OCI pull/convert/verify/list/remove; exact-prefix curated layers; real Alpine/Python runs; repeated converter digests match; derived-image whiteouts; private preformatted scratch and live/snapshot GC holds | Linux/KVM image and determinism lanes |
| P2 | Default/ci/risky profiles and TOML resolution; seccomp, masked paths, device filter, capabilities/NNP; fast refusal versus drop; fork, memory and managed/one-shot wall caps; all four showcase simulants with opposite controls | Other backend scenarios and matching-platform cold-start gates |
| P3 | Versioned host receipts, allowed/denied flow tuples, actual cgroup peaks, optional trusted-key signatures; all eight containment families and controls; V6 same-length RAM/state tamper rejection | Generated green matrix on KVM/MSHV/WHP |
| P4 | Workspace rw/ro and escape denial; successful-only bounded output publication; root exec/cp/logs/ps/stop; 1 MiB byte-exact copy; reproducible verified bundles; portable scoped TLS credential injection; guest/RAM/snapshot/evidence exclusion and leaking legacy-env control | Other backend workspace/proxy scenarios |
| P5 | Retained single-threaded Python barrier; 20 repaired clones and disabled controls; single-use hot pool with admission quotas; warm/cold benchmark and gated baseline; six portable MCP tools, cancellation and SDKs; architecture/OS-bound self-contained packaging and verified archive installation | Linux ARM build/run; Developer ID/notarization and Linux signing; published download smoke; integration built by another person |

## The ten changes

Audit date: **2026-10-03**. All ten changes have started; none is an untouched proposal.
The implementation is checked in together with its pinned OpenVMM submodule. Generated
runtime artifacts and live proof remain outside Git. The optional native app is maintained
in the separate [nvx-showcase repository](https://github.com/maceip/nvx-showcase).
Local completion and the roadmap's full release criteria are separate.

| # | Change | Source and local implementation | Remaining acceptance work |
| --- | --- | --- | --- |
| 1 | OCI image front door | `image.py`, `image_run.py`: pull/convert/cache/verify/GC; exact-prefix curated layers; whiteouts; private prepared scratch; real Alpine/Python runs and determinism evidence | Linux/KVM run and determinism results |
| 2 | Secure defaults | `policy.py`, guest seccomp/device policy and OpenVMM network rejection: profiles, resource caps, masked paths, fast refusal; protected probes and opposite controls | Other backend scenarios and matching-platform performance gates |
| 3 | Warm templates and pool | `warm.py`, `pool.py`: retained Python runtime, repaired clones, single-use leases, refill and benchmark; `pool start --image` and `run --pool` | Other backend repair/timing evidence; independent performance history; additional runtime shims need their own verification |
| 4 | Workspace and lifecycle verbs | `workspace.py`, `registry.py`: read-only default live export, explicit rw, clean-success-only `/out`, exec/cp/logs/ps/stop and bundles | Workspace/lifecycle results on KVM/MSHV/WHP |
| 5 | Host-held credentials | `secrets.py`, `proxy.py`, `proxy_service.py`: env/keychain lookup, exact destination scopes, header sanitization/injection, proxy logs and snapshot exclusion | Other backend credential/snapshot scenarios |
| 6 | Run receipt | `receipt.py`, `events.py`, OpenVMM flow tap: versioned host evidence, image/policy/version/resource data, flow tuples, trusted-key signing/verification | Matching backend receipt/flow evidence |
| 7 | macOS/ARM64 platform | `hvf.py`, `doctor.py`, `runtime_release.py`, ARM workflow: setup/signing checks, managed HVF, clock repair, exit, realpaths, packaging/install; versioned archive naming corrected in this audit | Signed/notarized publication, clean-host public download smoke, Linux ARM results, and the full requested release matrix; Intel macOS is currently unsupported |
| 8 | SDK/MCP/quotas | `mcp.py`, `quota.py`, `sdk/`: six tools, loopback HTTP/stdio, streamed output, cancellation, idempotent handles, CPU/memory/concurrency/wall/snapshot budgets; included in packages | An integration built and exercised by another person; published packages |
| 9 | Containment scorecard | `containment.py`, `containment-matrix.md`: eight deterministic families, controls must be UNCONTAINED, CI wiring and scoped generated HVF matrix | Generated passing matrix on KVM/MSHV/WHP and scheduled adversarial campaign evidence |
| 10 | Doctor/explain/events/cookbook | `doctor.py`, `explain.py`, `events.py`, `bundle.py`, `cookbook.md`: actionable checks, ordered events, reproducible verified evidence bundle and runnable recipes | Clean-machine first-use proof and the other backend results |

The [format comparison](image-formats.md) explains why OCI imports preserve the existing
EROFS/ext4 execution format, and how upstream NVX and Nanvix differ.

The ARM release workflow previously emitted unversioned archive names that the downloader
would never select. It now uses `nvx-VERSION-PLATFORM.tar.gz`, matching the existing release
discovery contract. The offline discovery regression checks both ARM platforms, rejects the
old names, and selects only the matching versioned platform asset. This fixes staging and
discovery compatibility; it does not supply publication or a clean-host download result.

The older x86 packaging path also shipped a launcher that could not locate its runtime:
the archive stores artifacts under `guest/` and `provenance/`, whereas the launcher used
checkout paths under `build/` and `openvmm/target/release/`. `common.py` now recognizes
both packaged layouts and uses the shipped `bin/openvmm` (or `openvmm.exe`). Checkout
build outputs retain precedence. A legacy-layout smoke using the same real ARM binaries
passed doctor and booted a guest; it establishes path resolution, not x86 platform proof.

### Fresh audit verification

- `build/ten-changes-offline.log`: **94 offline tests passed**, with ruff clean.
- `build/ten-changes-types{,-linux,-windows}.log`: strict pyright passed for macOS,
  Linux and Windows. Formatting and actionlint also passed.
- `build/ten-changes-hvf.log`: **all 18 HVF scenarios passed**, with complete evidence
  under `build/test-results/ten-changes-audit/`. This includes all eight containment
  families and controls, the four simulants, image determinism/GC, workspace/copy,
  credential/RAM/snapshot exclusion, 20 repaired clones plus two disabled controls,
  portable MCP cancellation/quotas, managed lifecycle and final teardown.
  Its generated matrix is byte-identical to `containment-matrix.md`.
- Rebuilding the initramfs after the host helper change produced byte-identical guest
  and package-manifest files; its refreshed provenance binds the current source.
- `build/nvx-0.1.0-darwin-arm64.tar.gz` installed at `build/ten-changes-installed/`.
  Doctor passed **11/11** and its shipped CLI printed `NVX-TEN-CHANGES-INSTALL-OK`
  from a real guest. The legacy-layout copy at `build/ten-changes-legacy-layout/`
  also passed **11/11** and printed `NVX-TEN-CHANGES-LEGACY-OK`.
  Logs are `build/ten-changes-{installed,legacy}-guest.log`. The archive remains
  explicitly a dirty-source development preview with ad-hoc signing and no notarization.
- The TypeScript SDK compiled and both SDK client tests passed before staging;
  their log is `build/ten-changes-typescript.log`.
- `build/ten-changes-benchmark.json`: 20 cold and 20 single-use pool requests measured
  **3.25 ms p50** to first warm workload stdout, **4.67 ms** to completion, and
  **1058.05 ms** for cold image first stdout. Pool preparation, admission hashing and
  refill are excluded. The **20% / 1 ms bootstrap guard passed** all three metrics;
  the ten-point trend gate still reports **Warmup**, with zero checked metrics.
  Gate logs are `build/ten-changes-{bootstrap,trend}-gate.log`.
- `build/ten-changes-hosts.json` freshly confirms `remote_enabled: false`; no external
  backend result is inferred. The two pre-existing VMM processes remained running
  after the scenario suite and benchmark; task-created VMs were torn down.

### Git cleanup verification (2026-10-03)

- `build/git-review-offline-final.log`: **94 host tests passed**; ruff and formatting
  passed. Strict pyright reported zero errors on macOS, Linux and Windows.
- `build/git-review-rust-{tests,integration}.log`: **439 distinct Rust unit tests
  passed** across ten affected crates, with one existing ignored test. The full Rust
  formatting check and the OpenVMM build passed.
- The review fixed macOS directory offsets across rewind and enforced `O_NOACCESS`
  on file reads, writes and directory reads. Existing shared filesystem tests and
  a new unsupported-operation/symlink-unlink regression passed. Snapshot example
  initialization and architecture/temporary-path assumptions in tests were corrected.
- The TypeScript SDK compiled and both client tests passed. Host configuration tests
  (four) and Specula tests (25) passed; changed workflows passed actionlint. Shellcheck
  and the CI-pinned shell formatter passed.
- All 30 initramfs provenance source digests match this checkout. Build products,
  credentials and local settings are excluded from the staged source changes.
- All **18 distinct HVF scenarios passed** across `build/git-review-hvf.log` and
  `build/git-review-hvf-remaining.log`. The aggregate command completed 14 scenarios
  before Docker ran out of internal storage during the final fixture build; it did
  not exit successfully. The remaining four scenarios then passed against the rebuilt
  VMM using the existing fixture, after verifying all 19 NVX guest helpers byte-for-byte
  against the current provenance-bound initramfs (`build/git-review-reused-fixture.json`).
  The generated containment matrix matches `containment-matrix.md`; doctor passed
  11/11 checks. Task-created VMs exited, while the two pre-existing VMMs stayed running.
- The leftover untracked `showcase/` tree was preserved at `../nvx-showcase`, connected
  to its own repository, and excluded from NVX. Its local changes remain separate.

## Local gates

- Before the review fixes, `./scripts/check.sh` passed 82 offline tests in 2.77 seconds.
  The updated stock-macOS-path gate and guest evidence are recorded below.
- Strict pyright: macOS, Linux and Windows, zero errors. Formatting and shell checks passed.
- Swift: 30 tests in five suites passed, including shared proxy/MCP contracts and the existing warm lifecycle.
- Rust: 88 snapshot, 51 virtio-fs, three macOS filesystem, and one fresh-generation/entropy/clock test passed. Network rejection tests also passed earlier in this task.
- The complete 18-scenario HVF run passed with exit zero:
  `build/p5-hvf-release-accepted.log`, with complete evidence and restorable warm
  templates in `build/test-results/p5-hvf-release-accepted/`. All eight containment
  families and all four showcase simulants passed with opposite negative controls.
- Long-path snapshot/control restore passed in `build/p5-long-control-accepted.log`.
- Root CLI pool stop waited for refill-worker and clone teardown:
  `build/p5-pool-stop-accepted.log` (`NVX-POOL-CLI-STOP-OK`).
- TypeScript SDK compilation and its two client tests passed; shellcheck, shfmt and
  Rust formatting passed. The Python SDK and shared MCP/proxy vectors passed above.
- The final development archive installed into a new directory, passed all 11 doctor
  checks, and pulled/converted Alpine into an empty NVX image cache before printing
  `NVX-GUEST-BOOT-OK` from a real guest. Evidence:
  `build/p5-release-final-install.log`, `build/p5-release-clean-doctor.json`, and
  `build/p5-release-clean-guest.log`. The archive is
  `build/p5-release-final-preview.tar.gz`; the installed launcher is
  `build/p5-release-clean-install/bin/nvx`. This exercises local archive installation,
  not a clean machine or a published `download` path.

The measured warm baseline has 20 cold and 20 pool requests, with distinct single-use clone
IDs. The pre-review build measured 3.69 ms p50 to first workload stdout, 5.82 ms to completion,
and 1181.90 ms for the cold image path (`build/p5-warm-accepted.json`). All three metrics
passed the existing gate (`build/p5-perf-accepted.log`) against
the first real local point (40% relative tolerance, 5 ms absolute tolerance, minimum
history explicitly one). Pool preparation, full-file admission hashes, and refill occur
before ready admission and are excluded from request timing. These timings are not the
older x86 bare-guest boot metric. The offline regression control triples latency and fails.

## Distribution and scope

Development previews are labelled `development: true`, with the actual dirty-source and
ad-hoc-signing state. They are not public signed/notarized releases. Strict macOS staging
requires a clean pinned core, Developer ID signature, hypervisor entitlement and successful
notarization assessment. The ARM release workflow signs the Linux inventory, installs the
whole archive, and runs doctor plus a real guest. It uploads artifacts for review rather
than claiming publication. Windows packages include a `.cmd` launcher; both SDKs and
compiled TypeScript ship with the CLI/MCP source.

Snapshots remain architecture/backend bound. A credential-bound warm snapshot excludes
host proxy state; cloning it requires a fresh binding and is currently refused. Ordinary
`--env` data intentionally enters guest RAM. The containment matrix covers the named probes,
not kernel CVEs, side channels or a hostile host administrator. macOS live exports pin their
root and map the admitted workload identity to the host export owner; they do not impersonate
Linux process credentials.

## Required external results

The configured-host resolver reports `remote_enabled: false`: no `.nvx-hosts.json` profiles
are available. This Mac has no Developer ID Application signing identity. The remaining
requirements are:

1. Actual KVM, MSHV and WHP correctness/containment/performance results, plus ARM Linux build/run.
2. Clean pinned release revisions, configured signing/notary identities, accepted notarization,
   published ARM assets, and the remote `download → doctor → run` smoke on clean hosts.
3. At least one SDK/MCP integration built and exercised by another person.

The scheduled adversarial campaign also requires its configured disposable target and
controller. No remote, signing, publication, or external-integration success is inferred
from the checked-in workflows.

## Review fixes and coverage boundaries

The 2026-10-02 review is retained verbatim in `implementation-review.md`.

- F1: the pre-change guest successfully opened `/dev/null` and `/dev/urandom`
  (`build/review-f1-before.log`). The converter's Linux UAPI defines character type
  as 2; a compile-time assertion now records that contract. The expanded live probe
  checks real null/zero/urandom/full I/O, a devpts slave and a controlling `/dev/tty`
  under default and ci profiles, and rejects mem, kmsg and block-node creation under
  ci with CAP_MKNOD while the risky control creates them. A deny-all filter now fails.
- F2: receipt signatures use pinned `cryptography` in process, without subprocesses
  or temporary signing material. The optional dependency manifest ships in packages
  and is included in development/CI requirements. Tests also reject a wrong trusted
  key, a modified signed body and a malformed signature. The signed guest-run receipt
  verified with a PATH selecting Apple's LibreSSL 3.3.6:
  `build/review-stock-signed-verified.log`.
- F3: the manual HVF workflow no longer depends on untracked Swift sources; offline
  dependencies and self-hosted actionlint labels are declared. `doc/ci.md` names the
  manual trigger, lack of automatic Mac PR coverage, release evidence requirements
  and uploaded benchmark/gate files. Swift results above belong to the prior local
  optional-app checkout and are not a core CI claim.
- F4: pool tests cover concurrent lease exhaustion, duplicate identities, double
  retirement, concurrent quota denial without budget leaks and invalid-size admission.
  Proxy tests cover redirect refusal, capability replay across instances, DNS rebinding
  and non-allowlisted forwarding. Warm tests reject bad manifests/inventories/symlinks
  before spawning and validate failure of stale repair, repeated entropy, restarted
  runtime and incomplete-control evidence. These oracle tests do not establish guest
  repair by themselves; that still requires the live clone scenario.
- F5: the existing single baseline point remains explicitly bootstrap evidence. The
  workflow now guards with 20% relative and 1 ms absolute tolerances, and separately
  requests ten independent points for the trend gate. Until then, **Warmup** is not a
  passing trend. Repeated measurements of this working tree do not manufacture history.
- F6: clone namespace flags and socket domains are filtered; clone3 returns ENOSYS for
  libc fallback. Fork/thread positive controls pass. Valid CLONE_NEWUSER is also denied
  by the workload's chroot in the risky control, so its non-vacuous seccomp check uses
  an invalid flag combination: filtered EPERM versus unconfined kernel EINVAL, with
  ordinary invalid clone also returning EINVAL. Actual mount/net/PID namespace control
  clones and clone3 succeed. IPv6 is permitted by the filter but unavailable in this
  kernel; both profiles report EAFNOSUPPORT. Evidence: `build/review-policy3.log` and
  `build/test-results/review-policy3/policy/`.
- F7: plan copies are consolidated under `doc/`, the original paths still work, and
  `.workbuddy-ai/` is ignored without deleting its contents. New canonical plans and
  status are registered with Git intent-to-add for review; all work remains uncommitted.

| Module | Offline evidence | Requires a live guest or release runner |
| --- | --- | --- |
| warm / pool | Hash/inventory/credential admission; concurrent accounting and quotas; repair-oracle mutant rejection | Actual clock/CRNG/generation repair, retained runtime, 20 clones, VM teardown and timing |
| proxy / secrets | Golden bytes, real local HTTP forwarding, redirects, capability scopes, DNS pinning, host config and redaction | Real guest TLS injection, absence of credentials from guest RAM and snapshot memory; leaking legacy-env control |
| release | Inventory, architecture/OS checks, tamper and overwrite refusal; shipped optional dependency | Actual installed guest; platform signing, notarization and public download |
| explain / lifecycle | Golden explanations and deletion/symlink boundaries | Real event causes, control protocol, guest stop and confirmed VMM teardown |

The offline test count is not a measure of security coverage. Scenario-only claims
above depend on the named runtime evidence and remain pending on the other backends.

### Accepted review verification

- Fresh Python 3.13 environment with PATH selecting `/usr/bin/openssl` (LibreSSL 3.3.6):
  **92 offline tests passed in 4.56 seconds**, with ruff clean and no signing skip:
  `build/review-stock-offline-accepted3.log`.
- Strict pyright for macOS, Linux and Windows: zero errors, in
  `build/review-types-accepted.log`, `build/review-linux-types-accepted.log` and
  `build/review-windows-types-accepted.log`. Python formatting, actionlint for the
  three touched workflows, composite YAML parsing, 25 Specula tests and four host
  resolver tests passed. No OpenVMM Rust source changed during these review fixes.
- All **18 HVF scenarios passed** after the guest rebuild:
  `build/review-hvf-complete.log`, complete evidence in
  `build/test-results/review-hvf-complete/`. This includes all eight containment
  families and four simulants with opposite controls, 20 repaired clones plus two
  disabled controls, scoped TLS credential/snapshot exclusion, MCP and final teardown.
- The updated 20-request pool benchmark measured **3.08 ms p50 to first workload stdout**
  and 4.73 ms to completion; the same workload's cold image path measured 1012.00 ms.
  These are requests against already-resumed ready clones, excluding pool preparation,
  admission hashing and refill, and are not the x86 bare-guest boot metric. Evidence:
  `build/review-warm.json`. The 20%/1 ms bootstrap guard passed all three metrics
  (`build/review-warm-bootstrap-gate.log`); the normal ten-point trend gate reports
  **Warmup and zero checked metrics** (`build/review-warm-trend-gate.log`). History is
  still one independent baseline point; this observation is not a claimed trend.
- The updated development archive is `build/review-release-preview.tar.gz`, installed
  at `build/review-release-installed/`. Its doctor passed all 11 checks and the optional
  pinned signing manifest is present. That installed CLI verified the real signed-run
  receipt with Apple's crypto-command PATH (`build/review-installed-signing.log`).
  With an empty NVX cache it also pulled/converted Alpine and booted a real guest that
  printed `NVX-UPDATED-INSTALL-OK` (`build/review-installed-guest.log`).
  Metadata still truthfully reports dirty-source/ad-hoc signing, no Developer ID and no
  notarization; external release gates remain required.

Generated proof lives under `build/`. Earlier failed/superseded snapshot RAM and scratch
copies were removed to recover disk space; their logs/JSON and an `OBSOLETE-ARTIFACTS.txt`
notice remain. The final accepted run retains its complete templates. No pre-existing VM,
container, volume, checkout, plan document, or user file was removed during that cleanup.
