# Generated containment evidence

Each table is rendered from a real accepted run. Source revisions are specific to
each platform; a passing row includes its opposite control.

## Apple Silicon macOS / HVF

NVX `423b8387fe1839184921c3e7fc76f00898b50676`; core `4b20f45f173bd4d971ec71a710fe9045c801da79`.

Containment SHA-256: `6ab709e961a9677e1edf482dbe042185552c50eabe3215bd92ad11b7ff9c306e`.

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

NVX `fd639237c481b03c514233c23934c9885260dd26`; core `4b20f45f173bd4d971ec71a710fe9045c801da79`.

[Native run](https://github.com/maceip/nvx/actions/runs/37186695364/job/111390011962).

Containment SHA-256: `b3bb024e7f45022cb64e96d37446a9c8a4cb727ec39a8c90dad2fe96ea9cd14b`.

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

NVX `19459df26f6d9184ee279bf84fedbb01e2044266`; core `75b6560159c4ba903025ffd99dc3c95909002f49`.

[Native run](https://github.com/maceip/nvx/actions/runs/37186596678).

Containment SHA-256: `4a95057e55d0dba23f9a013d9d4ed722ac63e7e624aad86b135ce6810285b0d5`.

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
