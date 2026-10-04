# Roadmap: from a working fork to a best-in-class, generic microVM sandbox

Status: **Canonical proposal and acceptance contract.** See
[current item-by-item evidence](implementation-status.md#the-ten-changes) for implemented
behavior, verification and remaining gates. Every original "today" statement below was
read out of this tree (fork of `microsoft/nvx`,
branch `dev`, HEAD `9ad9af9`) and cites the file that carries the evidence.
The historical `showcase/` paths below now live in the separate
[maceip/nvx-showcase](https://github.com/maceip/nvx-showcase) repository.

Audience for this document: us, before the conference talk. Two goals that pull in
slightly different directions, and this plan tries to satisfy both:

- **Talk value.** A live demo where untrusted code runs in an NVX microVM and visibly
  fails to escape, outperforms the obvious comparison, and leaves a receipt behind.
- **Adoption value.** A stranger on a Mac or a Linux box can get value in under five
  minutes and can keep using it after the talk without us in the room.

The second goal is the one that decides whether this becomes a project or a demo.
Everything below is ranked by *adoption impact per unit of work*, with talk value as a
tiebreaker.

---

## 0. Where we actually are (evidence, not vibes)

What the fork has today that upstream does not:

| Capability | Evidence |
| --- | --- |
| macOS / Apple Silicon (HVF) boot path: aarch64 kernel config + patches, `--hypervisor hvf` | `kernel/config-microvm-aarch64`, `kernel/patches-aarch64`, `scripts/nvx.py` (`HYPERVISORS` includes `hvf`) |
| aarch64 guest artifacts, virtio-net via consomme, scripted snapshot save/restore on HVF | `doc/usage.md` (`--save-snapshot`, `--memory-backing-file`, `--virtio-net`) |
| Agent ergonomics: one-command Claude Code / Codex in a guest | `scripts/nvx-claude-codex.sh`, README section |
| A native macOS app (Runtime + Snapshots tabs), an MCP server, and a credential-shielding HTTP proxy | `showcase/`, commit `9ad9af9` (`NVXCore`, `NVXMCP`, `NVXProxy`) |
| A measured, honest comparison against Firecracker with live results | `doc/sandbox-showcase.md` |

What the upstream design docs say is still missing (this is our real gap list):

- No image conversion path: layers must be hand-built EROFS with hand-supplied UUIDs
  (`doc/design/sandbox-filesystem-and-agent-architecture.md`, "Image preparation and
  distribution (Proposed)").
- No seccomp filter installed by the agent, no cgroup device filter, no masked paths —
  the kernel config enables the facilities; the agent does not use them (same doc,
  "Production agent and launch sequence (Proposed)"; seccomp appears only as a kernel
  config item in `doc/build.md`).
- `sandbox` accepts only KVM/MSHV hypervisors in practice; **HVF is not wired into the
  sandbox command** (`scripts/nvx.py`, `--hypervisor` at line 1592 draws from the same
  `HYPERVISORS` tuple, but the sandbox launch path and its tests are Linux-only, and
  release platforms for `darwin` are `hvf`-only at line 294).
- Egress denial is a **silent blackhole** — the client waits for its full timeout
  (`doc/sandbox-showcase.md`, "Honest warts").
- No cross-hypervisor and no cross-architecture snapshot restore; no public sandbox
  snapshot workflow; Linux can probe block contents before PID 1, so a "platform
  template" cannot yet be proven image-free (`doc/design/current-limits.md`).
- Known rough edges that will burn a first-time user: stale guest clock breaking TLS,
  no ICMP on macOS, 512 MiB default too small for installers, no guest-initiated clean
  exit on HVF, symlinked snapshot parents (README; `doc/usage.md`).

Measured numbers we can quote on stage and must not regress: cold start ~294–357 ms,
snapshot restore median **247.6 ms** (p95 265.9, 128 MiB / 1 vCPU / KVM), deterministic
Ubuntu initramfs + EROFS rebuilds (`doc/sandbox-showcase.md`).

---

## The ten changes

### 1. An OCI image front door: `nvx run --image`

**Problem.** Today, running one containerized workload requires building EROFS layers,
preformatting an ext4 scratch image, and passing `--layer distro,path,<UUID>` by hand.
No one outside this repo will do that. This single fact caps adoption at approximately
"people who read `doc/design/`".

**Change.** Build the conversion step the design doc already asks for, and put it behind
a one-liner:

```bash
nvx image pull python:3.12-slim        # -> content-addressed EROFS layers in ~/.nvx/cache
nvx run --image python:3.12-slim -- python -c "print(1)"   # layers + scratch are implicit
nvx run --image ghcr.io/acme/ci:1.4 --workspace ./src -- make test
```

Mechanically: the converter runs off the launch path (a container, since we already
require Docker for guest builds), splits a curated base into `distro` + `runtime` +
`custom` by exact layer-digest prefix match, synthesizes OCI deletions as overlay
whiteouts, applies the deny-by-default metadata policy (`trusted.overlay.*` /
`user.overlay.*` never copied; caps/SELinux/ACLs only under explicit policy), and emits a
small manifest keyed by digest. Unrecognized images flatten into one `custom` layer and
still work. Scratch comes from a prepared pool/template, never `mkfs` on the launch path.

**Why it's the top item.** It converts NVX from "a kernel + a build system" into "a thing
that runs your image". It is also the single most generic change in this list: OCI is the
one image format everybody already has.

**Talk value.** `nvx run --image` vs. the same thing on Firecracker, which has no
workload-staging primitive at all — that asymmetry is already one of our findings
(`doc/sandbox-showcase.md`).

**Effort.** L (largest item). Dependencies: blob cache + GC (#8's quota work shares the
cache), scratch pool.

---

### 2. Secure by default: a named policy profile, seccomp, and loud denials

**Problem.** Safe behavior today is opt-in and incomplete. `--network-egress deny` must
be asked for; there is no seccomp filter; no cgroup device filter; no masked `/proc`
paths; and when a policy blocks something the guest learns by silence.

**Change.** Make the hardened profile the default and give it a name and a file:

```bash
nvx run --image X --profile default      # non-root uid, all caps dropped, no_new_privs,
                                         # seccomp=nvx-default, egress=deny,
                                         # host mounts=none, pids.max/memory.max set, timeout
nvx run --image X --profile ci           # + workspace mount, egress allowlist, receipt
nvx run --image X --profile risky --network-egress allow --cap net_raw   # explicit, logged
nvx policy show default                  # prints the resolved profile as TOML/JSON
nvx policy lint ./nvx.toml               # reject dangerous combinations before launch
```

Three concrete hardening items inside it:

1. **Install a seccomp filter.** The kernel already has seccomp/BPF and cgroup-BPF
   (`doc/build.md`); the agent does not use them. Ship one curated allowlist profile
   (default) plus `unconfined`, and state plainly which syscalls the default blocks.
2. **Fast-fail network denial.** Refuse with TCP RST / ICMP admin-prohibited instead of
   dropping, so a blocked exfiltration attempt fails in milliseconds. Today it burns the
   client's full 8-second timeout — a wart we already admit on stage. Fixing it removes a
   wart *and* makes the demo sharper (the malware fails instantly instead of hanging).
3. **Cgroup device filter + masked paths**, so profiles that must keep `CAP_MKNOD` (e.g.
   systemd-as-entrypoint images) still cannot reach host devices.

**Why adoption.** Secure defaults are the product. A sandbox that is safe only when
configured by an expert is a sandbox that will be misconfigured, and the first CVE filed
against it will be about the default.

**Why generic.** A `--profile` + `--policy` file is how every sandbox product is
configured; it also gives us one place to express the matrix we show in the talk.

**Effort.** M. Independent of #1; can land first if we want an early win.

---

### 3. Instant start: warm templates and a pre-warmed pool

**Problem.** 294–357 ms cold / 247.6 ms restore is excellent for a VM and still too slow
to be the unit of work in an agent loop or a test suite that spawns one sandbox per case.
Our own ablation notes say restore "wins on resumed warm state", not on raw speed — so
the win is only real if we actually pre-warm.

**Change.** Two layers:

- **`nvx warm --image X`** produces a *workload-start snapshot*: boot, run the guest
  agent, let the language runtime initialize, then checkpoint at a single-threaded,
  barrier-held point (the design doc's "warm shim"). This is the cloneable starting point.
- **`nvx pool start --image X --size 8`** keeps N resumed clones hot with repaired clocks,
  entropy, and network identity, so `nvx run` becomes "grab a clone, bind config, go".
  Target: single-digit-milliseconds to workload-runnable.

Restore repair must be explicit and tested: clock sample, CRNG reseed, generation-ID
restore detection, stale block-buffer invalidation, and discarding partial decoder state
from the old host connection (`doc/design/snapshot-and-restore.md`).

**Why adoption.** "Faster than a container" is the only headline that makes people switch
from Docker for untrusted work. Everything else in this list is table stakes; this is the
differentiator.

**Why generic.** A pool + clone API is the primitive every agent harness wants, and it is
hypervisor-agnostic — which also keeps the KVM/MSHV/WHP/HVF story honest.

**Talk value.** The number on the slide becomes "N ms from request to running untrusted
code", measured live, with a warm clone — not a boot-time microbenchmark.

**Effort.** L. Depends on the trusted platform-template build point (placeholder layers,
provenance) from the design docs. Highest-variance item: schedule it knowing it may only
partially land before the talk, and keep the demo honest either way.

---

### 4. Files in, files out: workspace, exec, logs, copy

**Problem.** Getting data into or out of a sandbox today means seeding an ext4 overlay
from the host via loop-mount, or POSTing results over the network (exactly what the
Firecracker comparison arm had to do). That is unacceptable ergonomics for anyone whose
job is "run a build" or "run a test".

**Change.** Make the managed lifecycle the normal path, with container-shaped verbs:

```bash
nvx run  --image X --workspace ./src:/src --out ./artifacts -- make test
nvx exec  <id> -- cat /src/report.xml      # via the existing control console
nvx cp    <id>:/src/report.xml ./          # or: --out already landed it
nvx logs  <id> [--follow] [--json]         # structured, not a console scrape
nvx ps                                     # live sandboxes, images, RSS, wall time
nvx stop  <id> [--timeout 10]              # graceful through the agent, then terminate
```

Two rules that keep it safe and generic:

- `--workspace` is a **virtio-fs host export with a declared mode** (`ro` default), using
  the machinery that already exists (`--serve`, `--mount`, `--mount-deny`). Writable
  exports are explicit and recorded in the receipt.
- `--out` is a host directory that receives artifacts on clean exit only, and only from
  paths the workload was told about. No surprise host writes.

**Why adoption.** "Run my build in a sandbox and give me the output" is the use case with
the largest surface area: CI, untrusted PR builds, malware triage, data processing.
Today it is possible but not pleasant; with these verbs it is one line.

**Effort.** M. Mostly plumbing over `sandbox_lifecycle.py`, `control_session.py`, and
`nvx_9p.py`, all of which exist.

---

### 5. Credentials that never enter the guest

**Problem.** The README's macOS agent demo ends with "export `ANTHROPIC_API_KEY` /
`OPENAI_API_KEY` at the guest shell". That is the least secure part of the whole system:
the secret is now in guest RAM, in any snapshot you take, and in anything the workload can
read. `NVXProxy` (commit `9ad9af9`) already proves the right shape — header sanitization
and token injection on the host — but it is a showcase component, not a documented CLI
feature.

**Change.** Productize it:

```bash
nvx run --image X \
  --secret ANTHROPIC_API_KEY            # value read from host env/keychain, never in guest
  --egress-allow api.anthropic.com:443  # DNS name, not just CIDR
  --proxy-log ./proxy.jsonl             # every request: host, method, bytes, verdict
```

The guest gets a proxy address and nothing else; the host injects the header, strips
inbound/ambiguous auth headers, enforces the destination allowlist, and refuses
non-allowlisted hosts with a fast, explainable error. Snapshots taken while a secret is
live either exclude proxy state or refuse to capture — pick one and document it.

**Why adoption.** "Let an AI agent write and run code without giving it my keys" is the
most requested sandbox capability of the moment, and we are one small step from owning it
credibly. It is generic: the mechanism is "named secret + destination allowlist", not
"Claude support".

**Talk value.** Highest-audience-appeal demo in the set: run an agent in the sandbox,
have it try to POST the key to an attacker endpoint, show the refusal and the log line.

**Effort.** M (much of `NVXProxy` exists; this is CLI surface, allowlist policy, and
snapshot interaction).

---

### 6. A run receipt: what ran, what it touched, what was blocked

**Problem.** `--outcome-report` exists and is good (`instance_id`, backend, outcome,
network policy, teardown checklist). It is not yet the artifact that makes NVX usable in
CI or in an incident review: it does not record the network flows that were attempted and
blocked, the exact image digests, resource peaks, or the versions of kernel/agent/VMM.

**Change.** One signed-ish, stable, versioned JSON document per run, written by the host
(not the guest), plus a JSON-lines event stream:

```json
{
  "receipt_version": 1,
  "instance_id": "...", "image": {"ref": "...", "manifest_digest": "sha256:...",
    "layers": [{"role": "distro", "digest": "sha256:..."}]},
  "policy": {"profile": "default", "egress": "deny", "allow": [...], "seccomp": "nvx-default",
    "uid": 65534, "caps": [], "pids_max": 128, "memory_max": "...", "mounts": []},
  "network": {"attempted": [{"dst": "1.2.3.4", "port": 443, "proto": "tcp", "verdict": "denied"}],
    "allowed": [...], "bytes_in": 0, "bytes_out": 0},
  "resources": {"peak_rss_mib": ..., "wall_ms": ..., "cpu_ms": ...},
  "versions": {"kernel": "...", "initramfs": "...", "openvmm": "...", "agent": "...", "abi": 2},
  "exit": {"code": 0, "signal": null, "teardown": "guest-exit"}
}
```

Add `--receipt-signature` (host key, optional) and `nvx receipt verify`. Keep the schema
versioned and never break `--outcome-report` consumers — extend it, or emit both.

**Why adoption.** This is what turns a sandbox into something a security team will approve
and a CI pipeline will gate on. Nobody adopts a sandbox they cannot audit.

**Talk value.** The receipt *is* the demo artifact: project the network section while the
ransomware simulant tries to phone home, and the "denied" tuple appears on screen.

**Effort.** S–M. The flow-log tap is the only real work; everything else is aggregation.

---

### 7. Ship macOS/ARM64 as a platform, not a fork

**Problem.** The Mac path works but is held together by manual steps in the README: build
OpenVMM with `--backend hvf`, hand-write an entitlements plist, `codesign` it yourself,
build the guest, accept a stale clock, no ICMP, no clean guest exit, remember to pass
`/private/tmp` instead of `/tmp`. And `sandbox` — the interesting command — is not wired
to HVF at all. A conference audience is mostly Macs; if `brew install nvx && nvx run`
isn't the experience, the talk ends with zero installs.

**Change.**

- `nvx setup` (or `brew postinstall`) that does the entitlement signing automatically and
  verifies it (`scripts/nvx_tools/openvmm-macos.entitlements.plist` already exists).
- Publish signed + notarized macOS **and** Linux release artifacts for **both** x86_64 and
  aarch64, including aarch64 kernel/initramfs, so `nvx download` works on Apple Silicon
  with no local build. Today's release platform matrix is `macos-hvf`, `linux-kvm`,
  `linux-mshv`, `windows-whp` — extend it with architecture, not just hypervisor.
- **Make `sandbox` work on HVF**, including the EROFS/overlay layer path and the managed
  lifecycle. This is a hard prerequisite for #1, #4, #5, and #6 mattering on a Mac.
- Fix the papercuts with generic mechanisms, not Mac special-cases: host clock sample at
  boot (also fixes snapshot restore), guest-initiated exit (`nvx-exit 0`) on HVF, realpath
  resolution for snapshot parents, a default memory size that doesn't fill tmpfs, and — if
  HVF permits — ICMP.
- Say plainly that snapshots are architecture-bound (an x86_64 snapshot never restores on
  ARM64). Cross-arch restore is *not* a goal; pretending otherwise will burn a user.

**Why generic.** Every one of these is "the Linux/Windows path already does this; make the
Mac path equal", which is exactly the work that belongs upstream rather than in a fork.

**Effort.** M–L, mostly mechanical, and the highest prerequisite density of any item.
Start it early even if #1 is the headline.

---

### 8. Agent-native surface: SDK + shipped MCP server + quotas

**Problem.** `NVXMCP` exists in `showcase/` with `nvx_sandbox_exec`, `nvx_status`,
`nvx_snapshot_checkpoint`, `nvx_snapshot_rollback`, `nvx_snapshot_list`,
`nvx_proxy_status` — a good instinct, but it lives in a macOS Swift app, is not part of any
release, and has no defined quota model. Meanwhile the actual integration surface people
will use is programmatic, not a terminal.

**Change.**

- Ship one binary/command, `nvx mcp serve`, in every release; document it as *the*
  integration point. Keep the tool set small and stable: run, exec, status, snapshot,
  files, secrets (never returning secret values).
- Add a thin Python and TypeScript SDK over the same local JSON-RPC/HTTP control endpoint,
  with streaming stdout/stderr, cancellation, timeouts, and idempotent handles.
- Add a quota/limit model: max concurrent sandboxes, per-sandbox and aggregate CPU/memory,
  wall-clock caps, snapshot budget. Without this, an agent that loops will take the laptop
  down — and "the agent killed my machine" is the fastest way to lose an adopter.
- Keep it product-neutral. `scripts/nvx-claude-codex.sh` is a demo recipe, not core.

**Why adoption.** In 2026 the buyer of a sandbox is frequently another program. A stable,
documented programmatic surface with quotas is what makes NVX embeddable, and it is where
"generic" is won or lost.

**Effort.** M. Shares the blob cache and lifecycle plumbing with #1 and #4.

---

### 9. A public containment battery and scorecard

**Problem.** We have `test-adversarial` (Copilot-driven, budgeted, with oracles) — an
impressive research artifact — and `test-microvm`, but no compact, deterministic,
*publishable* statement of what NVX contains. Security buyers do not run a 900-second AI
campaign; they look for a table.

**Change.** Ship a deterministic escape/containment suite that runs in minutes in CI:

| Probe family | Expected verdict under `default` profile |
| --- | --- |
| identity & privileges | uid != 0, `CapEff` empty, `no_new_privs` |
| filesystem | `mount` denied, host paths invisible, virtio-fs export respects mode |
| process/namespace | cannot see or signal the agent, no escape from the workload cgroup |
| devices & kernel | no raw port I/O, no config region or control device in the namespace |
| resources | fork bomb capped by `pids.max`, memory capped, OOM reported not fatal to host |
| network | egress denied by default, allowlist honoured, ingress refused |
| snapshot integrity | tampered manifest/memory rejected; restore refuses foreign arch |
| neighbor/tenant | no shared scratch, no cross-instance visibility |

Publish the result as a **containment matrix** in the README, generated from
`--outcome-report`, and gate CI on it. Keep `test-adversarial` as the deeper campaign that
runs on a schedule, not on every PR.

Hardening items that fall out of writing the battery: seccomp (#2), cgroup device filter
(#2), masked paths, and a hard rule that the control console and config region are never
visible inside the workload.

**Why adoption.** Trust is the product feature. A reproducible, versioned "here is exactly
what it stops, and here is what it does not" is the single most persuasive artifact we can
publish — more than any benchmark.

**Talk value.** The matrix is the slide; one live cell (exfiltration or fork bomb) is the
demo; the Firecracker comparison row is the punchline.

**Effort.** M. Reuses `microvm_tests.py`, `adversarial_oracles.py`, and the outcome
schema.

---

### 10. `nvx doctor`, explainable failures, and a cookbook

**Problem.** Every rough edge we documented is a place a new user silently gives up:
stale clock → TLS failure; `/tmp` symlink → snapshot path error; missing entitlement →
cryptic hypervisor error; 512 MiB → tmpfs full; blocked egress → hang; Docker missing →
build failure with a stack trace. None of this is exotic; it is just un-diagnosed.

**Change.**

```bash
nvx doctor            # /dev/kvm or entitlement, docker, arch vs. artifacts, cache, versions
nvx explain <id>      # "blocked: egress to 203.0.113.9:443 not in allowlist —
                      #  add --egress-allow 203.0.113.9:443 or --profile ci"
nvx events <id>       # the JSON-lines stream, human-formatted
nvx bundle <id>       # a reproducible tarball: image ref, policy, cmdline, logs, receipt
```

Plus: one structured event log per run (host-side), sane defaults (clock sync, memory
floor, tmpfs warnings), and a **cookbook** in `doc/` with ~10 copy-paste recipes —
"run an untrusted npm install", "sandbox a CI job", "run an agent with a scoped key",
"triage a suspicious binary", "snapshot, inspect, roll back". Recipes, not API reference,
are what people actually read.

**Why adoption.** Time-to-first-success is the whole game. This item is cheap and it
multiplies the value of items 1–9.

**Effort.** S. Do it early; it also makes the talk rehearsals survivable.

---

## Suggested sequencing

**Before the talk (must land, in this order):**

1. #10 `doctor` + explainable errors + cookbook — cheap, unblocks rehearsals.
2. #7 macOS/ARM64 parity, especially `sandbox` on HVF and signed release artifacts.
3. #2 default hardened profile + seccomp + fast-fail denial (removes a stage wart).
4. #6 run receipt with the network flow log (the demo artifact).
5. #9 containment battery + published matrix (the slide).
6. #5 secrets via proxy (the crowd-pleaser demo).

**Right after the talk:**

7. #1 OCI image front door (the adoption unlock — do not ship the talk without at least a
   working `nvx image pull` for the two images used on stage).
8. #4 workspace/exec/logs/cp.
9. #8 SDK + shipped MCP + quotas.
10. #3 warm pool (highest variance; measure honestly and publish whatever number is real).

## Keeping it generic (and out of fork-debt)

The fork currently mixes two kinds of code, and the split matters more than any single
feature:

- **Generic, belongs upstream:** the aarch64 kernel config and patches, `--hypervisor hvf`,
  `sandbox` on HVF, the OCI converter, the policy profile, the receipt, the containment
  battery, `doctor`.
- **Fork/local, must stay optional:** `showcase/` (macOS GUI, `NVXCore`, `NVXMCP`,
  `NVXProxy`, `RepoRoot.swift` resolving a repo path on disk),
  `scripts/nvx-claude-codex.sh`, and anything that hardcodes a host path, a vendor, or a
  single agent product.

Rules I'd adopt now: the CLI, its output schemas, and the guest ABI are the compatibility
surface — everything else ships as an extra. No feature may require the macOS app. No
demo may require a specific vendor's CLI. Every Mac convenience becomes either a platform
parity fix (generic) or an example under `examples/` (not core). That is what keeps this
usable by someone who has never heard of the talk.

## Anti-goals (explicitly not building)

- **Cross-architecture or cross-hypervisor snapshot restore.** Architecturally bound; say
  so loudly instead of half-supporting it.
- **Multi-container pods / a Kubernetes-shaped API.** The single-workload-per-microVM
  specialization is a deliberate strength (`doc/design/sandbox-filesystem-and-agent-architecture.md`).
- **A general init system in the outer guest, or systemd-as-workload support beyond one
  documented compatibility profile.**
- **Nested virtualization, PCI, arbitrary devices, firmware boot.** These break the fixed
  ABI that makes the isolation argument legible.
- **A hosted service.** Staying local and single-node is what keeps this installable.

## Success metrics (six months post-talk)

- `brew install nvx && nvx run --image alpine -- echo hi` succeeds on a clean MacBook in
  under five minutes, with no build step.
- p50 time-to-workload-runnable on a warm pool is under 50 ms; cold start does not regress
  past today's ~294–357 ms.
- The containment matrix is green in CI on Linux/KVM, Linux/MSHV, Windows/WHP, and
  macOS/HVF, and is regenerated from `--outcome-report` rather than hand-written.
- At least three integrations exist that we did not write (agent harness, CI runner,
  triage tool), all through the SDK/MCP surface.
- Zero documented "you must edit this file by hand" steps to get a first sandbox running.

## Appendix: where each item lands in the tree

| # | Primary files | Design doc that already asks for it |
| --- | --- | --- |
| 1 | new `nvx_tools/image.py`, `sandbox.py`, `build.py` | sandbox-fs-and-agent §"Image preparation and distribution" |
| 2 | `sandbox.py`, `guest/common/nvx-init-agent`, new `policy.py` | sandbox-fs-and-agent §"Production agent"; `current-limits.md` |
| 3 | `snapshot.py`, new `warm.py`/`pool.py`, `guest/common/nvx-snapshot` | `snapshot-and-restore.md`, §"Checkpoint handoff" |
| 4 | `sandbox_lifecycle.py`, `control_session.py`, `nvx_9p.py` | sandbox-fs-and-agent §"Control protocol" |
| 5 | `showcase/Sources/NVXCore/NVXProxy.swift` → core, new `secret.py` | (new; proxy exists) |
| 6 | `sandbox.py` outcome schema, new `receipt.py` | `run.md` outcome report; issue-level schema work |
| 7 | `nvx.py` platform matrix, `build.py`, `openvmm-macos.entitlements.plist` | `distribution.md`, `run.md` |
| 8 | `showcase/Sources/NVXMCP/main.swift` → release binary, new SDK | (new) |
| 9 | `microvm_tests.py`, `adversarial_oracles.py`, new `containment/` | `copilot-adversarial-testing.md`, `validation.md` |
| 10 | new `doctor.py`, `nvx.py` error paths, `doc/cookbook.md` | (new) |
