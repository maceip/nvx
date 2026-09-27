 NVX harness (scripts/nvx.py) and prebuilt artifacts only target:

Linux (using KVM via /dev/kvm or MSHV via /dev/mshv)

Windows (using the Windows Hypervisor Platform / WHP)

However, NVX is built on top of Microsoft OpenVMM, which already has backend support for macOS on Apple Silicon (aarch64 via Apple's native Hypervisor.framework). Making NVX function on macOS is architecturally feasible, but requires bridging several gaps across the orchestration layer, hypervisor backend, and guest kernel images.

What It Would Take to Make It Work
1. Compile OpenVMM for macOS (Apple Silicon, this laptop, in ../openvmm)
OpenVMM’s core Rust codebase supports compiling a standalone openvmm binary for aarch64-apple-darwin using Hypervisor.framework.


2. Provide ARM64 Guest Images (Kernel & Rootfs)
NVX boots a tailored, stripped-down Linux kernel and an Alpine-based minimal root filesystem.

The prebuilt binaries distributed in NVX releases target x86_64 host hypervisors.

For Apple Silicon, you must compile an ARM64 (aarch64) NVX kernel configuration along with an Alpine aarch64 initramfs/rootfs image containing the NVX guest utilities (/sbin/nvx-exit, etc.).

3. Update the scripts/nvx.py Runner
The Python orchestration script is hardcoded for specific OS/hypervisor pairs:

Add detection for sys.platform == "darwin".

Expose a new hypervisor backend flag (e.g. --hypervisor hvf or --hypervisor hypervisor-framework).

Map OpenVMM execution flags to match the command-line interface OpenVMM expects when running on macOS instead of WHP or KVM.

4. Guest Boot Directives and Device Tree (FDT)
On x86_64, OpenVMM can directly jump into 64-bit Linux boot protocol entry points or legacy BIOS/UEFI stubs. On ARM64 macOS via Hypervisor.framework:

OpenVMM must set up a flattened device tree (FDT) or minimal firmware protocol to tell the Linux guest kernel where memory, virtio MMIO/PCI registers, and UART serial consoles reside.

You need to confirm whether the pinned OpenVMM submodule revision in NVX contains working direct-boot logic for aarch64 under Hypervisor.framework, or if you must invoke OpenVMM directly using custom CLI arguments instead of relying on standard NVX script defaults.

