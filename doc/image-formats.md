# Image formats: OCI, upstream NVX and Nanvix

OCI is the import and distribution format for change #1. It lets NVX accept existing
Linux container images, including their command, environment, working directory and
layer deletions. Conversion happens during image preparation, before a VM starts.
OCI does not replace NVX's runtime filesystem or select its isolation mechanism.

| System | Import/distribution | Files used by the VM |
| --- | --- | --- |
| Upstream `microsoft/nvx` | Prepared guest artifacts and explicitly supplied sandbox layers; general image preparation remains proposed in its design | Linux kernel and compressed cpio initramfs; one to three compressed EROFS lower layers (`distro`, `runtime`, `custom`) plus preformatted ext4 scratch for OverlayFS |
| This NVX checkout | Docker/OCI Linux images, pulled for the host architecture and converted into a content-addressed cache | The same Linux, EROFS and ext4 layout; the manifest records layer roles, SHA-256 digests, EROFS UUIDs and image command defaults |
| Nanvix | Direct Nanvix-targeted ELF payloads, or OCI images consumed by its containerd shim | Its own `kernel.elf`, an application ELF/initrd (or a multibinary image), and an optional FAT32 ramfs image |

Upstream describes the EROFS/ext4 launch contract in
[its run guide](https://github.com/microsoft/nvx/blob/dev/doc/run.md) and the proposed
conversion boundary in
[its filesystem design](https://github.com/microsoft/nvx/blob/dev/doc/design/sandbox-filesystem-and-agent-architecture.md).
This checkout implements that import boundary in
[`image.py`](../scripts/nvx_tools/image.py) and launch preparation in
[`image_run.py`](../scripts/nvx_tools/image_run.py). The original explicit `--layer`
and `--scratch` path remains available.

The [OCI image specification](https://github.com/opencontainers/image-spec/blob/main/spec.md)
defines the manifest, configuration and filesystem layers. Here Docker performs the pull
and exports the image; the converter handles the filesystem semantics and emits EROFS
blobs and a prepared scratch template. Unknown bases flatten into a `custom` layer.
Registered bases share only an exact ordered digest prefix. EROFS UUIDs identify filesystems;
they are separate from content digests.

Nanvix also uses OCI for packaging. Its
[OCI image specification](https://github.com/nanvix/nanvix/blob/dev/doc/nanvix-oci-image-spec.md)
uses standard OCI filesystem layers with an `/initrd/` binary and optional `/ramfs/`
tree. The shim converts that tree to FAT32 with `mkramfs` and launches `nanvixd`.
The application must target Nanvix; its
[image-building guide](https://github.com/nanvix/nanvix/blob/dev/doc/docker-images.md)
explicitly distinguishes these binaries from standard Linux executables. The
[Windows run guide](https://github.com/nanvix/nanvix/blob/dev/doc/run-windows.md)
also documents bundling application and daemon ELFs with `mkimage`.

Therefore OCI is a useful common distribution envelope, but the payloads differ:
NVX imports ordinary Linux userspace; Nanvix packages software built for its own OS.
An OCI label such as `linux/amd64` on a Nanvix image does not make that payload a Linux
workload: Nanvix's specification uses `com.nanvix.*` annotations to identify the real
target. These images are not interchangeable.
