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
| 2 | Default/ci/risky profiles, UID/capabilities/NNP, seccomp namespace and socket rules, devices, masked paths, PID/memory/wall caps and fast egress refusal | HVF protected probes and opposite controls pass. Linux ARM also passed these controls before its interrupted warm-clone run. All backend controls must pass. |
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

- `build/completion-pool-cleanup-macos-proof/NVX-ACCEPTANCE.json` binds a full
  accepted HVF run to NVX `9f7b241a11414d0c712c8679886b087f56f49896` and core
  `5048794dc542ad7deee1b3ac9e0093f893aeefe3`: all 18 scenarios, eight containment
  families and opposite controls, four simulants, workspace/copy, credentials/RAM/
  snapshots, 20 repaired clones with disabled controls, MCP, determinism and teardown.
- That run measured 20 cold and 20 single-use pool requests: warm first stdout
  **3.31 ms p50**, completion **4.92 ms**, cold first stdout **1005.74 ms**.
  Pool preparation, admission hashes and refill are excluded from request latency.
  The 20%/1 ms bootstrap gate checked three metrics and found zero regressions.
- `build/completion-pool-cleanup-offline.log`: **116 offline tests**, ruff clean.
  Strict macOS/Linux/Windows pyright passed. Real delayed-ready transport and pool
  teardown tests cover the startup/refill bugs found during this repeat.
- Core `5c1f378cb` fixes the x86 root-control command-line failure. The two affected
  crates passed check, all-target clippy, docs and **179 Rust tests**, including
  explicit root authorization and rejection controls; repository formatting passed.
- `build/completion-root-current-offline.log` in the main checkout: **122 tests**
  passed, including preserved snapshot/disassembly work. Its 11 doctor checks passed
  and its actual guest printed `NVX-INTEGRATED-TEN-ITEMS-OK`.
- The owned nested Ubuntu ARM host exposed `/dev/kvm` API 12 and admitted a real VM.
  Native VP, GIC and register state compiled and real clock/TLS restore passed.
  Its full battery passed identity, seccomp, resources, devices, containment,
  simulants, receipts, images, workspace and credentials before a warm-clone failure
  during host disk exhaustion. A fresh clean-source full run is in progress.
- Hosted Intel HVF and Windows WHP admission succeeded. These are admission results,
  not full guest acceptance. Hosted Ubuntu x86 KVM admission succeeded; the hosted
  ARM runners expose no usable `/dev/kvm`, so their runtime lane fails explicitly.

## Fixes found by the repeated platform tests

- Windows Docker launches use the resolved executable/wrapper path.
- First-use image conversion keeps workload stdout free of a trailing image digest.
- Intel macOS's owned OCI converter installs its QEMU dependency explicitly.
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
