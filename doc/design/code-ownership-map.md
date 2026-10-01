# Component responsibility map

[Design index](../design.md)

This map assigns each design area to the architectural component responsible
for it. It is a navigation aid, not an implementation index: the components
own their internal structure, and the other design chapters define the
contracts between them.

| Area | Responsible component |
| --- | --- |
| Machine-profile selection, ABI constants, machine validation, and command-line ownership | OpenVMM machine-profile definitions shared by every launcher and the VM worker |
| CLI options, host-attachment construction, restore preparation, capture orchestration, portb output drain, and outcome reports | OpenVMM command-line entry layer and its VM controller |
| Management-RPC microVM creation, capture, restore, readiness, and guest-exit reporting | OpenVMM management-RPC service |
| Worker composition, fixed virtio placement, snapshot boundaries, management exclusion, clock contracts, and restore sequencing | OpenVMM VM worker |
| Restore-time VP materialization and saved-VP inventory validation | OpenVMM partition unit |
| Restore-time RAM capacity, range selection, split backing, and private copy-on-write mapping | Snapshot machine contract, OpenVMM memory-layout engine, and guest-memory manager |
| Linux direct MP-table loading | OpenVMM Linux direct loader and MP-table builder |
| Base-chipset allowlist and memory-layout defaults | OpenVMM base-chipset manifest builder |
| portb, shutdown, and snapshot PMIO; RTC, PIT, and IOAPIC save and restore | OpenVMM chipset devices |
| Virtio transport, shared interrupt status, and device-private saved state | OpenVMM virtio transport and device models |
| Control-session protocol, authenticated broker, and local peer identity | OpenVMM virtio-console broker and serial socket and named-pipe backends |
| Portable networking, egress policy, and endpoint quiesce | OpenVMM Consomme endpoint, egress policy, and virtio-net device |
| HostFs profile, denied paths, and filesystem saved state | OpenVMM microVM virtio-fs profile |
| State-unit quiesce, start, rollback, inventory, and downtime advance | OpenVMM state-unit framework |
| Snapshot format, machine contract, publication, and artifact validation | OpenVMM snapshot helpers and platform file primitives |
| Backend CPU contracts and snapshot clocks | KVM, MSHV, and WHP backends |
| Sandbox launch and kernel features | NVX sandbox launcher and microVM kernel configuration |
| Workload namespace and root construction | NVX guest container launch and entry helpers |
| Guest workload, scratch quiesce, managed lifecycle, and post-restore CPU/RAM repair | NVX guest init agent and snapshot helper |
| OpenVMM control-plane and guest-artifact integration tests | OpenVMM microVM and management-RPC VMM tests |
| NVX Linux and device integration tests | NVX microVM process tests and guest test scripts |
| Copilot adversarial controller and typed broker | NVX adversarial controller and broker |
| Credential-free adversarial executor and independent oracles | NVX adversarial executor and oracle watchdog |
