# aarch64 kernel patches (none)

The aarch64 NVX guest kernel builds from pristine upstream Linux plus
`../config-microvm-aarch64`. All patches in `../patches` are specific to
the x86 microVM profile (port 0xE9 consoles, PVH/E820 shared interrupt
status, LAPIC frequency) and do not apply to aarch64.
