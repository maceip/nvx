# Generated containment evidence

Each table is rendered from a real accepted run. Source revisions are specific to
each platform; a passing row includes its opposite control.

## Apple Silicon macOS / HVF

NVX `65329b2db8bb1a1fa10013816834306500468c51`; core `d591aaee1c5c10a10d8df85a48e865ec13031dc2`.

Containment SHA-256: `762a6a4585457e68c1d6c71e7d3e97655a254e1bf489a22aaf7b05c576c4230d`.

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

NVX `65329b2db8bb1a1fa10013816834306500468c51`; core `d591aaee1c5c10a10d8df85a48e865ec13031dc2`.

Containment SHA-256: `904a601ed9ddc5061a9988ce3dfedd540f15898f153faae551b34ec81b75cf93`.

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

NVX `65329b2db8bb1a1fa10013816834306500468c51`; core `d591aaee1c5c10a10d8df85a48e865ec13031dc2`.

[Native run](https://github.com/maceip/nvx/actions/runs/37197876063/job/111423381901).

Containment SHA-256: `480199fce8b099da21cd4acf8a615633d677cf919a0fbb858eba6e208ea92154`.

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

NVX `65329b2db8bb1a1fa10013816834306500468c51`; core `d591aaee1c5c10a10d8df85a48e865ec13031dc2`.

[Native run](https://github.com/maceip/nvx/actions/runs/37197876018/job/111423514217).

Containment SHA-256: `173cbc992775002a2c7cfc5515307af4a0614ab09e03fcb456ac1794de53b283`.

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

Intel macOS / HVF and Linux / MSHV remain pending real runtime acceptance.
