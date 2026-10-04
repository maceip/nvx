# Implementation and acceptance evidence

NVX and its pinned OpenVMM core contain the implementations for the ten changes.
Full acceptance remains incomplete on the platforms and release gates listed below.
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
| 3 | Retained Python runtime, clock/CRNG/generation/net/block repair, 20 single-use clones, bounded pool admission/refill/teardown and cold/warm benchmark | HVF 20-clone repair plus disabled controls and 20 cold/warm requests pass. Apple Silicon/HVF passes a strict ten-commit trend gate against independently measured source-bound runs. Fourteen Mac, ten ARM and two native x86 Linux measurements are recorded with source, sample and log verification. The latest ten ARM points all have independent provenance; the older unverified legacy point falls outside that window. A fresh strict ARM repeat is required. Release acceptance rejects insufficient history; other platforms still need their ten measured points. Other runtime shims require separate proof. |
| 4 | Read-only/default or explicit rw workspace, escape denial, successful-only bounded output, exec/cp/logs/ps/stop and verified reproducible bundles | HVF and Linux ARM real workspace/lifecycle tests pass, including separate streams, status 37 and byte-exact 1 MiB copy. Other backend results remain required. |
| 5 | Host-held scoped TLS credentials, header sanitization/injection and proxy capability binding; no host secret in guest/RAM/snapshot/evidence | HVF and Linux ARM real credential isolation tests pass, including the leaking legacy-env control. Other backend results remain required. |
| 6 | Versioned host receipts, image/policy/versions, flow tuples and cgroup peaks; optional pinned-key signing/verification | HVF and Linux ARM receipt/flow scenarios pass. Host signature/tamper tests pass. Other backend results remain required. |
| 7 | Native ARM/x86 macOS HVF, native ARM/x86 Linux KVM state, WHP/MSHV snapshot state, six-platform packaging and trusted installation | Native HVF acceptance passes on Apple Silicon; Linux ARM builds/boots/restores. Native Windows and Intel hosted runs are being exercised. Developer ID/notarization, MSHV runtime proof and published clean-host installation remain prerequisites for the requested release matrix. |
| 8 | Six MCP tools, HTTP/stdio, Python/TypeScript SDKs, streaming/cancellation/timeouts, idempotent handles and aggregate quotas | Fresh installed Mac/HVF and ARM/KVM packages pass all six tools through the real TypeScript SDK, including streaming, copy, cancellation and quotas. The plan explicitly requires an integration built and exercised by another person; that result is still required. |
| 9 | Eight containment families with UNCONTAINED opposite controls, generated backend-bound matrix, four simulants and scheduled adversarial workflow | All eight families and simulants pass on HVF and Linux ARM. KVM x86, WHP, Intel HVF and MSHV full matrices and the configured scheduled adversarial campaign remain required. |
| 10 | Doctor, explainable failures, bounded ordered events, evidence bundles and runnable cookbook | HVF doctor passes 11/11 and the installed development CLI has booted a real guest with an empty image cache. A clean public download/doctor/run smoke remains part of release acceptance. |

[Image formats](image-formats.md) explains the OCI import boundary and the existing
EROFS/ext4 runtime formats, with the upstream NVX and Nanvix comparison.

## Current exercised evidence

The isolated clean completion checkout is `../nvx-completion`. The main checkout
contains the same committed implementation plus preserved user work on snapshot
inspection/disassembly; that work is not part of the completion commits.

- `build/completion-restore-copy-clock-macos-proof/NVX-ACCEPTANCE.json`
  binds the latest full accepted HVF repeat to NVX
  `4385058610c8138f336e919308c22b189307c769` and core
  `75b6560159c4ba903025ffd99dc3c95909002f49`: all 18 supported scenarios,
  eight containment families and opposite controls, four simulants, workspace/copy,
  credentials/RAM/snapshots, 20 repaired clones with disabled controls, MCP,
  determinism and teardown. All nine acceptance steps passed.
- That run measured 20 cold and 20 single-use pool requests: warm first stdout
  **2.83 ms p50**, completion **4.24 ms**, cold first stdout **1001.68 ms**.
  Pool preparation, admission hashes and refill are excluded from request latency.
  The full run enforced the strict ten-commit comparison, checked all three metrics
  and found zero regressions (`build/completion-restore-copy-clock-macos-proof/performance-gate.md`).
  All nine step-log hashes were independently verified. The main checkout at
  `75c513f` has the same tested core, passes 139 Python tests, lint and all 11
  doctor checks, and preserves all eleven pre-existing user edits. Its additional
  change fixes a test fixture's cleanup-publication ordering and retains Intel
  diagnostics; the full acceptance proof above remains bound to its exact source.
- `build/completion-intel-shared-memory-arm-evidence/NVX-ACCEPTANCE.json`
  records NVX `b7e3058` / core `94d8908b0` on the owned
  four-CPU, 6 GiB nested ARM/KVM host.
  All nine acceptance steps and all 18 supported ARM scenarios passed. Its
  20 cold and 20 warm samples measured **165.73 ms** first stdout,
  **210.10 ms** completion and **7462.49 ms** cold first stdout, with zero
  regressions against the earlier independently measured ARM bootstrap baseline.
  This is a bootstrap comparison; the ARM ten-commit gate remains outstanding.
  Metadata and all step logs were exported and SHA-256 verified. Full RAM and
  scratch remain inside the owned VM disk.
- The corrected core `464978e47` at NVX `3d88ba1` passes all twenty repaired ARM
  clones, both disabled controls and twenty cold/warm pool requests including refill.
  Warm first stdout is **182.56 ms p50**, completion **230.33 ms** and cold
  first stdout **7370.33 ms**. Its strict comparator reports six of ten required
  historical points and fails explicitly for insufficient history. Results and
  clean-source provenance are exported in
  `build/completion-arm-host/fresh-restore-host-arm-warm-evidence/`.
- Core `75b656015` at NVX `4385058` also passes twenty repaired ARM clones and
  both disabled controls. The source revisions, actual executable hash and result
  digest are recorded in
  `build/completion-arm-host/restore-copy-clock-arm-warm-evidence/REPEAT-RESULT.json`.
  Its repeated twenty-cold/twenty-warm pool measurement also passes refill and
  teardown: first stdout **126.90 ms p50**, completion **197.67 ms**, cold first
  stdout **8326.89 ms**. Source, executable and all four exported logs are
  hash-verified. The strict gate still fails for six of ten historical points;
  five imported points are independently source-bound and one legacy point has
  not been independently source-verified. This nested host does not demonstrate
  the plan's under-50-ms warm-start target.
- Fresh installed packages at NVX `3d88ba1` / core `464978e47` passed doctor, empty-cache
  first-use guest execution and all six real TypeScript SDK tools on both hosts.
  Results are `build/completion-fresh-restore-host-typescript-live/result.json`
  and `build/completion-fresh-restore-host-arm-typescript-live-result.json`.
  Those are our own integrations; the separate-person requirement remains open.
- `build/completion-guest-stream-offline.log`: **131 offline tests**, ruff clean.
  Strict macOS/Linux/Windows pyright passed. Real delayed-ready transport and pool
  teardown tests cover the startup/refill bugs found during this repeat.
- Core `5c1f378cb` fixes the x86 root-control command-line failure. The two affected
  crates passed check, all-target clippy, docs and **179 Rust tests**, including
  explicit root authorization and rejection controls; repository formatting passed.
- `build/completion-guest-stream-root-offline.log` in the main checkout: **139 tests**
  passed, including preserved snapshot/disassembly work. Its 11 doctor checks passed
  and its rebuilt guest printed `NVX-HOST-METADATA-ROOT-GUEST-OK`. The separately installed
  current development archive also passed all 11 doctor checks and a real first-use
  OCI guest with an empty image cache.
- `build/completion-guest-stream-typescript-live/result.json` records all six tools through
  the installed TypeScript SDK against a real HVF guest with an empty image cache: both output streams,
  status 37, idempotence, byte-exact 1 MiB copy, snapshot operations, quota denial,
  cancellation and an exec workload's forged instance marker. This is our own
  integration test; the separate-person acceptance requirement remains open.
- Native Windows now passes its corrected named-pipe listener regression at
  NVX `3cf89c4` / core `beacdd8c6`; its full WHP repeat then passed containment, workspace and credentials but
  failed on the first warm restore with a closed control endpoint. The exact
  executable reproduced that failure in native diagnostic run `37178705609`.
  A fresh named-pipe connection arrived during RAM copying and was discarded
  during restore. Core `464978e47` preserves that unprocessed connection, still
  authenticates with fresh credentials and rejects the captured capability. The
  regression failed before the fix; check, clippy, docs, all 82 console tests
  and final formatting pass. Native run `37179030537` now passes both broker
  regressions and executes the first restored guest workload successfully.
  Its repair assertion then fails. Diagnostic run `37180394450` preserves the
  exact result: identity, hostname, UID, private-channel denial, output and egress
  pass; the guest clock is 4.11 seconds stale. RAM materialization occurred after
  the admission-time downtime calculation. Core `75b656015` refreshes that value
  after the private copy and generation checks; its regression fails before the
  fix and passes afterward, and check, clippy, docs, 168 entry tests and final
  formatting pass. Native Windows run `37180767156` at NVX `0244ae5` / core
  `75b656015` now succeeds: all twenty repaired clones, both disabled controls,
  both broker regressions and the slow-copy clock regression pass. The downloaded
  per-clone results and batch invariants were independently revalidated. Its
  receipt is `build/completion-restore-copy-clock-whp-result.json`; the full
  WHP run `37180859116` subsequently passes containment, simulants, receipts,
  image execution, workspace, credentials and twenty-clone repair, then fails
  MCP snapshot removal on Windows' readonly scratch attribute. The fix removes
  only validated, owned readonly payloads and retains the image pin and quota
  until deletion succeeds. Both regression tests fail before the fix; all 133
  offline tests, lint and strict Darwin/Linux/Windows typing pass afterward.
  Native run `37182933521` at NVX `b495316` / the exact same core `75b656015`
  now passes both regressions and all six real Python SDK MCP tools, including
  snapshot deletion, separate streams, status 37, byte-exact 1 MiB transfer,
  idempotence, quota denial and cancellation. Its result is
  `build/completion-whp-snapshot-remove-native-result.json` in the diagnostic
  checkout. The complete cached-core WHP repeat is tracked separately.
- Intel's native probe rejects POSIX shared-memory RAM and admits regular-file
  RAM. At NVX `b7e3058` / core `94d8908b0`, native build, clippy, docs and memory
  tests pass, but run `37176187450` fails the first seccomp policy guest with a
  closed control endpoint. Its artifact omitted the causal VMM log. Diagnostic
  run `37181265965` therefore reuses that exact source and native executable,
  enables debug logging and retains the actual instance cache. The newer full
  lane at NVX `4385058` stopped earlier on a test fixture's publication-order
  race, corrected in `75c513f`; it supplies no new Intel runtime acceptance.
  VM admission alone is not full guest acceptance.
  The first exact-build diagnostic timed out during OCI preparation before any
  VM instance was created. Run `37182061676` separates image preparation from
  the unchanged 180-second policy guest probe, matching the full release runner.
- ARM history collection at the already existing revisions `75c513f` and
  `598ca08` completed twenty cold and twenty warm requests per revision on the
  same owned host. Both runs preserve clean source/executable provenance and
  verified logs and samples. The first attempt at `75c513f` failed refill
  readiness with two connection resets and contributes no measurement. Its
  original template and failure logs remain retained. Paired startup/teardown
  trials and two twenty-request pool diagnostics then passed, including a run
  without debug profiling, and the unmodified canonical measurement completed
  after task-owned storage reclamation. The reset's cause is undetermined;
  neither the passing diagnostics nor the history import establish a code fix.
- Native x86 KVM at NVX `b7e3058` / core `94d8908b0` passed the portable controls,
  credentials, 20 repaired clones, MCP, scratch and SMP restore. Its next failure
  was a missing real VP-binding profile record, corrected in core `219537904`.
- A ten-repeat x86 scratch probe at core `94d8908b0` reproduced RAM digest
  corruption on trial four before any restore. Core `42edd2f8c` then verified
  each capture while the source worker was alive: trial five passed that check
  but its RAM digest changed after source teardown. Both full failing artifacts
  are retained. Guest-requested capture now publishes an independent RAM copy;
  the focused source-overwrite regression, scoped core checks, 258 Rust tests
  and final repository formatting pass. Native run `37178498614` now passes
  all ten scratch captures and all processor-restore profile checks at NVX
  `81233b9` / core `e1cd86c18`. Capture checksum checks and corrupt/missing
  scratch controls remain enforced (`build/completion-independent-capture-kvm-result.json`).
- Full x86 KVM run `37178540996` at the same source passes all eight runtime
  acceptance steps, including all supported scenarios, containment, determinism,
  twenty-clone repair, MCP and twenty cold/warm benchmark requests. Its ninth
  gate fails explicitly because the required ten-commit `linux-kvm` history is
  absent. No complete release-acceptance proof is published for that run.
- The newest full x86 KVM run `37180859127` at NVX `4385058` / core `75b656015`
  also passes all eight runtime steps and all twenty cold/warm pool requests.
  First stdout is **15.76 ms p50**, completion **19.41 ms**, cold first stdout
  **2139.42 ms**. All nine step-log hashes and the actual native executables
  were independently verified. Its final gate fails solely for absent history;
  those two real x86 runs are now retained as the first source-bound trend points.
  The result is `build/completion-restore-copy-clock-full-kvm-result.json` in the
  diagnostic checkout, and a complete release-acceptance proof remains unpublished.
- Hosted ARM runners expose no usable `/dev/kvm`; their runtime lane fails
  explicitly. Actual ARM runtime evidence comes from the owned nested host above.

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
- The x86 KVM repeat exposed the console/snapshot namespace constraint. Capture
  and restore sockets now share the snapshot's immediate parent. Long paths capture
  in a short private directory and publish after the source VM exits; both path
  lengths and temporary namespace cleanup have a persisted-configuration regression.
- The next KVM repeat reached the actual PMIO boundary and found the core's snapshot
  validator rejecting the supported stderr boot console. The corrected inherited
  provider allowlist accepts only console and stderr. Check, all-target clippy,
  docs, **89 helper tests**, including valid/invalid provider controls, and final
  repository formatting pass. The native KVM repeat now captures and publishes
  the protected and legacy credential snapshots and passes credential isolation.
  Its first clone identified the separate restore-time portable-adapter admission.
- Networked x86 restore now supplies the explicit portable network profile required
  by the core while taking the saved address and identity from the snapshot. The
  persisted-configuration regression covers all four x86 backends; it continues to
  reject fresh address, workload identity and memory overrides during restore.
- KVM and WHP then reached the first clone and found the core requiring the captured
  control-listener pathname. Explicit same-kind listener replacements now preserve
  every attachment field except the endpoint. Namespace validation still applies,
  broker listeners still require caller approval and a fresh capability, and clients,
  inherited providers and disconnected attachments keep exact matching. Tests cover
  removed source directories, fresh boot/control endpoints, namespace escapes and
  ten mutated attachment fields. Check, clippy, docs, **257 Rust tests** and final
  repository formatting pass. The native Windows lane also executes its named-pipe
  listener regression before saving a newly built core.
- Intel's native memory probe independently admits anonymous and regular file
  mappings but reproduces `HV_ERROR` for POSIX shared memory. Intel guest RAM now
  uses an unlinked private regular file while retaining descriptor sharing and
  snapshot transfer. The existing workspace tempfile crate supplies the secure
  file; no new package or lockfile dependency was introduced. Scoped check,
  native and Intel cross-compilation, clippy, docs, all **10 memory tests** and
  final repository formatting pass. The expanded tests also found Darwin's
  advisory decommit preserving old bytes; replacing the anonymous range now
  passes both existing zero-page tests. Native Intel memory tests run in CI.
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
- The corrected Windows image probe passed in the next real WHP repeat. That
  repeat found bundle staging cleanup attempted while its file was still open.
  Bundles now close and fsync staging before atomic no-overwrite publication and
  cleanup. The Windows lane runs offline host tests and strict types before its
  native build, so this platform's filesystem behavior is checked directly.
- Native Windows offline checks found mandatory-lock reads before lock acquisition,
  read-only cache GC, Unix path assumptions and open-file unlink incompatibility.
  Windows now acquires the lock before any protected-byte access, makes only
  unreferenced cache blobs writable for deletion, validates archive paths in guest
  POSIX coordinates and opens shared files with delete sharing. A guest can unlink
  an open file, read the retained handle and then close it. The native repeat passes
  these functional regressions; its remaining Unix-only mode assertion is corrected
  to check the permission bits actually reported by each platform.
- MCP obtains each new instance ID from bounded, atomically published private
  host metadata. Workload stderr and image-conversion diagnostics cannot supply
  or replace an instance ID. Exec retains its known owned ID. A live TypeScript
  check found the leaked stderr marker; stream, forged-marker and private-file
  regressions cover the correction.
- A fresh installed SDK run found OCI preparation diagnostics entering guest
  stderr. Private-metadata MCP launches now keep successful host preparation out
  of both guest streams; failures retain a bounded actionable diagnostic. The
  installed TypeScript SDK's empty-cache six-tool repeat passes, including
  separate streams, status 37, idempotence, file copy, quotas and cancellation.
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
