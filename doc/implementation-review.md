# Implementation review — 2026-10-02

## Addendum — F1 re-test, 2026-10-03 (supersedes F1 above; reviewer's text kept verbatim)

F1's premise is wrong and its prescribed probe already exists. Do not act on F1:

- `BPF_DEVCG_DEV_CHAR` is `(1 << 1) == 2`, not `(1 << 5) == 32` — the code's own
  `_Static_assert` says so, and it matches the uapi encoding
  (`access_type == (ACC << 16) | DEV`). Masking with `0xffff` and comparing
  against `2` correctly admits character devices. All jump targets in
  `nvx-device-policy.c` were hand-verified against the instruction indices.
- The "missing positive control" already exists and is enforced:
  `policy_tests.STANDARD_DEVICES` opens + IO-checks null/zero/urandom/full and
  allocates a pty + `/dev/tty`; `validate("device-policy")` requires all six
  `"ok"`, and the runner additionally executes STANDARD_DEVICES under the pure
  default profile, raising "default profile denied a permitted private device"
  otherwise. A deny-all filter fails this validation, not silently passes it.
- Live evidence on the current tree (HVF, 2026-10-03):
  `build/test-results/f1-device-policy/` — protected (ci+MKNOD), risky control,
  and default-profile standard devices all validate in both directions
  (`F1-DEVICE-POLICY-VALIDATE-OK`). Direct probe: all six permitted devices open
  with working IO; mknod of mem/kmsg/disk denied.
- The 12:54 `review-policy` failure cited nowhere above was a transient empty-pipe
  flake (guest child died before writing); the 12:56 re-run passed both scenarios.
- One adjacent thorn, not a blocker: `nvx.py run --profile default` auto-selects a
  warm-snapshot restore, and a restore from the stale
  `build/snapshot-proof/generation` template wedged (25+ min at full CPU, killed).
  Fresh boots via the image path take ~1 min. Stale templates should be pruned or
  the restore should time out loudly before the talk demo.

Scope: the P0–P5 work in the working tree (uncommitted, plus new untracked modules), reviewed
against [roadmap-best-in-class-sandbox.md](roadmap-best-in-class-sandbox.md) and
[implementation-plan.md](implementation-plan.md). This is a review, not a change.

This is the historical review of the October 2 working tree. Its findings and test
counts describe that snapshot; see [implementation evidence](implementation-status.md)
for the subsequent fixes and current validation. The native app now lives in
[maceip/nvx-showcase](https://github.com/maceip/nvx-showcase), outside this checkout.

**Verdict.** This is unusually disciplined work: the security primitives are hand-written and
fail-closed, the image converter is paranoid about traversal and overlay metadata, the proxy
pins DNS before it listens, and — most importantly — the containment suite *requires a failing
negative control in code*, which is the thing nearly every team claims and skips. The defects I
found are not in the threat model; they are in (a) one device-filter instruction I cannot
verify from the host and whose probe cannot catch it either way, (b) a host dependency that
breaks the signing feature on the very platform the talk targets, and (c) CI wiring that will
fail on a clean checkout. All three are cheap to fix; (a) and (b) should be fixed before any
claim is made on stage.

---

## What I actually ran

| Check | Result |
| --- | --- |
| `ruff check scripts/` (ruff 0.15.22 installed into an isolated venv) | **clean** — but `ruff` is not on `PATH` by default, so `scripts/check.sh` dies on line 6 on a clean machine until `pip install -r requirements-dev.txt` |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` (Python 3.13.12) | **82 tests, 4.7 s, 2 errors** (F2 and one environment artifact) |
| `python3 scripts/nvx.py --help` | 41 commands; the new surface (`image`, `policy`, `receipt`, `containment`, `exec/cp/logs/ps/stop`, `warm`, `pool`, `mcp`, `install`, `bundle`, `events`, `doctor`, `setup`, `explain`) is wired |
| Scenario registry | 12 new scenarios + `hvf` in `MICROVM_TEST_BACKENDS` |
| Pinned kernel | `linux-6.18.38` (out-of-tree build dir; sources are not on this host) |

Not verifiable here: live HVF/KVM/MSHV/WHP scenario runs, `pyright` (not installed), Swift
tests, and the 20-clone warm numbers — those I read from `build/*.log` and `data/warm/`.

---

## What is genuinely good

- **Negative controls are enforced, not asserted.** `containment.py::assert_pair` fails unless
  `protected == True and control == False`, and `render()` rejects any document whose
  `control_verdict != "UNCONTAINED"`. A cell cannot be hand-edited green.
- **Seccomp is a real deny-by-default filter.** `nvx-seccomp.c` gates on `arch` with
  `SECCOMP_RET_KILL_PROCESS`, sets `NO_NEW_PRIVS`, and returns `EPERM` by default. I audited all
  197 allowed syscalls: no `ptrace`, `bpf`, `kexec_*`, `init_module`/`finit_module`, `setns`,
  `unshare`, `mount`, `pivot_root`, `chroot`, `io_uring_*`, `keyctl`, `perf_event_open`,
  `userfaultfd`, `pidfd_getfd`, `seccomp`, `process_vm_*`, `settimeofday`, `clock_settime`.
- **The OCI converter is the paranoid part done right.** Path-traversal rejection, symlink
  resolution in guest coordinates with a loop bound, rejection of `trusted.overlay.*` /
  `user.overlay.*` xattrs (allowing only `user.*` and `security.capability`), whiteout/opaque
  synthesis, hardlink targets must be already-admitted regular files, and `/etc/passwd`
  symlink rejection.
- **The credential proxy is not a tunnel.** `CONNECT` is refused with 405; DNS is resolved and
  pinned *before* the listener starts, so a later answer cannot redirect an injected credential;
  inbound `authorization` / `x-api-key` / `cookie` are stripped; injection requires an exact
  `(scheme, host, port)` match inside the allowlist; a per-instance capability token is
  required and is itself in the event-log redaction set.
- **Receipts bind evidence, not just text.** Canonical JSON, `body_sha256`, per-file evidence
  digests verified with symlink rejection, strict flow-log schema with ordering and a
  truncation marker, 16 MiB caps, and secret redaction over `argv` and image ref.
- **Fail-closed defaults.** An unknown profile, a disabled isolation knob without `risky`, or a
  seccomp/device-policy install failure all abort the launch rather than continuing degraded.

---

## Findings

### F1 — Device filter: the type test looks wrong, and no test can catch it (High)

`guest/common/nvx-device-policy.c` masks `access_type` with `0xffff` and then does
`JNE r2, BPF_DEVCG_DEV_CHAR → DENY`. `BPF_DEVCG_DEV_CHAR` is `1 << 5` (`32`). On the pinned
kernel, `__cgroup_bpf_check_dev_permission` builds the context as
`.access_type = (access << 16) | dev_type`, where `dev_type` is the legacy
`DEVCG_DEV_BLOCK = 1` / `DEVCG_DEV_CHAR = 2` (`include/linux/device_cgroup.h`). If that is what
6.18.38 does, the comparison never matches and the filter **denies every device access** —
including `/dev/null`, `/dev/zero`, `/dev/urandom`, `/dev/ptmx` and `/dev/tty`.

- Impact if true: fails closed (no security hole) but silently breaks any workload that opens a
  device node — shell `>/dev/null`, `subprocess.DEVNULL`, PTY allocation.
- Why it is undetected: `policy_tests.DEVICES` only asserts that `mknod` of `null`/`mem`/`disk`
  is *denied* and that `/proc/sysrq-trigger` is masked. A deny-all filter passes that probe
  identically to a correct one. This is the exact failure mode my plan's "negative control"
  rule does not cover: a control proves the protection is present, not that it is *correct*.

**Settle it in one command** inside a default-profile guest:

```sh
python3 -c "import os;print(os.open('/dev/null',os.O_RDWR),os.open('/dev/urandom',os.O_RDONLY))"
```

If that raises `EPERM`, the filter is deny-all. Then fix the comparison (test the bit with
`BPF_JSET`, or compare against the value the kernel actually puts in the low word) and extend
the probe so it asserts **both** directions:

- permitted: `/dev/null`, `/dev/zero`, `/dev/urandom`, `/dev/full`, a `pts` slave, `/dev/tty`;
- denied: any block device, `/dev/mem`, `/dev/kmsg`.

### F2 — Receipt signing is broken on stock macOS (High)

`receipt.py::_openssl` shells out to `openssl pkeyutl -rawin` for Ed25519. macOS ships
LibreSSL 3.3.6, where `genpkey -algorithm ED25519` reports *Algorithm ED25519 not found* — so
`test_receipt.test_signature_requires_trusted_key_and_rejects_modified_signed_body` errors on a
clean Mac and the signing feature itself cannot work there. That is the platform the talk
targets.

Fix: implement Ed25519 in pure Python (~150 lines, RFC 8032) or take a
`cryptography` dependency; stop shelling out and stop writing secrets/temp files on the signing
path. Until then, guard the test with a capability probe and an explicit skip reason, and say in
`doc/usage.md` that signatures require OpenSSL 1.1.1+/3.x.

### F3 — The HVF lane cannot run from a clean checkout (Medium-High)

`.github/workflows/nvx-microvm-tests-hvf.yml` is good in substance (check.sh → pyright →
doctor → `test-microvm --backend hvf` → warm benchmark → gate → containment matrix → artifacts)
but has three problems:

1. It runs `cd showcase && swift test`, and `showcase/` was **deleted from HEAD** (`3ca3918
   "Remove showcase/ (now lives in maceip/nvx-showcase)"`) and now exists only untracked. On a
   clean checkout that step fails.
2. It is `workflow_dispatch` only — fine while there is no committed runner, but it means pull
   requests get zero macOS coverage, and the plan's promise of a wired lane is only half-kept.
3. `doc/ci.md` does not mention the lane at all (it documents the kvm/mshv/whp jobs), so the
   evidence trail is undocumented.

Fix: drop or relocate the Swift step, add the lane to `doc/ci.md` with its manual trigger and
evidence location, and record per-release evidence until a persistent runner exists.

### F4 — Unit coverage is thin where the risk is highest (Medium)

The 82 offline tests are fast and mostly construction-level, which is right. But the newest and
riskiest modules carry one to three tests each: `test_warm` 1, `test_pool` 3, `test_secrets` 3,
`test_proxy` 2, `test_release` 2, `test_explain` 1, `test_lifecycle` 2. The real evidence for
those lives in live scenarios that cannot run in PR CI.

Fix: raise the floor on the thinnest ones (pool quota accounting and lease exhaustion, proxy
redirect/replay and non-allowlisted refusal, secret absence in snapshots, warm clone repair
invariants), or state in `doc/implementation-status.md` which modules are scenario-only so no
one mistakes the unit count for coverage.

### F5 — The performance gate is honest but currently weak (Medium)

`data/warm/` holds a single baseline point and the gate runs with `--minimum-history 1` at 40%
relative / 5 ms absolute tolerance, so any regression under ~40% passes. The status document
says this explicitly — good. Keep saying it wherever the 3.69 ms number appears: it is
request-to-first-stdout against an already-resumed clone, with pool preparation, admission
hashing and refill excluded, and it is not the older x86 bare-guest boot metric. Grow the
history to the normal 10-point window before the number is quoted as a trend.

### F6 — Seccomp has no argument filtering (Low-Medium)

The allowlist is number-only. `clone` / `clone3` with `CLONE_NEWUSER` is therefore permitted, and
`socket` is unrestricted by domain (`AF_NETLINK` included). Capabilities are dropped and
`setns`/`unshare` are denied, so the practical impact is limited to extra kernel attack surface —
but Docker's default profile blocks `CLONE_NEWUSER` explicitly for that reason.

Fix: add argument rules denying `CLONE_NEWUSER` (and, cheaply, `NEWNS`/`NEWNET`/`NEWPID`) on
`clone`/`clone3`; consider restricting `socket` domains.

### F7 — Housekeeping (Low)

Root-level duplicates of both plan documents (`implementation-plan.md`,
`roadmap-best-in-class-sandbox.md` alongside the `doc/` copies), `.workbuddy-ai/` untracked in
the tree, and `doc/implementation-status.md` untracked. Consolidate before the repo is shown
publicly.

---

## Suggested order

1. **F1** — run the one-line guest check; fix the filter and add positive device assertions.
2. **F2** — get `scripts/check.sh` to 82/82 on a stock Mac (pure-Python Ed25519, or a
   capability-gated skip).
3. **F3** — make the HVF lane runnable from a clean checkout and document it in `doc/ci.md`.
4. **F6** — add the `CLONE_NEWUSER` argument rule.
5. **F4 / F5 / F7** — coverage floor, gate history, doc consolidation.

F1 and F2 are the only ones I would block a public claim on. Everything else in this tree — the
containment evidence discipline, the converter, the proxy, the receipt — is better than the plan
asked for.
