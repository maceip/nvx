# Implementation and acceptance evidence

The ten requested mechanisms are implemented in NVX and its pinned OpenVMM core.
The original acceptance requirements in [the roadmap](roadmap-best-in-class-sandbox.md)
and [implementation plan](implementation-plan.md) remain the completion contract.
A platform is accepted only after its real runtime scenarios, negative controls,
image determinism and measured performance gate pass. Compilation and VM admission
are recorded separately from that result.

## The ten changes

| # | Implemented behavior | Acceptance evidence and remaining gates |
| --- | --- | --- |
| 1 | OCI pull/convert/cache/verify/GC, exact-prefix curated EROFS layers, OCI whiteouts and private prepared ext4 scratch | Real Alpine/Python execution and repeated image conversion pass on Apple Silicon/HVF. KVM, Intel HVF, WHP and MSHV full runs are tracked below. |
| 2 | Default/ci/risky profiles, UID/capabilities/NNP, seccomp namespace and socket rules, devices, masked paths, PID/memory/wall caps and fast egress refusal | HVF and Linux ARM full acceptance passes. Native x86 KVM/WHP policy probes also pass; their full batteries remain under test. All backend controls must pass. |
| 3 | Retained Python runtime, clock/CRNG/generation/net/block repair, 20 single-use clones, bounded pool admission/refill/teardown and cold/warm benchmark | HVF 20-clone repair plus disabled controls and 20 cold/warm requests pass. Three matching-platform bootstrap performance metrics are checked. Ten independent historical points have not accumulated; the trend gate remains Warmup. Other runtime shims require separate proof. |
| 4 | Read-only/default or explicit rw workspace, escape denial, successful-only bounded output, exec/cp/logs/ps/stop and verified reproducible bundles | HVF and Linux ARM real workspace/lifecycle tests pass, including separate streams, status 37 and byte-exact 1 MiB copy. Other backend results remain required. |
| 5 | Host-held scoped TLS credentials, header sanitization/injection and proxy capability binding; no host secret in guest/RAM/snapshot/evidence | HVF and Linux ARM real credential isolation tests pass, including the leaking legacy-env control. Other backend results remain required. |
| 6 | Versioned host receipts, image/policy/versions, flow tuples and cgroup peaks; optional pinned-key signing/verification | HVF and Linux ARM receipt/flow scenarios pass. Host signature/tamper tests pass. Other backend results remain required. |
| 7 | Native ARM/x86 macOS HVF, native ARM/x86 Linux KVM state, WHP/MSHV snapshot state, six-platform packaging and trusted installation | Native HVF acceptance passes on Apple Silicon; Linux ARM builds/boots/restores. Native Windows and Intel hosted runs are being exercised. Developer ID/notarization, MSHV runtime proof and published clean-host installation remain prerequisites for the requested release matrix. |
| 8 | Six MCP tools, HTTP/stdio, Python/TypeScript SDKs, streaming/cancellation/timeouts, idempotent handles and aggregate quotas | Offline SDK checks and the real HVF portable MCP scenario pass. The plan explicitly requires an integration built and exercised by another person; that result is still required. |
| 9 | Eight containment families with UNCONTAINED opposite controls, generated backend-bound matrix, four simulants and scheduled adversarial workflow | All eight families and simulants pass on HVF and Linux ARM. KVM x86, WHP, Intel HVF and MSHV full matrices and the configured scheduled adversarial campaign remain required. |
| 10 | Doctor, explainable failures, bounded ordered events, evidence bundles and runnable cookbook | HVF doctor passes 11/11 and the installed development CLI has booted a real guest with an empty image cache. A clean public download/doctor/run smoke remains part of release acceptance. |

[Image formats](image-formats.md) explains the OCI import boundary and the existing
EROFS/ext4 runtime formats, with the upstream NVX and Nanvix comparison.

## Current exercised evidence

The isolated clean completion checkout is `../nvx-completion`. The main checkout
contains the same committed implementation plus preserved user work on snapshot
inspection/disassembly; that work is not part of the completion commits.

- `build/completion-mcp-identity-macos-proof/NVX-ACCEPTANCE.json` binds a full
  accepted HVF repeat to NVX `66498ba7348f69649a29dbe5a024d664be9ea9e3` and core
  `5cb6890a39744321baa57d788f7f791dfad81c3d`: all 18 scenarios, eight containment
  families and opposite controls, four simulants, workspace/copy, credentials/RAM/
  snapshots, 20 repaired clones with disabled controls, MCP, determinism and teardown.
- That run measured 20 cold and 20 single-use pool requests: warm first stdout
  **2.77 ms p50**, completion **4.16 ms**, cold first stdout **1022.52 ms**.
  Pool preparation, admission hashes and refill are excluded from request latency.
  The 20%/1 ms bootstrap gate checked three metrics and found zero regressions.
- `build/completion-mcp-host-metadata-offline.log`: **129 offline tests**, ruff clean.
  Strict macOS/Linux/Windows pyright passed. Real delayed-ready transport and pool
  teardown tests cover the startup/refill bugs found during this repeat.
- Core `5c1f378cb` fixes the x86 root-control command-line failure. The two affected
  crates passed check, all-target clippy, docs and **179 Rust tests**, including
  explicit root authorization and rejection controls; repository formatting passed.
- `build/completion-root-mcp-identity-offline.log` in the completion checkout: **136 tests**
  passed, including preserved snapshot/disassembly work. Its 11 doctor checks passed
  and its rebuilt guest printed `NVX-CURRENT-ROOT-GUEST-OK`. The separately installed
  current development archive also passed all 11 doctor checks and a real first-use
  OCI guest with an empty image cache.
- `build/completion-mcp-typescript-live/result.json` records all six tools through
  the installed TypeScript SDK against a real HVF guest: both output streams,
  status 37, idempotence, byte-exact 1 MiB copy, snapshot operations, quota denial,
  cancellation and an exec workload's forged instance marker. This is our own
  integration test; the separate-person acceptance requirement remains open.
- The owned nested Ubuntu ARM host exposed `/dev/kvm` API 12 and admitted a real VM.
  Native VP, GIC and register state compiled and real clock/TLS restore passed.
  Its fresh clean-source battery passed all 18 scenarios, including 20 repaired
  clones, disabled controls, MCP and teardown; image determinism also passed.
  `build/completion-warm-boundary-arm-evidence/NVX-ACCEPTANCE.json` records an independent
  full acceptance run at NVX `1f8b9be157d5adb25dda12c1c7fb788ad8ba2876` and
  core revision `5cb6890a39744321baa57d788f7f791dfad81c3d`. Its
  20-request cold/warm benchmark measured first stdout **212.01 ms p50**,
  completion **275.63 ms** and cold first stdout **8745.39 ms** on the nested
  four-CPU, 6 GiB host. All three matching-platform comparisons passed with zero
  regressions against the earlier separately measured ARM baseline.
  An earlier failed run encountered host disk exhaustion.
- Hosted Intel's native build, clippy, docs and core tests passed; its first timed
  guest probe timed out without guest logs. Windows native builds and doctor passed,
  but scenario acceptance failed and the earlier cross-drive upload lost the logs.
  Corrected repeats are being followed. Hosted Ubuntu x86 KVM admission succeeded; the hosted
  ARM runners expose no usable `/dev/kvm`, so their runtime lane fails explicitly.

## Fixes found by the repeated platform tests

- Windows Docker launches use the resolved executable/wrapper path.
- The x86 guest enables user namespaces and packet sockets, matching ARM. Build
  validation requires them so unconfined seccomp controls can exercise the blocked
  operations instead of failing because the kernel lacks the feature.
- Raw x86 one-shot and managed sandboxes receive the controlled portable adapter
  by default, so their network-denial flags always have a matching device. Explicit
  adapter/profile pairs retain their selected configuration.
- Direct lifecycle provisioning applies that adapter when an x86 network policy
  is supplied, including the tenant-isolation probes. An actual persisted-config
  regression covers all four x86 backends. The protected network probe and its
  opposite control now pass on native KVM and WHP; their latest full repeats reached
  tenant isolation and identified this separate provisioning entry point.
- Receipt and warm-clone collector fixtures use the x86 portable gateway mapping
  with their egress policy, while ARM retains its explicit host-loopback adapter.
  The native x86 KVM repeat passed tenant isolation and reached the receipt probe;
  its old fixture requested the unsupported generic loopback option.
- Native x86 warm capture uses the guest PMIO snapshot boundary with a paired
  workload-start snapshot. Restore takes memory, workload identity and network
  addressing from that snapshot, verifies its paired scratch image, and completes
  the guest repair acknowledgement before thawing disk I/O. ARM retains its disk
  snapshot path. Wire compatibility and restore-argument regressions pass; both
  guest helpers compile for ARM and x86 with warnings treated as errors.
- Intel HVF is admitted by the x86 microVM frontend, fixed network IRQ and snapshot
  contract. The three affected core crates pass check, all-target clippy, docs and
  **267 Rust tests**, plus Intel cross-compilation and final repository formatting.
  Their native runtime acceptance is being repeated.
- Expanded native Linux core checks found a stale preflight test rejecting the
  supported stderr boot console. The corrected test positively checks console,
  stderr and none, and identifies any unexpectedly admitted rejection case.
  Scoped entry checks, clippy, docs, 166 local tests and repository formatting pass;
  native x86 verification is being repeated.
- Windows receipt and image probes accept the CLI's CRLF instance-ID line. The
  latest native WHP repeat passed all eight containment families, simulants and
  receipts before identifying the separate image probe's old LF-only parser.
- MCP obtains each new instance ID from bounded, atomically published private
  host metadata. Workload stderr and image-conversion diagnostics cannot supply
  or replace an instance ID. Exec retains its known owned ID. A live TypeScript
  check found the leaked stderr marker; stream, forged-marker and private-file
  regressions cover the correction.
- Windows proof logs and the image cache share the checkout drive so failed gates
  can upload their evidence. Gate errors include bounded, credential-redacted
  diagnostics. A source-keyed cache retains the verified native core binary after
  a scenario failure; acceptance still checks its source revision and digest.
- Image download/conversion precede timed guest probes as separately recorded
  gates. Host probe timeouts preserve partial stdout/stderr and print a bounded,
  redacted cause instead of losing the evidence in a command traceback.
- First-use image conversion keeps workload stdout free of a trailing image digest.
- Intel macOS's owned OCI converter installs its QEMU dependency explicitly.
  The package CLI also admits Intel macOS alongside the other five release targets;
  the all-platform CLI regression and 118 offline tests pass, with strict types on
  macOS, Linux and Windows.
- Portable fixtures extract the provenance-verified current initramfs instead of
  rebuilding a full compiler container for each scenario.
- Control capabilities are complete and EOF-sealed before OpenVMM starts.
- Startup keeps one authenticated attachment through delayed guest readiness.
- Pool stop waits for handlers, refill threads and VM teardown before a temporary
  template is removed. Release benchmarking allows the full clone readiness budget.
- Acceptance verifies the actual core binary revision and digest before and after
  testing, so an older or replaced binary cannot be labelled as the current source.
  Its stale/dirty/tampered regression checks pass with all 117 offline tests and
  strict macOS/Linux/Windows types.
- WHP/MSHV save and restore pending userspace ExtINT state; Windows-only constructors
  and OpenHCL activity conversions are checked on their actual target bodies.
- The x86 command-line builder carries the explicit `unsafe-root` authorization
  through to the guest so risky-profile negative controls can execute. Numeric
  `0:0`, `0:GID` and `UID:0` CLI identities remain rejected.

## Remaining prerequisites

1. A usable MSHV host and disposable adversarial target/controller configuration.
2. Developer ID Application signing identity and a notary profile, followed by the
   signed/notarized six-platform release and clean-host public download smoke.
3. An SDK/MCP integration built and exercised by another person.

Native Windows, Intel HVF and KVM runs are still being followed and repaired.
No missing runtime result, notarization, public publication or external integration
is inferred from checked-in source or CI wiring. Snapshots remain architecture and
backend bound. Credential snapshots exclude host proxy state and require a fresh
binding for reuse. Ordinary `--env` values intentionally enter guest memory.

Generated evidence lives under `build/`. Superseded failed-run RAM/scratch copies
were removed to recover host space; their logs, JSON and artifact hashes are retained
with an `OBSOLETE-ARTIFACTS.txt` notice. Current accepted proof artifacts are retained.
User files, Docker volumes/images and unrelated processes were preserved.
