# Implementation plan

Companion to [roadmap-best-in-class-sandbox.md](roadmap-best-in-class-sandbox.md). Same ten
features, regrouped into six dependency-coherent phases. **Original acceptance contract.**
See [current evidence](implementation-status.md#the-ten-changes)
for the evidence and gates still outstanding. The requirements below remain the contract.

The one rule that shapes everything below: **a feature is not done until a test would fail
if the feature were deleted.** Every row names the harness, the test, and — where the test
could otherwise pass vacuously — a *negative control* that disables the protection and
asserts the verdict flips.

---

## 1. Test contract (applies to every phase)

### Harnesses that exist today

| Layer | Command | Where |
| --- | --- | --- |
| Host unit | `python3 -m unittest scripts/test_nvx_9p.py scripts/test_control_session.py` | `scripts/check.sh` |
| Lint / types | `ruff check scripts/`, `pyright` (strict, `pyproject.toml`) | `scripts/check.sh` |
| Guest scenario | `nvx.py test-microvm --backend {kvm,mshv,whp} --scenario NAME` | `nvx_tools/microvm_tests.py`, `MICROVM_TEST_SCENARIOS` + dispatch; scripts in `nvx_tools/microvm_test_scripts/` |
| Adversarial | `nvx.py test-adversarial --campaign ...` | `adversarial_cases/*.json`, `adversarial_oracles.py` — scheduled, **not** PR CI |
| Swift (separate optional app) | `(cd ../nvx-showcase && swift test)` | [maceip/nvx-showcase](https://github.com/maceip/nvx-showcase), `Tests/NVXShowcaseTests/` |
| Perf gate | `nvx.py performance collect|gate`, baselines in `data/` | `ci.yml`, `run-platform.yml` |

### Definition of done, per feature

1. **Unit tests** in a new `scripts/test_*.py`, added to `scripts/check.sh`.
2. **Scenario test** with an `NVX-*-OK` marker if the behavior is guest-observable, added to
   `MICROVM_TEST_SCENARIOS` and to the Ubuntu-unsupported set if it is Alpine-only.
3. **Negative control**: the same probe run with the protection off must produce the opposite
   verdict. A containment test that never fails is not evidence.
4. **CI lane wired**: unit + lint on every PR; scenario on kvm/mshv/whp; hvf on the macOS lane.
5. **Docs**: `doc/usage.md` option row + one cookbook recipe.
6. **Perf non-regression**: `performance gate` against `data/` baselines; new suites only land
   with baselines recorded.

### Rules that keep tests real

- Synchronize on markers, never `sleep`. Timing assertions are generous (≥10× margin) and the
  measured value is recorded in the receipt, not asserted as a pass/fail cliff.
- Every new unit module must be reachable from `scripts/check.sh` and run offline, with no
  hypervisor, in under ~30 s.
- Golden vectors are language-neutral and committed (as `control_session.py` already does) so
  Python and Swift tests validate the same bytes.
- Scenario tests must not depend on wall-clock load; they inherit the existing `--timeout`
  phase machinery.

### New harness needed up front

- **A macOS/HVF lane.** Most new features must be verified on a Mac, and CI today only has
  Linux/KVM, Linux/MSHV, Windows/WHP self-hosted pools (`doc/ci.md`). Add one Apple Silicon
  runner and a `nvx-microvm-tests-hvf` job; until it exists, the macOS lane is a documented
  manual pre-release gate and its results are attached to the release.
- **`nvx_tools/containment.py`** — the probe runner for phase 3 (see below).

---

## 2. Phases

Dependencies run left to right. Phases P0→P3 are the pre-talk set; P4 and P5 land after.

```
P0 foundations ──> P1 image ──> P3 evidence ──> P5 speed
      │                │             │
      └──> P2 policy ──┴──> P4 io/secrets ──> P5 reach (SDK/MCP, releases)
```

### P0 — Foundations and diagnostics (roadmap #10, part of #7)

*Why first:* everything else is verified through these, and the Mac audience is gated on them.

| Deliverable | Files | Tests |
| --- | --- | --- |
| `nvx doctor`: hypervisor access, entitlement, Docker, arch-vs-artifact match, cache, versions | new `nvx_tools/doctor.py`, `nvx.py` | `scripts/test_doctor.py`: each check's pass/fail/advice strings; fault injection for missing `/dev/kvm`, wrong-arch artifact, unsigned binary. Negative control: delete an artifact → `doctor` exits nonzero. |
| `nvx explain <id>`: policy denials in plain language with the exact flag to add | new `nvx_tools/explain.py` | `scripts/test_explain.py`: golden denial→message table; unknown event → honest "no explanation". |
| Structured per-run event log (host-side JSON lines) | `nvx_tools/common.py` | `scripts/test_events.py`: ordering, truncation of oversize fields, no secret material (assert token-shaped strings are rejected by the redactor). |
| **HVF in `sandbox`**: EROFS/overlay + managed lifecycle on Apple Silicon | `nvx_tools/sandbox.py`, `microvm_tests.py` | Existing scenarios run under `--hypervisor hvf`: `sandbox-blocks`, `structured-outcome`, `managed-lifecycle`, `workload-identity`. Gate: parity table kvm/mshv/whp/hvf in `doc/ci.md`. |
| Clock sync at boot and after restore; guest-initiated exit on HVF; realpath for snapshot parents | `guest/common/init`, `nvx_tools/snapshot.py` | Scenario `hvf-parity`: boot → `date` within 1 s of host → `/sbin/nvx-exit 0` → host observes exit 0. Negative control: disable clock sample → TLS marker fails and the scenario fails. |

**Exit:** `nvx doctor` green on a clean MacBook, and `test-microvm --backend hvf` runs the
sandbox scenarios.

### P1 — OCI image front door (roadmap #1)

| Deliverable | Files | Tests |
| --- | --- | --- |
| `nvx image pull|convert|ls|rm` + content-addressed layer cache | new `nvx_tools/image.py` (Docker-based converter, off the launch path) | `scripts/test_image.py`: manifest determinism (same input → identical digest, twice), whiteout/opaque synthesis from OCI deletions, metadata policy rejects `trusted.overlay.*` / `user.overlay.*`, unrecognized image flattens to one `custom` layer, cache hit/miss accounting, GC refcounts held by live sandbox **and** snapshot, corrupt manifest rejected. |
| Curated-base split by exact layer-digest prefix | `image.py` | Unit: matching and non-matching prefix tables; identical bytes with different digests do **not** match. |
| `--image` on `run`/`sandbox` (layers + scratch implicit) | `nvx.py`, `sandbox.py` | Scenario `image-run`: `nvx run --image alpine:3.20 -- /sbin/nvx-sandbox-smoke` → `NVX-IMAGE-RUN-OK`, uid 65534, receipt layer digests equal converter output. Determinism: extend `verify-guest-determinism` to rebuild two images twice, byte-identical. |
| Scratch pool/template, no `mkfs` on launch path | `image.py`, `sandbox.py` | Unit: pool acquire/release, concurrent acquires give distinct images, `ENOSPC` surfaced as an actionable error. |

**Exit:** `nvx run --image python:3.12-slim -- python -c "print(1)"` works on Linux/KVM and
macOS/HVF with no hand-built artifacts.

### P2 — Secure by default (roadmap #2)

| Deliverable | Files | Tests |
| --- | --- | --- |
| `--profile {default,ci,risky}` + `nvx.toml` + `nvx policy show|lint` | new `nvx_tools/policy.py` | `scripts/test_policy.py`: precedence CLI > file > default; `lint` rejects contradictory combos (egress allow + `default`); parse errors name the line; rendered profile is deterministic. |
| Agent installs a **seccomp** filter (kernel has it; agent does not use it today) | `guest/common/nvx-init-agent`, new `guest/common/seccomp-nvx-default.json` | Scenario `seccomp-profile`: guest asserts `Seccomp: 2` in `/proc/self/status` and that an allowlist-excluded syscall returns `EPERM`, marker `NVX-SECCOMP-OK`. Negative control: `--profile risky` → `Seccomp: 0` and the syscall succeeds. |
| Cgroup device filter; masked paths; `no_new_privs` retained | `nvx-init-agent`, `nvx-container-enter` | Extend `workload-identity` and `denied-filesystem-paths`: device node access denied under `CAP_MKNOD` profile; masked paths empty. Negative control: unfiltered profile sees them. |
| **Fast-fail egress denial** (RST / admin-prohibited, not a blackhole) | `nvx_tools/` endpoint policy | Scenario `egress-fast-fail`: denied connect is refused within the assertion window and the latency is recorded; extend `l3-l4-egress-policy` and `structured-outcome` to run under `--profile default`. Negative control: legacy drop path times out → scenario fails. |
| Resource caps by default (`pids.max`, `memory.max`, wall timeout) | `policy.py`, `sandbox.py` | Scenario `resource-caps`: fork-bomb simulant stops at the cap, exit 0, host heartbeat survives. Negative control: uncapped profile exceeds the cap. |

**Exit:** the four simulants from `doc/sandbox-showcase.md` (ransomware, identity, fork bomb,
exfiltration) are contained with **no flags at all** under `--profile default`, and each has a
negative control that is *not* contained.

### P3 — Evidence (roadmap #6 + #9)

| Deliverable | Files | Tests |
| --- | --- | --- |
| `receipt v1`: image digests, resolved policy, network flows attempted/allowed/denied, peaks, versions; host-written, versioned | new `nvx_tools/receipt.py` | `scripts/test_receipt.py`: golden v1 document, version-bump rules, `--outcome-report` still emitted for existing consumers, flow-log parsing, tamper detection (flip one byte → `nvx receipt verify` fails), optional signature. |
| Flow-log tap in the network endpoint | endpoint policy | Scenario `receipt-flow-log`: exfiltration simulant → receipt contains a *denied* tuple (dst/port/proto) and an *allowed* tuple for the allowlisted collector; stable modulo volatile fields. |
| `nvx_tools/containment.py` + `nvx containment run|render` | new | `scripts/test_containment.py`: golden probe table, plus **every probe has a negative control** run under `--profile risky` that must report UNCONTAINED. A probe with no failing control is rejected by the runner. |
| Probes: identity, filesystem, process/namespace, devices, resources, network, snapshot integrity, tenant isolation | `containment.py`, `microvm_test_scripts/` | Each probe is an existing or new scenario; results render the README matrix. Wired into `nvx-microvm-tests-{kvm,mshv,whp,hvf}`; `test-adversarial` stays the deeper scheduled campaign. |

**Exit:** the containment matrix is generated, green on all four backends, and each cell has a
recorded negative control.

### P4 — Files in/out and secrets (roadmap #4 + #5)

| Deliverable | Files | Tests |
| --- | --- | --- |
| `--workspace HOST:GUEST[:ro|rw]`, `--out DIR`, `nvx exec|cp|logs|ps|stop` | `sandbox_lifecycle.py`, `control_session.py`, `nvx_9p.py`, `nvx.py` | Extend `filesystem-live.sh` / `managed-lifecycle`: rw share round-trips guest write → host read; ro share rejects write; `exec` returns separate stdout/stderr and exit 37; `cp` byte-exact on a 1 MiB random file; `logs --json` ordered. Unit `scripts/test_workspace.py`: reserved targets (`/`, `/etc`, `/proc`, `/sys`, `/dev`, `/.nvx-agent`) rejected; symlink escape inside a workspace denied. |
| `nvx bundle <id>` reproducible tarball | new | Unit: bundle contains image ref, policy, cmdline, logs, receipt; `nvx bundle verify` on a tampered copy fails. |
| Credential proxy: `--secret NAME`, `--egress-allow HOST:PORT`, `--proxy-log` | core-ify `NVXProxy` | `scripts/test_proxy.py` (Python) + `ProxyTests.swift` share golden vectors: inbound `Authorization` stripped, injection only for allowlisted hosts, non-allowlisted host refused fast, request log records host/method/bytes/verdict. |
| Secret never reaches the guest, the receipt, the logs, or a snapshot | `receipt.py` redactor, snapshot path | Scenario `secret-isolation`: guest `/proc/1/environ` and snapshot files contain no token bytes; allowlisted host succeeds; `evil.example` refused. Negative control: legacy `--env SECRET=...` path leaks and the scenario fails. |

**Exit:** `nvx run --image X --workspace ./src:/src --out ./artifacts -- make test` produces
artifacts on the host, and an agent run completes without the key ever entering guest RAM.

### P5 — Speed and reach (roadmap #3, #8, rest of #7)

| Deliverable | Files | Tests |
| --- | --- | --- |
| `nvx warm --image X` → workload-start snapshot; `nvx pool start --size N` | new `nvx_tools/warm.py`, `nvx_tools/pool.py`, `snapshot.py` | Scenario `warm-clone`: 20 clones from one warm snapshot, each with distinct instance id, fresh generation id, distinct machine ID, working allowlisted egress, correct workload output, and repaired clock/entropy. Unit `scripts/test_pool.py`: lease/return accounting, quota denial spawns nothing, stale decoder state discarded on rehandshake. |
| Warm-start benchmark + baselines | `benchmark.py`, `data/` | New suite in `benchmark`; p50 recorded; `performance gate` wired so a regression fails CI. |
| Signed/notarized darwin-arm64 + linux-arm64 releases | `build.py`, `release.py`, `archive.py` | `scripts/test_release.py`: artifact inventory per platform/arch, entitlement present via `codesign -d`, notarization check on the macOS lane, clean-machine smoke (`download` → `doctor` → `run` prints `NVX-GUEST-BOOT-OK`). |
| `nvx mcp serve` in every release; Python/TS SDK; quotas | core-ify `NVXMCP`; new `sdk/` | `scripts/test_mcp.py` + Swift golden-vector contract: initialize, tools/list, tools/call for run/exec/status/snapshot/files/secrets; version negotiation; quota denial; streaming frames; a tool never returns a secret value. |

**Exit:** warm start under 50 ms p50 (published honestly, whatever the number is), one-line
install on macOS and Linux, and at least one external integration built by someone else.

---

## 3. Gates

```bash
# every commit
./scripts/check.sh                 # ruff + unittest over all scripts/test_*.py
pyright                            # strict, per pyproject.toml
# when changing the optional native app, in its separate repository
(cd ../nvx-showcase && swift test)

# per PR
nvx.py test-microvm --backend kvm --scenario <touched scenarios>
nvx.py benchmark --suite cold-start --output /tmp/bench.json && nvx.py performance gate ...

# pre-release (all backends, incl. hvf)
nvx.py test-microvm --backend {kvm,mshv,whp,hvf}
nvx.py containment run --format md > doc/containment-matrix.md

# scheduled
nvx.py test-adversarial --campaign workload-isolation --replay <last manifest>
```

`scripts/check.sh` must be updated in the same commit that adds each `scripts/test_*.py`, or
the test does not exist as far as CI is concerned.

## 4. Sequencing and parallelism

| Phase | Depends on | Parallelizable with | Talk gate |
| --- | --- | --- | --- |
| P0 | — | P1 converter (host-side, no guest change) | pre-talk |
| P1 | P0 (hvf sandbox for the Mac path) | P2 | pre-talk |
| P2 | P0 | P1 | pre-talk |
| P3 | P2 (needs the protections to assert about) | P4 proxy | pre-talk |
| P4 | P1, P2 | P3 | post-talk (secrets demo is pre-talk if time allows) |
| P5 | P1, P3 | — | post-talk |

Critical path: P0 → P2 → P3. P3 is the slide and the demo; if the schedule compresses, cut P5
first and P4's `bundle` second — never cut a negative control.

## 5. Risks

| Risk | Mitigation |
| --- | --- |
| Warm-start target (P5) is high-variance | Measure first, publish the real number; the talk quotes whichever path is green. No aspiration on stage. |
| macOS lane has no CI runner yet | Ship P0's macOS lane as a manual gate with attached logs; promote to CI when the runner exists. Nothing else claims Mac coverage until then. |
| Seccomp breaks legitimate workloads | Ship `unconfined` from day one; the `ci` profile is the documented loosening; cookbook lists which workloads need it. |
| Cache/GC bugs silently corrupt layers | Digests verified on admission and on restore; `nvx image verify` as a standalone command; GC refcounts tested with a live sandbox and a snapshot holding the same blob. |
| Test suite becomes the bottleneck | Unit layer stays offline and fast; scenarios stay marker-driven and parallel per backend; adversarial stays scheduled. |

## 6. Explicitly not tested (so nobody infers a claim)

Cross-architecture and cross-hypervisor snapshot restore; kernel CVE-class or side-channel
resistance; multi-tenant isolation on a shared host beyond "no shared scratch, no
cross-instance visibility"; anything requiring nested virtualization. The containment matrix
states its own scope on its face.
