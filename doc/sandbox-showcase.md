# NVX vs Firecracker: malicious-code sandbox showcase (talk plan + evidence)

Status: DRAFT — all cells below were executed live except where marked.
Run on Linux/KVM; analyze on Mac. Single instance throughout —
scale is elucidated from the wiring, never measured.

## Environment (measured, not claimed)

- Linux measurement box: Azure Ubuntu 26.04, x86_64, 4 vCPU, 31 GiB RAM,
  `/dev/kvm` present.
- NVX: fork at `maceip/nvx` `dev` (`def84b4`), openvmm `6d9a62155`
  (includes snapshot-chaining work), release binary, backend `kvm`.
- Firecracker: stock release v1.17.0 binary, no jailer, no seccomp
  customization, TAP+NAT hand-wired on the host.
- Same guest family both arms: NVX release kernel `vmlinux` + initramfs
  (alpine for NVX `sandbox`/`run`, unpacked into an ext4 rootfs for
  Firecracker). Same simulant scripts, adapted only for shell/basename
  differences.

## The wiring (one cell)

NVX arm, per case — one command:

```bash
python3 scripts/nvx.py sandbox run \
  --layer distro,build/ubuntu-distro.erofs,<UUID> \
  --scratch case-scratch.ext4 \
  --entrypoint /sim-<case>.sh \
  --net 10.0.0.2/24 --network-profile portable \
  --network-egress deny --network-egress-allow 10.0.0.1:tcp:18080 \
  --pids-max 128 \
  --outcome-report out-<case>.json
```

Simulants are seeded into the scratch overlay (`upper/`) from the host
via loop-mount. Verdicts come back three ways: guest `SIM|` console
lines, the process exit code, and `--outcome-report` JSON
(`instance_id`, `backend`, outcome, network policy, teardown checklist)
— the machine-readable reward signal.

Firecracker arm, per case — hand-wired: TAP device + NAT + forwarding
on the host, ext4 rootfs baked with simulants, static IP in the guest,
5 API calls (boot-source, drive, network-interface, machine-config,
start), results POSTed back by the guest, post-mortem log read via
`debugfs`. The setup asymmetry IS part of the demo.

## Verdict matrix (all cells executed live)

| case | NVX (policy on) | Firecracker (stock) |
|---|---|---|
| ransomware | loot on scratch encrypted (sim works), system writes blocked 2/2, no host paths visible → `CELL_ONLY`, exit 0 | 4/4 writes as uid 0 → `UNCONTAINED`, exit 1 |
| identity | uid 65534, `mount` denied, `CapEff` zero → `NONROOT`, exit 0 | uid 0, `mount` allowed → `UNCONTAINED`, exit 1 |
| fork bomb | 100 processes, capped by `--pids-max 128` → `CAPPED`, exit 0 | 238 processes → `UNCONTAINED`, exit 1 |
| exfiltration | attacker endpoint unreachable under `--network-egress deny` + report pinhole; verdict receipt delivered → `BLOCKED`, exit 0 | `secret-data` POSTed to attacker listener in 0 s → `EXFILTRATED`, exit 1 |
| exfil control (NVX, no policy flags) | same malware exfiltrated in 0 s — the policy is the difference | n/a |

## Honest warts (say them on stage)

- NVX egress-deny is a silent blackhole: the blocked attempt took the
  full 8 s client timeout, not a fast refusal.
- The fork-bomb simulant needed four revisions: dash exits silently on
  fork failure, so at a tight cap the shell dies before reporting —
  hence the census-gated spawner (builtin-only process count, stops
  before touching the cap).
- Firecracker has no workload-staging primitive: simulants ride in a
  hand-built ext4 image, results come back over the network or
  post-mortem. That friction is a finding, not a complaint.
- One exfil control run showed a doubled POST receipt (client retry);
  ground truth is the attacker listener log, which is unambiguous.

## Single-instance timeline (measured on the measurement box)

- `sandbox run` one-shot: boot → workload → exit + outcome JSON in
  seconds (see per-case logs in `~/demo/zoo/`).
- `benchmark --suite shell-snapshot-restore` (128 MiB, 1 vCPU, KVM):
  snapshot restore median **247.6 ms** (p95 265.9, n=3).
- `benchmark --suite cold-start`: ~294–357 ms across scenarios.
- Ablation line: restore is not an order of magnitude faster than cold
  boot on tiny guests — it wins on *resumed warm state*, which is what
  the forensics loop shows next.

## Forensics loop (executed live)

1. Boot microVM, infect guest tmpfs (ransomware-lite).
2. Guest-initiated capture (`nvx-snapshot` → `--snapshot-destination`);
   source is terminal, snapshot published (manifest/state/memory).
3. Transfer 512 MiB to the Mac; `nvx.py snapshot verify` passes and
   reports `x86_64`, firmware boot, manifest v5.
4. Restore on Linux: guest resumes with the encrypted loot intact —
   the exact compromised moment, resumable.

## Portability boundaries (say plainly)

- Snapshots are arch-bound (VP/GIC state, enforced `architecture`
  field): an x86_64 snapshot can never restore on ARM64. The Mac is
  the review station (verify + decode), not a second run
  chamber.
- Firecracker's own docs resume snapshots only on identical
  hardware/software with no network guarantee across clones; its
  disks stay user-managed while NVX scratch travels paired with
  state. Our restores keep `10.0.0.2` and gateway ping.

## Supply chain

- `verify-guest-determinism --guest ubuntu`: PASSED live — rebuilt
  the Ubuntu initramfs (36.6 MiB) and EROFS distro layer (56.7 MiB)
  twice from scratch; both attempts byte-identical
  ("Ubuntu initramfs and EROFS artifacts are deterministic").
- Release provenance files (`vmlinux.provenance.json`,
  `initramfs.provenance.json`) bind artifacts to source revisions.

## Reproduce

- NVX arm: driver scripts live in `/tmp/` on the measurement box
  (drafted from the Mac); simulants seeded into
  `~/demo/zoo/case-scratch.ext4` (`upper/`); collector
  `/tmp/zoo-collector.py` (ports 18080 report / 19090 attacker).
- Firecracker arm: `~/demo/firecracker/` (v1.17.0), rootfs
  `~/demo/zoo/fc-rootfs.ext4`, TAP `tap-zoo` + NAT, per-case driver
  `/tmp/zoo-fcone.sh`.
- Forensics: `/tmp/zoo-forensics.py`, `/tmp/zoo-forensics-restore.py`,
  snapshot at `~/demo/forensics/snap-infected`, Mac copy at
  `/tmp/forensics-mac/snap-infected`.
- Benchmarks: `~/demo/bench/` (`shell-snapshot-restore.log`,
  `cold-start.log`, `benchmark-metadata.json`).
