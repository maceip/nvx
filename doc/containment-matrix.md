# Generated containment evidence

Each table is rendered from a real accepted run. Source revisions are specific to
each platform; a passing row includes its opposite control.

## Apple Silicon macOS / HVF

NVX `7c7f7239c7abc046fd4ec352e4e794670b07dae9`; core `f1f6019b73a891b0cc9be381eba220750cfb9146`.

Containment SHA-256: `ceb6e73151d77c7d30f923ce8c0bfcfc479a1ab7914ccd26ae1844cc2a6466ea`.

| Probe family | Backend | Protected | Negative control | Scope |
| --- | --- | --- | --- | --- |
| identity | hvf | CONTAINED | UNCONTAINED | non-root, empty capabilities, NNP and seccomp syscall denial |
| filesystem | hvf | CONTAINED | UNCONTAINED | system writes denied; scratch writable; mount denied |
| process/namespace | hvf | CONTAINED | UNCONTAINED | agent invisible; creation of another network namespace denied |
| devices | hvf | CONTAINED | UNCONTAINED | CAP_MKNOD retained in ci; raw memory/block nodes denied; proc mask |
| resources | hvf | CONTAINED | UNCONTAINED | bounded fork simulant reaches pids limit without stopping host |
| network | hvf | CONTAINED | UNCONTAINED | collector connection denied; actual host observes control request |
| snapshot integrity | hvf | CONTAINED | UNCONTAINED | same-length RAM/state tamper rejected; foreign arch refused |
| tenant isolation | hvf | CONTAINED | UNCONTAINED | private scratch hides previous instance writes |

## ARM Linux / KVM on the owned nested VM

NVX `2131a1ea94b2e29ad56e5c193181feef06e708dd`; core `75b6560159c4ba903025ffd99dc3c95909002f49`.

Containment SHA-256: `d0d70ec204e07edc31f3d89dfbc3eeda4df9a30e73836a852a9079ce40f8d2e1`.

| Probe family | Backend | Protected | Negative control | Scope |
| --- | --- | --- | --- | --- |
| identity | kvm | CONTAINED | UNCONTAINED | non-root, empty capabilities, NNP and seccomp syscall denial |
| filesystem | kvm | CONTAINED | UNCONTAINED | system writes denied; scratch writable; mount denied |
| process/namespace | kvm | CONTAINED | UNCONTAINED | agent invisible; creation of another network namespace denied |
| devices | kvm | CONTAINED | UNCONTAINED | CAP_MKNOD retained in ci; raw memory/block nodes denied; proc mask |
| resources | kvm | CONTAINED | UNCONTAINED | bounded fork simulant reaches pids limit without stopping host |
| network | kvm | CONTAINED | UNCONTAINED | collector connection denied; actual host observes control request |
| snapshot integrity | kvm | CONTAINED | UNCONTAINED | same-length RAM/state tamper rejected; foreign arch refused |
| tenant isolation | kvm | CONTAINED | UNCONTAINED | private scratch hides previous instance writes |

## Native x86 Linux / KVM

NVX `de301089694241d606c46f5003f87edca6284686`; core `f1f6019b73a891b0cc9be381eba220750cfb9146`.

[Native run](https://github.com/maceip/nvx/actions/runs/37193112786/job/111409284539).

Containment SHA-256: `43fce254ecdd8b32867c4fe78ce108097ff80935474d610cb26a4e2de7a21775`.

| Probe family | Backend | Protected | Negative control | Scope |
| --- | --- | --- | --- | --- |
| identity | kvm | CONTAINED | UNCONTAINED | non-root, empty capabilities, NNP and seccomp syscall denial |
| filesystem | kvm | CONTAINED | UNCONTAINED | system writes denied; scratch writable; mount denied |
| process/namespace | kvm | CONTAINED | UNCONTAINED | agent invisible; creation of another network namespace denied |
| devices | kvm | CONTAINED | UNCONTAINED | CAP_MKNOD retained in ci; raw memory/block nodes denied; proc mask |
| resources | kvm | CONTAINED | UNCONTAINED | bounded fork simulant reaches pids limit without stopping host |
| network | kvm | CONTAINED | UNCONTAINED | collector connection denied; actual host observes control request |
| snapshot integrity | kvm | CONTAINED | UNCONTAINED | same-length RAM/state tamper rejected; foreign arch refused |
| tenant isolation | kvm | CONTAINED | UNCONTAINED | private scratch hides previous instance writes |

## Native Windows / WHP

NVX `de301089694241d606c46f5003f87edca6284686`; core `f1f6019b73a891b0cc9be381eba220750cfb9146`.

[Native run](https://github.com/maceip/nvx/actions/runs/37193112844).

Containment SHA-256: `de463d9ac0152fede5158295cc70b51b04653c792a9cf1d20ef53ea9836b11ff`.

| Probe family | Backend | Protected | Negative control | Scope |
| --- | --- | --- | --- | --- |
| identity | whp | CONTAINED | UNCONTAINED | non-root, empty capabilities, NNP and seccomp syscall denial |
| filesystem | whp | CONTAINED | UNCONTAINED | system writes denied; scratch writable; mount denied |
| process/namespace | whp | CONTAINED | UNCONTAINED | agent invisible; creation of another network namespace denied |
| devices | whp | CONTAINED | UNCONTAINED | CAP_MKNOD retained in ci; raw memory/block nodes denied; proc mask |
| resources | whp | CONTAINED | UNCONTAINED | bounded fork simulant reaches pids limit without stopping host |
| network | whp | CONTAINED | UNCONTAINED | collector connection denied; actual host observes control request |
| snapshot integrity | whp | CONTAINED | UNCONTAINED | same-length RAM/state tamper rejected; foreign arch refused |
| tenant isolation | whp | CONTAINED | UNCONTAINED | private scratch hides previous instance writes |

## Pending platforms

Intel macOS/HVF and Linux/MSHV still require accepted runtime results. Their
containment rows are not inferred from the other platforms.
