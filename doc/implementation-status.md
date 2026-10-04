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
| 1 | OCI pull/convert/cache/verify/GC, exact-prefix curated EROFS layers, OCI whiteouts and private prepared ext4 scratch | Real Alpine/Python execution and repeated image conversion pass on Apple Silicon/HVF, ARM/KVM, native x86 KVM and WHP at the exact revisions below. Intel HVF and MSHV full runs remain required. |
| 2 | Default/ci/risky profiles, UID/capabilities/NNP, seccomp namespace and socket rules, devices, masked paths, PID/memory/wall caps and fast egress refusal | HVF, Linux ARM, native x86 KVM and native WHP full acceptance passes at the exact revisions below. Current-core ARM and Mac protected/opposite controls also pass. Intel HVF and MSHV controls remain required. |
| 3 | Retained Python runtime, clock/CRNG/generation/net/block repair, 20 single-use clones, bounded pool admission/refill/teardown and cold/warm benchmark | HVF, ARM/KVM, native x86 KVM and WHP pass 20-clone repair with disabled controls, 20 cold/warm requests and strict ten-point trend gates at the revisions below. Fifteen Mac, eleven ARM, twelve native x86 Linux and eleven Windows points are independently source/sample/log verified. Diagnostic-branch measurements retain their identities and are excluded. The nested ARM host exceeds the under-50-ms target. Intel HVF and MSHV still need runtime/history proof; other runtime shims require separate proof. |
| 4 | Read-only/default or explicit rw workspace, escape denial, successful-only bounded output, exec/cp/logs/ps/stop and verified reproducible bundles | HVF, ARM/KVM, native x86 KVM and WHP real workspace/lifecycle tests pass, including separate streams, status 37 and byte-exact 1 MiB copy. Intel HVF and MSHV results remain required. |
| 5 | Host-held scoped TLS credentials, header sanitization/injection and proxy capability binding; no host secret in guest/RAM/snapshot/evidence | HVF, ARM/KVM, native x86 KVM and WHP real credential isolation tests pass, including the leaking legacy-env control. Intel HVF and MSHV results remain required. |
| 6 | Versioned host receipts, image/policy/versions, flow tuples and cgroup peaks; optional pinned-key signing/verification | HVF, ARM/KVM, native x86 KVM and WHP receipt/flow scenarios pass. Host signature/tamper tests pass. Intel HVF and MSHV results remain required. |
| 7 | Native ARM/x86 macOS HVF, native ARM/x86 Linux KVM state, WHP/MSHV snapshot state, six-platform packaging and trusted installation | Full acceptance passes on Apple Silicon/HVF, Linux ARM/KVM, native x86 KVM and native WHP at the source-bound revisions below. The fixed Windows reader passes the unchanged performance gate; earlier failed measurements remain retained. The corrected Intel build passes native checks, but its first protected guest times out before readiness. Exact-build debug and trace replays reproduce that timeout; trace proves Linux kernel execution and ends at a virtio-fs configuration read. A management-inspection replay follows to distinguish the cause. Intel full acceptance remains absent. Developer ID/notarization, MSHV runtime proof and published clean-host installation remain prerequisites for the requested release matrix. |
| 8 | Six MCP tools, HTTP/stdio, Python/TypeScript SDKs, streaming/cancellation/timeouts, idempotent handles and aggregate quotas | Fresh installed Mac/HVF and ARM/KVM packages pass all six tools through the real TypeScript SDK, including streaming, copy, cancellation and quotas. The plan explicitly requires an integration built and exercised by another person; that result is still required. |
| 9 | Eight containment families with UNCONTAINED opposite controls, generated backend-bound matrix, four simulants and scheduled adversarial workflow | All eight families and simulants pass on HVF, Linux ARM, native x86 KVM and native WHP at the source-bound revisions below. Intel HVF, MSHV and the configured scheduled adversarial campaign remain required. |
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
  `8c7963a` now pins the separately tested default-HVF discovery fix in core
  `4b20f45f1`, passes 143 Python tests and lint, and preserves all eleven
  pre-existing user edits byte for byte. Its actual clean-built Mac executable
  passes all 11 doctor checks and a real protected/opposite-control seccomp
  retest. That focused result is not a new full acceptance run. Later changes
  fix fixture ordering, owned Windows snapshot
  deletion and shared-port allocation, and retain native diagnostics; the full
  acceptance proof above remains bound to its exact source.
- The core-4b20 full Mac repeat at NVX `423b838` / core `4b20f45f1`
  passes all nine acceptance gates, including the complete scenario battery,
  opposite controls, MCP, determinism, twenty repaired clones and twenty
  cold/warm requests. Its strict ten-point comparison checks all three metrics
  with zero regressions. First stdout is **2.59 ms p50**, completion **3.87 ms**,
  cold first stdout **926.07 ms**. Source, executable, every gate log, containment
  digest and raw sample medians are independently verified in
  `build/completion-core4b20-full-macos-proof/NVX-ACCEPTANCE.json` in the
  diagnostic checkout.
- The fixed-source Mac repeat at NVX `7c7f723` / core `f1f6019b7`
  passes all nine acceptance gates, including all supported scenarios, opposite
  controls, MCP, image determinism, twenty repaired clones and twenty cold/warm
  requests. Its strict ten-point comparison checks all three metrics with zero
  regressions. First stdout is **2.78 ms p50**, completion **4.23 ms**, cold first
  stdout **975.45 ms**. Source and executable remain unchanged throughout; all
  gate-log hashes, the containment digest and raw sample medians are independently
  verified in
  `../nvx-mac-proof-repeat/build/completion-immutable-full-macos-proof/NVX-ACCEPTANCE.json`.
  The preceding diagnostic-checkout run passed its gates but correctly withheld
  a proof after a workflow edit changed HEAD; its rejection and logs are retained.
- The control-register correction repeat at NVX `65329b2` / core `d591aaee1`
  passes all nine Mac acceptance gates. The complete scenario battery, all eight
  containment families and opposite controls, four simulants, MCP, twenty
  repaired clones with disabled controls, image determinism and twenty cold/warm
  requests pass. First output is **2.76 ms p50**, completion **4.15 ms**, cold
  first output **1045.67 ms**. The unchanged strict ten-point comparison checks
  all three metrics with zero regressions. Actual executable bytes, unchanged
  clean source, every gate log, containment schema/render and sample medians
  are independently verified in
  `build/completion-intel-control-state-full-macos-proof/NVX-ACCEPTANCE.json`
  in the diagnostic checkout. The user checkout independently passes 143 of
  146 host tests with three Windows-only skips, lint and all eleven doctor
  checks, and preserves all eleven original user-edited files byte for byte.
- `build/completion-intel-shared-memory-arm-evidence/NVX-ACCEPTANCE.json`
  records NVX `b7e3058` / core `94d8908b0` on the owned
  four-CPU, 6 GiB nested ARM/KVM host.
  All nine acceptance steps and all 18 supported ARM scenarios passed. Its
  20 cold and 20 warm samples measured **165.73 ms** first stdout,
  **210.10 ms** completion and **7462.49 ms** cold first stdout, with zero
  regressions against the earlier independently measured ARM bootstrap baseline.
  That original run used a bootstrap comparison; the later strict ARM run below passes.
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
  checkout. The complete cached-core repeat `37183449202` then passes warm repair
  and MCP but stops in the host-loopback fixture after sixteen failed shared
  TCP/UDP port allocations. The corrected fixture alternates the protocol that
  selects the candidate, closes every failed pair and retains the last bind cause.
  Its regression fails before the fix; all 135 offline tests, lint and strict
  Darwin/Linux/Windows types pass afterward. Native repeat `37184233688` at
  `2131a1e` passes both shared-port regressions, both snapshot deletion regressions,
  all six MCP tools and all seven real host-loopback policy cases. Full WHP
  acceptance and strict history are still tracked separately.
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
  the unchanged 180-second policy guest probe, but the preparation step times out
  after fifteen minutes; the guest probe is skipped. Full native run `37181535232`
  at `75c513f` / core `75b656015` subsequently completes native checks and both
  image preparations, then reproduces the closed endpoint in its first protected
  guest. Its retained VMM log records memory finalization without explaining the
  exit. Diagnostic `37183911824` therefore reuses that exact newer executable and
  source, captures converter progress, enables debug/profile logging and retains
  the actual VM cache. It reproduces the failure before guest control readiness:
  `microVM virtio-net requires an explicit KVM, MSHV, WHP, or HVF hypervisor`.
  The default macOS network IRQ branch used `guest_arch`, which this generic
  definitions crate never sets. Core `4b20f45f1` selects the same fixed IRQ for
  default macOS HVF as for explicit HVF. The discovery regression fails before
  the fix and passes afterward; compile, all-target clippy, docs, all fourteen
  definitions tests and final repository formatting pass. Native Mac build and
  protected/control execution pass at `8c7963a`. Run `37185577628` was cancelled
  after converter setup took more than twenty-five minutes; native compilation
  and guest execution were not reached. GitHub returned no cancelled-job log,
  so the setup delay's cause remains undetermined. The replacement
  `37187107040` at `1afbb56` builds and checks the native core before starting
  the separately bounded converter, then runs the unchanged acceptance suite.
  The previous executable is not relabeled as the new core.
  That replacement completes native compilation, clippy, docs and tests, starts
  the converter and prepares both real images. Its first policy guest then
  fails during VP creation: `Intel HVF lacks required VMCS control bits 0x4000
  for 0x4012`. The failed guest log and actual native executable are retained and
  hash-verified; the earlier default-network rejection no longer occurs. A
  separate native framework capability diagnostic, run `37190028573` at
  `fd57e2b`, confirms that neither native PAT/EFER MSR access nor the guest PAT
  VMCS field is exposed; the guest EFER VMCS field is exposed. VM and vCPU
  creation and cleanup pass. Source and log digests are independently verified.
  Core `f1f6019b7` preserves and validates PAT in software, accesses EFER through
  VMCS, restores its guest-mode entry control and retains the existing snapshot
  format. Both framework-constraint regressions fail before the fix; all thirteen
  local tests pass afterward, including invalid PAT writes, capture/restore and
  EFER write rules. Native ARM and cross-Intel check, all-target clippy and docs,
  plus final repository formatting pass. A new native Intel build and full
  runtime run, [37190449136](https://github.com/maceip/nvx/actions/runs/37190449136),
  completed its second attempt at NVX `6521f01` / core `f1f6019b7` with a guest VM-entry failure. The matching new Mac executable
  passes all eleven doctor checks and real protected/opposite seccomp execution.
  All eleven pre-existing user edits remain byte-identical after integration.
  No Intel runtime acceptance is inferred from these checks.
  The first core-f1 attempt completes its native build and checks, then GitHub
  reports that the hosted runner lost communication with the server during the
  acceptance step. Neither its job log nor a runtime proof was preserved; the
  job-log API returns 404 and the completed-run fallback reports no log. The
  underlying cause and guest results remain undetermined. The second attempt at the exact same NVX and core revisions retains its actual VMM log: the first protected seccomp guest fails VM entry with `0x80000021` (invalid guest state). All three preparation gates pass; the guest never becomes ready, and no acceptance or history point is admitted. The build artifact from that actual attempt is independently byte-verified and matches the separately exported executable. Independent export
  [37195655106](https://github.com/maceip/nvx/actions/runs/37195655106) retains
  the cached executable: its actual bytes, clean core-f1 provenance and x86-64
  Mach-O architecture are verified in
  `build/completion-f1-native-intel-build-result.json` in the diagnostic checkout.
  The separate history collector
  [37195251612](https://github.com/maceip/nvx/actions/runs/37195251612) was cancelled
  after the same pinned core failed actual guest VM entry. It preserves no
  downloadable history artifact, and its completed job-log API returns 404.
  Its actual guest/measurement progress is unknown. No history points are
  admitted; the cancellation investigation and API results are retained.
  A native capability probe,
  [37197539325](https://github.com/maceip/nvx/actions/runs/37197539325),
  confirms CR0 fixed-one bits `0x80000021` and CR4 fixed-one bits `0x2000`.
  Direct unmasked writes omit the required NE and VMXE bits. Core `d591aaee1`
  applies native fixed-bit policy while keeping architectural guest values in
  read shadows and saved state. It handles paging transitions, MOV, CLTS and
  LMSW, and retains guest cache-disable state virtually. All seventeen backend
  tests pass, as do native ARM and cross-Intel check, all-target clippy, docs,
  and the final repository formatter. The native Intel build, clippy, docs and
  tests also pass in
  [37197856945](https://github.com/maceip/nvx/actions/runs/37197856945).
  Its doctor and two image-preparation gates pass; the first protected seccomp
  guest then times out before readiness. The VMM log contains two startup
  messages and no earlier VM-entry fatal message. The boot cause remains
  undetermined; no full acceptance or timing point is admitted. All recorded
  step hashes and the actual native executable are verified in
  `build/completion-intel-control-state-native-intel-repeat-result.json`.
  The complete build archive matches independent export
  [37200859764](https://github.com/maceip/nvx/actions/runs/37200859764)
  byte for byte, with native x86-64 Mach-O architecture, clean core provenance
  and locally verified code signature. The focused debug diagnostic
  [37202219884](https://github.com/maceip/nvx/actions/runs/37202219884)
  reproduces the same protected guest readiness timeout with the exact failed
  NVX/core revisions, executable and OCI image digest. Clean source and binary
  remain unchanged afterward. Its retained VMM log shows memory, initial
  registers and device initialization completing, then no explanation of the
  guest stall. The archive, actual job log and all bounded evidence files are
  hash-verified in
  `build/completion-intel-control-state-native-intel-debug-result.json`.
  The next exact-build trace replay,
  [37203558244](https://github.com/maceip/nvx/actions/runs/37203558244),
  reproduces the same readiness timeout. Its source, executable and OCI digest
  remain identical. The actual trace proves Linux kernel instruction execution
  and many device-register accesses; its final instruction reads one byte from
  the virtio-fs configuration at physical address `0xd0001100`. The cause of
  the subsequent stall remains unestablished. All bounded evidence and actual
  job-log hashes are verified in
  `build/completion-intel-control-state-native-intel-trace-result.json`.
  A further replay adds only the existing managed REPL and requests read-only
  VM inspection after that same trace marker. None of these focused diagnostics
  supplies full acceptance or performance history.
  A second native API probe,
  [37198280068](https://github.com/maceip/nvx/actions/runs/37198280068),
  accepts every new register mask, read shadow, normalized write and TLB
  invalidation. Its actual VMCS hardware state includes the required NE and
  VMXE bits while the guest read shadows retain the loader's original values.
  Source and native-log hashes are verified. This probe creates and cleans up
  a VM and vCPU without executing a guest; it supplies no runtime acceptance.
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
- The fresh full ARM run at `9117646` / core `75b656015` passes containment,
  simulants, receipts, images, workspace, credentials and all twenty repaired
  clones with both disabled controls. It then fails an MCP warm snapshot because
  memory flushing returns EIO. The outer guest kernel records virtual-disk writes
  and ext4 unwritten-extent failures at the same time; host free space was 586 MiB.
  All exported evidence hashes and unchanged source/executable provenance are
  verified. The failed payload remains in the owned disk. The VM was stopped
  cleanly and superseded completed scratch/staging duplicates were retired only
  after hash verification; original package archives, logs, current accepted
  proofs and essential actual failure payloads remain retained. A fresh complete
  run at `2131a1e` completes all nine gates and the strict ten-point comparison,
  with all three metrics checked and zero regressions. All eighteen supported
  scenarios, eight containment families with opposite controls, four simulants,
  MCP, determinism, twenty repaired clones plus two disabled controls and twenty
  cold/warm requests pass. First stdout is **185.38 ms p50**, completion
  **241.50 ms**, cold first stdout **7766.44 ms**. Every exported file and gate-log
  hash is verified, and source/core/executable remain unchanged. The nested host
  still exceeds the 50 ms target. The owned VM was stopped cleanly afterward.
  The earlier storage failure publishes no acceptance proof and does not
  establish the separate reset's cause.
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
- Native x86 KVM repeat `37183409867` at `b977e3d` / the same core also passes
  all eight runtime steps. Its twenty cold/warm requests measure **14.85 ms** first
  stdout, **19.60 ms** completion and **2146.97 ms** cold first stdout. All nine
  gate-log hashes are independently verified; its final comparison rejects the
  two-point history. The native history workflow now measures ten distinct real
  source revisions per Linux/Windows platform with exact executable provenance,
  unchanged source checks, twenty raw samples per metric and original log hashes.
  The original Windows diagnostic branch retains its actual commit ID. Its first
  twenty-cold/twenty-warm measurement passes, then collector cleanup encounters
  the same readonly scratch attribute. The collector now reuses the already
  verified owned-payload cleanup; the workload subprocesses still run each
  selected checkout unchanged. History collection is now complete on native Linux and Windows; platform acceptance is established only by their actual full strict repeats.
  The Linux job in `37184111713` now completes all ten requested real source
  measurements. Nine current-branch points and the previously verified `81233b9`
  point provide ten base-branch points after excluding the separate diagnostic
  branch. All sixty raw samples per revision, source/executable invariants,
  collected medians and log hashes are verified. A later independent Linux
  attempt at `0244ae5` fails pool refill readiness and is excluded entirely.
  Its cause remains undetermined. The separate diagnostic in `37184966918`
  passes one hundred cold and one hundred warm requests with unchanged exact
  source/executable and verified logs and raw samples. It retains worker
  exception stacks if a failure occurs and never contributes instrumented
  timings to history. A passing diagnostic establishes no fix for the earlier
  intermittent failure.
- Fresh native x86 KVM strict repeat `37185241156` at `84964ed` / core
  `75b656015` passes all nine acceptance gates, including all runtime scenarios,
  containment/opposite controls, MCP, determinism, repaired clones, cold/warm
  requests and the required ten-point performance comparison. All nine gate-log
  hashes and the containment digest are independently verified. Its twenty
  cold/warm samples measure **12.00 ms p50** first stdout, **14.52 ms** completion
  and **1994.77 ms** cold first stdout; all three metrics pass with zero
  regressions. The accepted source and executable are bound in
  `build/completion-strict-native-linux-repeat-evidence/measured-history-full-proof/NVX-ACCEPTANCE.json`
  in the diagnostic checkout.
- Native WHP full repeat `37184286867` at `2131a1e` / core `75b656015`
  passes all eight runtime acceptance steps, including complete scenarios,
  containment, image determinism and twenty cold/warm requests. All nine gate
  logs and the actual native executable provenance are verified. Its ninth gate
  rejects absent performance history, publishing no complete acceptance proof.
  A separate canonical twenty-cold/twenty-warm measurement of the same ancestor
  source in `37185576621` checks unchanged clean source/executable before and
  afterward; all raw samples, collected medians and four log hashes are verified.
  First stdout is **40.29 ms p50**, completion **59.09 ms**, cold first stdout
  **2381.57 ms**. The Windows job in `37184454453` completes all ten requested source measurements. Its nine base-branch points plus the separately verified `2131a1e` point provide ten admitted measurements; the diagnostic-branch point retains its actual identity and is excluded. All samples, source/executable invariants, collected medians and logs are verified.
- Native WHP strict repeat [37186596678](https://github.com/maceip/nvx/actions/runs/37186596678)
  at NVX `19459df` / core `75b656015` passes all nine acceptance gates, including
  the strict ten-point performance comparison. All nine gate-log hashes, the
  containment digest and the raw twenty-cold/twenty-warm samples are independently
  verified. First stdout is **34.91 ms p50**, completion **52.68 ms**, cold first
  stdout **2210.35 ms**, with zero regressions across the three metrics. The
  accepted proof is
  `build/completion-strict-native-windows-repeat-evidence/measured-history-full-proof/NVX-ACCEPTANCE.json`
  in the diagnostic checkout. Core-4b20 native Windows repeat `37186695373`
  at `fd63923` / core `4b20f45f1` passes all eight runtime gates but fails the
  strict performance comparison. First stdout is **57.96 ms p50** against a
  **43.72 ms** historical median; completion is **73.79 ms** against **58.27 ms**.
  Cold first stdout is **2174.15 ms** and passes. All nine logs and the raw
  twenty-cold/twenty-warm samples are independently verified; no complete
  acceptance proof is published. An unchanged-source repeat, `37189066404`,
  retains the same ten-point history, 20% threshold and 1 ms tolerance and
  reproduces both regressions: first stdout **60.67 ms**, completion **80.72 ms**,
  cold first stdout **2254.91 ms**. All eight runtime gates pass again. Every
  recorded gate-log hash and the twenty-cold/twenty-warm medians are verified.
  The failing measurements remain retained and are not imported into the baseline.
  A separate manual diagnostic compares the retained passing and failing builds
  on one native runner, twice each in reversed order, with identical NVX runtime
  sources, twenty cold/warm requests and source/executable checks before and after.
  It cannot contribute acceptance or baseline history.
  The corrected diagnostic completes in run
  [37190956734](https://github.com/maceip/nvx/actions/runs/37190956734) at
  `981b974`. Both retained builds pass all four diagnostic trials, totaling
  eighty cold and eighty warm requests. In baseline/candidate/candidate/baseline
  order, first-output medians are **39.06 / 31.27 / 44.81 / 31.37 ms**.
  The candidate's median of trial medians is **8.03%** slower for first output,
  **3.47%** faster for completion and **1.86%** slower for cold first output.
  Source, actual executable, every step log and all raw sample medians are
  independently verified. Variation between repeats of the same binary prevents
  attributing the earlier failed gates to a consistent binary slowdown. Those
  failed measurements remain retained; the diagnostic supplies neither acceptance nor
  baseline history. Earlier handoff and Python 3.10 hashing failures occurred before any
  guest ran, retain their original logs and supply no runtime measurements.
- Fresh core-`f1f6019b7` Windows repeat
  [37190492819](https://github.com/maceip/nvx/actions/runs/37190492819) at
  NVX `6521f01` passes all eight runtime gates, including the complete scenarios,
  opposite controls, MCP, determinism, twenty repaired clones and twenty
  cold/warm requests. Its strict ten-point comparison fails completion:
  **75.02 ms p50** against **58.27 ms**, a **28.7%** increase. First output
  **46.41 ms** and cold first output **2028.14 ms** pass. The actual newly compiled
  executable, all nine recorded gate-log hashes and raw sample medians are
  independently verified. Reapplying the unchanged gate to the saved measurements
  reproduces the same single failure. The failed measurement remains excluded
  from history. A native synthetic transport diagnostic measures the existing
  polling reader against an immediate pipe-read reference; it supplies no guest
  acceptance or replacement timeout implementation.
  Native diagnostic [37192677178](https://github.com/maceip/nvx/actions/runs/37192677178)
  at `1878a06` measures **15.77 ms** for an immediate response using that reader,
  including **15.60 ms** of polling sleep, against **0.07 ms** for a blocking-read
  reference. All four hundred raw samples and the actual transport source digest
  are verified. The Windows reader now uses overlapped I/O completion events;
  expired reads are cancelled and drained before buffers or events are released.
  Partial-read, disconnect and cancelled-read reuse regressions accompany the fix.
  Native transport validation at `e78e181` now passes all eight control-session
  tests, including the three named-pipe regressions. The same immediate-response
  check fails against retained `1878a06` in
  [37192976045](https://github.com/maceip/nvx/actions/runs/37192976045) and passes
  the fixed reader in
  [37192977864](https://github.com/maceip/nvx/actions/runs/37192977864).
  The medians of trial medians are **15.39 ms** before and **0.07 ms** after.
  Both source digests and all eight hundred raw samples are independently verified.
  The subsequent full guest acceptance result is below; the earlier failed
  acceptance remains bound to its original source.
- The fixed-reader full Windows repeat
  [37193112844](https://github.com/maceip/nvx/actions/runs/37193112844) at
  NVX `de30108` / core `f1f6019b7` now passes all nine acceptance gates,
  including the complete runtime scenarios, opposite controls, MCP, determinism,
  twenty repaired clones and twenty cold/warm requests. First output is
  **26.53 ms p50**, completion **32.35 ms**, cold first output **2091.29 ms**.
  The strict ten-point comparison checks all three metrics with zero regressions;
  both warm medians are below 50 ms. The actual executable, all nine gate-log
  hashes, containment digest and raw sample medians are independently verified.
  The VMM binary is identical to the earlier core-f1 run; the measured polling
  reader was replaced without changing the benchmark or its thresholds.
  The accepted proof is
  `build/completion-event-reader-native-whp-repeat-evidence/whp-proof/NVX-ACCEPTANCE.json`
  in the diagnostic checkout. The earlier failed results remain retained and
  excluded from baseline history.
- Native x86 KVM job [111390011962](https://github.com/maceip/nvx/actions/runs/37186695364/job/111390011962)
  at NVX `fd63923` / core `4b20f45f1` passes all nine acceptance gates with a
  newly compiled, source-bound executable. The strict ten-point comparison checks
  all three metrics with zero regressions. First stdout is **15.93 ms p50**,
  completion **18.82 ms**, cold first stdout **1915.54 ms**. All gate logs,
  the containment digest and raw twenty-cold/twenty-warm samples are independently
  verified in
  `build/completion-core4b20-native-kvm-proof-evidence/work/nvx/nvx/build/hosted-proof/NVX-ACCEPTANCE.json`.
  The overall workflow fails on its separate hosted ARM job because that host
  exposes no usable KVM device; the successful x86 result is independent.
- The fresh core-`f1f6019b7` native x86 KVM repeat
  [37190492890](https://github.com/maceip/nvx/actions/runs/37190492890) at
  NVX `6521f01` passes all eight runtime gates, including the complete scenarios,
  opposite controls, MCP, determinism, twenty repaired clones and twenty
  cold/warm requests. Its unchanged strict ten-point comparison fails first
  output: **18.61 ms p50** against **15.39 ms**, a **20.9%** increase exceeding
  both the 20% threshold and 1 ms tolerance. Completion **22.12 ms** and cold
  first output **1985.94 ms** pass. All nine recorded log hashes, raw sample
  medians and the actual clean-built native executable are independently verified.
  Reapplying the gate to the saved measurements reproduces the same failure.
  No complete acceptance proof is published, and this failed measurement is not
  imported into history. The separate hosted ARM jobs compile the new core but
  reject absent `/dev/kvm`; they supply no new ARM guest runtime evidence.
- The current-source native x86 KVM repeat
  [37193112786](https://github.com/maceip/nvx/actions/runs/37193112786) at
  NVX `de30108` / core `f1f6019b7` passes all nine acceptance gates. First output
  is **17.81 ms p50**, completion **21.98 ms**, cold first output **1972.70 ms**;
  its strict ten-point comparison checks all three metrics with zero regressions.
  Every gate-log hash, containment digest and raw twenty-cold/twenty-warm median
  is independently verified. The actual executable and compressed build archive
  are byte-identical to the earlier core-f1 run that failed first-output latency.
  That earlier failure remains retained; no Linux performance fix is inferred
  from the passing repeat. The complete accepted proof is
  `build/completion-event-reader-native-kvm-repeat-evidence/work/nvx/nvx/build/hosted-proof/NVX-ACCEPTANCE.json`
  in the diagnostic checkout. Separate hosted ARM jobs again reject absent KVM,
  so the overall workflow conclusion is failure despite the accepted x86 job.
- The fresh control-register correction repeat on the owned nested ARM/KVM VM
  at NVX `65329b2` / core `d591aaee1` passes all nine acceptance gates after
  storage recovery. First output is **202.97 ms p50**, completion **250.16 ms**,
  cold first output **7365.71 ms**. The unchanged strict ten-point comparison
  checks all three metrics with zero regressions. All eighteen supported
  scenarios, eight containment families with opposite controls, four simulants,
  MCP, image determinism and twenty repaired clones with both disabled controls
  pass. Every exported file, gate log, containment schema/render and twenty raw
  samples per metric are independently verified, with unchanged clean source
  and actual executable. The accepted proof is
  `build/completion-arm-host/core-d591-native-repeat/after-host-space-recovery/proof/NVX-ACCEPTANCE.json`
  in the isolated completion checkout. The first attempt's storage failure and
  partial evidence remain retained separately. The nested host still exceeds
  the roadmap's under-50-ms warm target.
  All four core-`d591aaee1` repeats are now retained as source-bound performance
  history at their actual measured NVX revision `65329b2`. Their strict gates
  passed against the prior history before these points were imported. The
  history contains 49 admitted measurements across four platforms and 51 raw
  records; the two diagnostic-branch records remain excluded. Original CSV
  rows and all previous provenance records are preserved without alteration.
- The control-register correction's native Windows WHP repeat
  [37197876018](https://github.com/maceip/nvx/actions/runs/37197876018/job/111423514217)
  at NVX `65329b2` / core `d591aaee1` passes all nine acceptance gates.
  First output is **23.88 ms p50**, completion **29.40 ms**, cold first output
  **2366.26 ms**. The unchanged strict ten-point comparison checks all three
  metrics with zero regressions. The native build, all four listener restore
  regressions and all 138 clean-checkout Python tests also pass. The actual PE
  executable, every gate-log hash, containment schema/render and twenty finite
  raw samples per metric are independently verified in
  `build/completion-intel-control-state-native-whp-repeat-evidence/whp-proof/NVX-ACCEPTANCE.json`.
- The control-register correction's native x86 KVM repeat
  [37197876063](https://github.com/maceip/nvx/actions/runs/37197876063/job/111423381901)
  at NVX `65329b2` / core `d591aaee1` passes all nine acceptance gates.
  First output is **16.28 ms p50**, completion **20.70 ms**, cold first output
  **2003.75 ms**. The unchanged strict ten-point comparison checks all three
  metrics with zero regressions. All gate-log hashes, containment schema/render,
  twenty raw samples per metric and the actual clean-built native ELF are
  independently verified in
  `build/completion-intel-control-state-native-kvm-repeat-evidence/work/nvx/nvx/build/hosted-proof/NVX-ACCEPTANCE.json`.
  The overall workflow fails because its hosted ARM runners lack usable KVM;
  that hardware failure does not change the successful x86 job's result.
- The newly compiled native ARM executable from `37186695364` passes doctor and
  a real seccomp protected/opposite control on the owned nested ARM/KVM VM at
  NVX `1afbb56` / core `4b20f45f1`. Clean source, executable digest and both logs
  are verified before and after testing. This is a focused current-core repeat,
  recorded in `build/completion-arm-host/core4b20-focused-runtime/REPEAT-RESULT.json`,
  and does not replace the full nine-gate core-`75b656015` result. The VM was
  stopped cleanly afterward.
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

Apple Silicon macOS, native Windows WHP, native x86 KVM and the owned nested
ARM/KVM repeats pass all nine acceptance gates with core `d591aaee1`. The ARM
repeat still exceeds the roadmap's under-50-ms warm target. Its preceding
storage-failed attempt and partial evidence remain retained separately.
Intel HVF fails its first protected guest's readiness check. Exact-build debug and
trace replays reproduce it; the trace reaches Linux virtio-fs configuration reads
before stopping. The cause remains unestablished; a management-inspection replay
follows. Full Intel runtime acceptance remains absent.
No missing runtime result, notarization, public publication or external integration
is inferred from checked-in source or CI wiring. Snapshots remain architecture and
backend bound. Credential snapshots exclude host proxy state and require a fresh
binding for reuse. Ordinary `--env` values intentionally enter guest memory.

Generated evidence lives under `build/`. Superseded failed-run RAM/scratch copies
were removed to recover host space; their logs, JSON and artifact hashes are retained
with an `OBSOLETE-ARTIFACTS.txt` notice. Current accepted proof artifacts are retained.
User files, Docker volumes/images and unrelated processes were preserved.
