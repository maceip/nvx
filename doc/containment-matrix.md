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
