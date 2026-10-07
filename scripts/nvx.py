#!/usr/bin/env python3
"""Build, run, benchmark, and package the OpenVMM/NVX distribution."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import platform
import select
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from nvx_tools import sandbox_lifecycle
from nvx_tools.adversarial import configure_parser as configure_adversarial_parser
from nvx_tools.benchmark import configure_parser as configure_benchmark_parser
from nvx_tools.build import (
    build_all,
    build_distro_layer,
    build_guest,
    build_initramfs,
    build_kernel,
    build_openvmm,
    materialize_kernel_provenance_inputs,
    record_openvmm_provenance,
    verify_guest_determinism,
)
from nvx_tools.build_config import (
    BuildConfig,
    DistroLayerBuildConfig,
    DockerBuildConfig,
    KernelBuildConfig,
    OpenVmmBuildConfig,
)
from nvx_tools.build_constants import (
    AlpineBuildConstants,
    BuildConstants,
    InitramfsBuildConstants,
    KernelBuildConstants,
    UbuntuBuildConstants,
)
from nvx_tools.ci import (
    OPENVMM_TEST_BACKENDS,
    REQUIRED_CI_RESULT_ENVIRONMENTS,
    required_ci_failures,
    run_openvmm_tests,
    run_openvmm_unit_tests,
    setup_cross_os_cache,
)
from nvx_tools.collect_alpine_sources import (
    configure_parser as configure_alpine_sources_parser,
)
from nvx_tools.collect_ubuntu_sources import (
    configure_parser as configure_ubuntu_sources_parser,
)
from nvx_tools.common import (
    ScriptError,
    artifact_path,
    openvmm_binary_path,
    require_file,
    sha256_file,
)
from nvx_tools.create_linux_source_archive import (
    configure_parser as configure_linux_source_archive_parser,
)
from nvx_tools.doctor import command_setup
from nvx_tools.doctor import configure_parser as configure_doctor_parser
from nvx_tools.events import EventLog
from nvx_tools.explain import configure_parser as configure_explain_parser
from nvx_tools.guests import GUEST_NAMES, guest_descriptor
from nvx_tools.hvf import boot_tokens as hvf_boot_tokens
from nvx_tools.hvf import network_arguments as hvf_network_arguments
from nvx_tools.microvm_tests import configure_parser as configure_microvm_test_parser
from nvx_tools.performance import configure_parser as configure_performance_parser
from nvx_tools.release import (
    collect_release_sources,
    create_release_archive,
    download_latest_release,
    package_release,
    verify_source_tree,
)
from nvx_tools.sandbox import (
    SandboxLaunch,
    SandboxLayer,
    SandboxMount,
    parse_workload_identity,
)
from nvx_tools.snapshot import format_report, verify_snapshot

DEFAULT_RELEASE_REPOSITORY = "maceip/nvx"
HYPERVISORS = ("auto", "whp", "kvm", "mshv", "hvf", "hypervisor-framework")
# Canonical name for each --hypervisor choice. hypervisor-framework is an
# alias for hvf (macOS Hypervisor.framework).
_HYPERVISOR_CANONICAL = {
    "auto": "auto",
    "whp": "whp",
    "kvm": "kvm",
    "mshv": "mshv",
    "hvf": "hvf",
    "hypervisor-framework": "hvf",
}
NETWORK_PROFILES = ("portable",)
SYSTEMD_ENTRYPOINTS = frozenset(("/usr/lib/systemd/systemd", "/lib/systemd/systemd"))


def _run(
    args: list[str | os.PathLike[str]], *, cwd: Path = BuildConstants.REPO_ROOT
) -> None:
    command = [os.fspath(arg) for arg in args]
    print(f">> {shlex.join(command)}")
    subprocess.run(command, cwd=cwd, check=True)


def _validate_sandbox_systemd_policy(launch: SandboxLaunch) -> None:
    if launch.entrypoint in SYSTEMD_ENTRYPOINTS:
        raise ScriptError(
            "systemd entrypoints are unsupported by the sandbox security profile"
        )
    distro = next(
        (layer for layer in launch.layers if layer.role == "distro"),
        None,
    )
    if distro is None:
        return
    manifest = distro.path.with_name(
        f"{distro.path.name}{BuildConstants.DISTRO_MANIFEST_SUFFIX}"
    )
    if not manifest.exists():
        return
    try:
        document: object = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ScriptError(f"invalid sandbox distro manifest: {error}") from error
    if not isinstance(document, dict):
        raise ScriptError("invalid sandbox distro manifest: expected a JSON object")
    manifest_document = cast(dict[str, object], document)
    if (
        manifest_document.get("format") != 1
        or manifest_document.get("artifact") != distro.path.name
        or manifest_document.get("artifact_sha256") != sha256_file(distro.path)
    ):
        raise ScriptError("sandbox distro manifest does not match its artifact")
    raw_packages = manifest_document.get("packages")
    if not isinstance(raw_packages, list):
        raise ScriptError("sandbox distro manifest has invalid package metadata")
    packages: list[dict[str, object]] = []
    for raw_package in cast(list[object], raw_packages):
        if not isinstance(raw_package, dict):
            raise ScriptError("sandbox distro manifest has invalid package metadata")
        package = cast(dict[str, object], raw_package)
        if not isinstance(package.get("name"), str):
            raise ScriptError("sandbox distro manifest has invalid package metadata")
        packages.append(package)
    if any(package["name"] == "systemd" for package in packages):
        raise ScriptError(
            "systemd images are unsupported by the sandbox security profile"
        )


def command_init(_: argparse.Namespace) -> None:
    _run(["git", "submodule", "update", "--init", "--recursive"])


def _openvmm_build_config(args: argparse.Namespace) -> OpenVmmBuildConfig:
    return OpenVmmBuildConfig(
        skip_restore=getattr(args, "skip_restore", False),
        backend=getattr(args, "backend", None),
    )


def _build_config(args: argparse.Namespace) -> BuildConfig:
    return BuildConfig(
        guest=getattr(args, "guest", InitramfsBuildConstants.DEFAULT_GUEST),
        native_guest=getattr(args, "native", False),
        openvmm=_openvmm_build_config(args),
    )


def command_build_guest(args: argparse.Namespace) -> None:
    build_guest(_build_config(args))


def command_build_kernel(_: argparse.Namespace) -> None:
    build_kernel(KernelBuildConfig())


def command_build_initramfs(args: argparse.Namespace) -> None:
    build_initramfs(BuildConfig.initramfs_config(args.guest))


def command_build_distro_layer(args: argparse.Namespace) -> None:
    build_distro_layer(
        DistroLayerBuildConfig(
            guest=args.guest,
            work=(
                BuildConstants.BUILD_DIR
                / InitramfsBuildConstants.DISTRO_WORK_DIRECTORY_TEMPLATE.format(
                    guest=args.guest
                )
            ),
            output=args.output,
            replace=args.replace,
        )
    )


def command_verify_guest_determinism(args: argparse.Namespace) -> None:
    if args.image:
        from nvx_tools.image import verify_image_determinism

        verify_image_determinism(tuple(args.image), args.work_dir)
    else:
        verify_guest_determinism(args.work_dir, args.guest)


def command_build_openvmm(args: argparse.Namespace) -> None:
    build_openvmm(_openvmm_build_config(args))


def command_record_openvmm_provenance(_: argparse.Namespace) -> None:
    record_openvmm_provenance(OpenVmmBuildConfig())


def command_materialize_kernel_provenance_inputs(_: argparse.Namespace) -> None:
    materialize_kernel_provenance_inputs()


def command_setup_cross_os_cache(_: argparse.Namespace) -> None:
    setup_cross_os_cache()


def command_check_required_ci(args: argparse.Namespace) -> None:
    results = {
        job: os.environ.get(environment, "")
        for job, environment in REQUIRED_CI_RESULT_ENVIRONMENTS.items()
    }
    failures = required_ci_failures(
        args.event_name,
        same_repository=args.same_repository == "true",
        self_hosted=args.self_hosted == "true",
        run_tests=args.run_tests == "true",
        run_workloads=args.run_workloads == "true",
        results=results,
    )
    if failures:
        for failure in failures:
            print(f"::error::{failure}")
        raise ScriptError(f"{len(failures)} required CI job result(s) did not match")


def command_test_openvmm(args: argparse.Namespace) -> None:
    run_openvmm_tests(args.backend)


def command_test_openvmm_unit(_: argparse.Namespace) -> None:
    run_openvmm_unit_tests()


def command_build(args: argparse.Namespace) -> None:
    build_all(_build_config(args))


def _canonical_hypervisor(selected: str) -> str:
    try:
        return _HYPERVISOR_CANONICAL[selected]
    except KeyError:
        raise ScriptError(
            f"unsupported hypervisor {selected!r}; choose {', '.join(HYPERVISORS)}"
        ) from None


def _hypervisor(selected: str) -> str:
    if selected != "auto":
        return _canonical_hypervisor(selected)
    if os.name == "nt":
        return "whp"
    if sys.platform == "darwin":
        return "hvf"
    return "kvm"


def _arm_direct(hypervisor: str) -> bool:
    machine = platform.machine()
    return machine.lower() in ("arm64", "aarch64") and hypervisor in ("hvf", "kvm")


def _require_apple_silicon(hypervisor: str) -> None:
    if hypervisor != "hvf":
        return
    machine = platform.machine()
    if sys.platform != "darwin" or machine.lower() not in (
        "arm64",
        "aarch64",
        "x86_64",
    ):
        raise ScriptError("the hvf hypervisor requires arm64 or Intel macOS")


def _release_platform(hypervisor: str) -> str:
    selected = _hypervisor(hypervisor)
    if sys.platform == "win32":
        host = "windows"
        release_platform = "windows-whp"
        supported = ("whp",)
    elif sys.platform.startswith("linux"):
        host = "linux"
        release_platform = f"linux-{selected}"
        supported = ("kvm", "mshv")
    elif sys.platform == "darwin":
        host = "macos"
        release_platform = "darwin-x86_64"
        supported = ("hvf",)
    else:
        raise ScriptError(f"release downloads are unsupported on {sys.platform}")
    if selected not in supported:
        raise ScriptError(f"{selected} is not supported on {host}")
    if _arm_direct(selected):
        return "darwin-arm64" if platform.system() == "Darwin" else "linux-arm64"
    return release_platform


def command_download(args: argparse.Namespace) -> None:
    download_latest_release(
        args.repository,
        _release_platform(args.hypervisor),
        allow_unsigned=args.allow_unsigned,
    )


def _format_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


def parse_share_spec(value: str) -> tuple[int, str, str]:
    """Parse `--share PORT:MNTPOINT[:ro|rw]` into (port, mountpoint, mode)."""
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ScriptError(f"--share must be PORT:MNTPOINT[:ro|rw], got {value!r}")
    try:
        port = int(parts[0])
    except ValueError:
        raise ScriptError(
            f"--share port must be an integer, got {parts[0]!r}"
        ) from None
    if not 1 <= port <= 65535:
        raise ScriptError(f"--share port must be 1-65535, got {port}")
    mountpoint = parts[1]
    if not mountpoint.startswith("/"):
        raise ScriptError(f"--share mountpoint must be absolute, got {mountpoint!r}")
    mode = parts[2] if len(parts) == 3 else "ro"
    if mode not in ("ro", "rw"):
        raise ScriptError(f"--share mode must be ro or rw, got {mode!r}")
    return port, mountpoint, mode


DEFAULT_DISK_NAME = "nvx-disk.raw"
DEFAULT_DISK_MIB = 4096
REPL_SAVE_OK = b"snapshot saved"
REPL_SAVE_FAILED = b"error: save-snapshot failed"


def parse_serve_spec(value: str) -> tuple[Path, str, str]:
    """Parse `--serve HOSTDIR:MNTPOINT[:ro|rw]` into (hostdir, mountpoint, mode)."""
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ScriptError(f"--serve must be HOSTDIR:MNTPOINT[:ro|rw], got {value!r}")
    hostdir = Path(parts[0])
    if not hostdir.is_dir():
        raise ScriptError(f"--serve host directory is missing: {parts[0]!r}")
    mountpoint = parts[1]
    if not mountpoint.startswith("/"):
        raise ScriptError(f"--serve mountpoint must be absolute, got {mountpoint!r}")
    mode = parts[2] if len(parts) == 3 else "ro"
    if mode not in ("ro", "rw"):
        raise ScriptError(f"--serve mode must be ro or rw, got {mode!r}")
    return hostdir, mountpoint, mode


def _start_serve_servers(
    specs: list[str],
) -> list[tuple[object, int, str, str]]:
    """Serve each `--serve` tree in-process on an ephemeral loopback port.

    Returns (server, port, mountpoint, mode) tuples; the caller shuts the
    servers down after the run. Reuses the 9P server from nvx_9p.py instead
    of requiring a hand-started process.
    """
    from nvx_tools.nvx_9p import Server, Share

    started: list[tuple[object, int, str, str]] = []
    for spec in specs:
        hostdir, mountpoint, mode = parse_serve_spec(spec)
        server = Server(
            Share(str(hostdir), read_write=(mode == "rw")), ("127.0.0.1", 0)
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(
            f"NVX-9P-SERVE-OK: 127.0.0.1:{port} root={hostdir} mode={mode}",
            flush=True,
        )
        started.append((server, port, mountpoint, mode))
    return started


def _stop_serve_servers(servers: list[tuple[object, int, str, str]]) -> None:
    for server, _port, _mountpoint, _mode in servers:
        try:
            server.shutdown()  # type: ignore[attr-defined]
            server.server_close()  # type: ignore[attr-defined]
        except OSError:
            pass


def _ensure_disk_image(path: Path, size_mib: int = DEFAULT_DISK_MIB) -> Path:
    """Return a raw disk image at path, creating a sparse one if missing."""
    if path.exists():
        if not path.is_file():
            raise ScriptError(f"--disk path is not a file: {path}")
        return path
    size = size_mib * 1024 * 1024
    try:
        with open(path, "wb") as handle:
            handle.truncate(size)
    except OSError as error:
        raise ScriptError(f"--disk could not create {path}: {error}") from error
    print(f">> created sparse {size_mib} MiB raw disk at {path}", flush=True)
    return path


def _forward_stdin(proc: subprocess.Popen[bytes], stdin_lock: threading.Lock) -> None:
    """Forward our stdin to the child (openvmm REPL); hold the pipe open.

    The REPL lives on openvmm's stdin (`snap`, `i <text>`, `shutdown`),
    so an interactive terminal keeps working and piped input reaches the
    guest/REPL. The pipe is never closed early: an EOF on our side must
    not look like a REPL hangup to the child. Writes take stdin_lock so
    scripted REPL commands never interleave with forwarded bytes.
    """
    assert proc.stdin is not None
    try:
        stdin = sys.stdin.buffer
    except AttributeError:
        return
    while True:
        try:
            chunk = stdin.read(65536)
        except (OSError, ValueError):
            return
        if not chunk:
            return
        try:
            with stdin_lock:
                proc.stdin.write(chunk)
                proc.stdin.flush()
        except (OSError, ValueError):
            return


REPL_ESCAPE = b"\x11"  # Ctrl-Q: guest-forward mode -> `openvmm>` prompt
REPL_PROBE = b"nvx-repl-probe"
REPL_PROBE_HIT = b"unrecognized subcommand"


def _repl_write(
    proc: subprocess.Popen[bytes], stdin_lock: threading.Lock, data: bytes
) -> None:
    assert proc.stdin is not None
    with stdin_lock:
        proc.stdin.write(data)
        proc.stdin.flush()


def _repl_enter_sync(
    proc: subprocess.Popen[bytes],
    buf: bytearray,
    stdin_lock: threading.Lock,
    timeout: float,
) -> None:
    """Switch openvmm's stdin from guest-forward mode to the REPL prompt.

    The escape byte and the following line must arrive in separate reads:
    bytes after Ctrl-Q in the same 32-byte read are consumed as guest
    input and dropped. Sync is a probe line: the REPL parser answers
    unknown input with `unrecognized subcommand` on the output, while a
    probe that lands pre-escape just annoys the guest shell.
    """
    _repl_write(proc, stdin_lock, REPL_ESCAPE)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _repl_write(proc, stdin_lock, REPL_PROBE + b"\n")
        try:
            _wait_for_marker_sync(proc, buf, REPL_PROBE_HIT, 3, "repl probe")
            return
        except ScriptError as err:
            if proc.poll() is not None:
                raise ScriptError("VM exited while entering the REPL") from err
    raise ScriptError("timed out entering the openvmm REPL")


def _drain_available(proc: subprocess.Popen[bytes], buf: bytearray) -> None:
    """Move every currently-readable byte from the child into buf+stdout."""
    assert proc.stdout is not None
    while True:
        ready, _, _ = select.select([proc.stdout], [], [], 0)
        if not ready:
            return
        chunk = os.read(proc.stdout.fileno(), 65536)
        if not chunk:
            return
        buf.extend(chunk)
        del buf[: max(0, len(buf) - 8 * 1024 * 1024)]
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()


def _wait_for_marker_sync(
    proc: subprocess.Popen[bytes],
    buf: bytearray,
    marker: bytes,
    timeout: float,
    what: str,
) -> None:
    """Single-threaded marker wait: drain-then-scan, no pump thread.

    The first version tailed output with a pump thread doing blocking
    reads into a lock-shared buffer while the main thread polled for the
    marker. A live HVF run never matched although scripted-producer unit
    tests passed, which looked like the pump deadlocking. Rerunning that
    exact pump against the live VM exonerated it: over 300 s the pump
    stayed alive while the shared buffer and the log file both stayed at
    0 bytes and the openvmm child kept burning CPU -- zero bytes were
    emitted, so there was nothing to pump. An identical launch minutes
    earlier printed the full 19 KiB boot log with the marker. The failure
    is an intermittent silent HVF boot, not a tailing deadlock (repro and
    driver log kept at /tmp/nvxdrive/oldpump_repro.py). The
    single-threaded drain stays: one reader keeps the timeout's
    buffered= count exactly what was scanned, so the next silent boot is
    diagnosable from the error alone.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _drain_available(proc, buf)
        if marker in buf:
            return
        if proc.poll() is not None:
            _drain_available(proc, buf)
            if marker in buf:
                return
            raise ScriptError(f"VM exited before {what}")
        time.sleep(0.2)
    _drain_available(proc, buf)
    raise ScriptError(
        f"timed out after {timeout:g}s waiting for {what} "
        f"(child alive={proc.poll() is None}, buffered={len(buf)})"
    )


def _run_repl_driven(command: list[str], args: argparse.Namespace) -> int:
    """Boot the VM and drive the openvmm REPL on its stdin to save a snapshot.

    Waits for `--save-on` on the combined output, escapes to the REPL with
    Ctrl-Q, sends `snap <dir>`, waits for the upstream "snapshot saved"
    marker, then sends `shutdown` (the REPL blocks resume after a save to
    protect the snapshot). Output is drained single-threaded via select,
    so the marker scan and the timeout's buffered= count always agree.
    Returns the process exit code.
    """
    save_dir = Path(args.save_snapshot).resolve()
    # Upstream `snap` creates the leaf and refuses an existing one, so only
    # ensure the parent exists here.
    save_dir.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.stdout is not None and proc.stdin is not None
    buf = bytearray()
    stdin_lock = threading.Lock()
    forward = threading.Thread(
        target=_forward_stdin, args=(proc, stdin_lock), daemon=True
    )
    forward.start()
    try:
        _wait_for_marker_sync(
            proc,
            buf,
            args.save_on.encode(),
            args.save_timeout,
            f"save marker {args.save_on!r}",
        )
        if args.save_exec is not None:
            # Still in guest-forward mode: the line runs in the guest shell.
            _repl_write(proc, stdin_lock, (args.save_exec + "\n").encode())
            _wait_for_marker_sync(
                proc,
                buf,
                args.save_ready.encode(),
                args.save_timeout,
                f"save-ready marker {args.save_ready!r}",
            )
        _repl_enter_sync(proc, buf, stdin_lock, min(args.save_timeout, 60))
        _repl_write(proc, stdin_lock, f"snap {save_dir}\n".encode())
        try:
            _wait_for_marker_sync(
                proc,
                buf,
                REPL_SAVE_OK,
                args.save_timeout,
                "snapshot-saved marker",
            )
        except ScriptError as err:
            failed = REPL_SAVE_FAILED in buf
            if failed:
                raise ScriptError(
                    "openvmm REPL reported save-snapshot failed "
                    "(fresh hvf boots need --memory-backing-file; "
                    "the NIC needs the `snapshot` option, added "
                    "automatically with --save-snapshot on hvf)"
                ) from err
            raise
        print(f">> snapshot saved to {save_dir}", flush=True)
        # Snapshot capture leaves the guest paused. Quit the VMM directly;
        # an ACPI shutdown request cannot be serviced by a paused guest.
        _repl_write(proc, stdin_lock, b"quit\n")
        try:
            return proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
            return proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass


def _append_consomme_option(spec: str, option: str) -> str:
    """Append an option to a consomme endpoint spec.

    Options after the backend name are colon-introduced and comma-separated,
    so a bare `consomme` spec takes `:option` while `consomme:...` takes
    `,option`.
    """
    return spec + ("," if ":" in spec else ":") + option


def _consomme_spec_cidr(spec: str) -> str | None:
    """Return the bare CIDR option of a consomme endpoint spec, if any."""
    rest = spec[len("consomme") :]
    if rest.startswith(":"):
        rest = rest[1:]
    for option in rest.split(","):
        if (
            "/" in option
            and "=" not in option
            and option not in ("gwloopback", "snapshot")
        ):
            return option
    return None


def _hvf_consomme_policy_fragments(args: argparse.Namespace) -> list[str]:
    """Translate nvx network-policy flags to consomme endpoint options."""
    fragments: list[str] = []
    if args.network_egress is not None:
        fragments.append(f"egress={args.network_egress}")
    if args.network_ingress is not None:
        if args.network_ingress == "allow":
            raise ScriptError(
                "--network-ingress allow is unsupported with --hypervisor hvf; "
                "inbound traffic is denied except for hostfwd forwards"
            )
        fragments.append("ingress=deny")
    fragments.extend(f"egress-allow={rule}" for rule in args.network_egress_allow)
    fragments.extend(f"egress-deny={rule}" for rule in args.network_egress_deny)
    return fragments


def _hvf_policy_static_ip(cidr: str) -> tuple[str, str, str]:
    """Guest IP (network+2), netmask, and gateway (network+1) for a CIDR.

    Mirrors the deterministic identity the VMM binds the policy to, so the
    guest must configure this exact static address (no DHCP under policy).
    """
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        raise ScriptError(
            f"invalid consomme CIDR {cidr!r} for hvf egress policy"
        ) from None
    if not 1 <= network.prefixlen <= 30:
        raise ScriptError(
            f"consomme CIDR {cidr!r} cannot host a policy identity "
            "(need a prefix of 1-30)"
        )
    gateway = network.network_address + 1
    guest = network.network_address + 2
    if int(guest) >= int(network.broadcast_address):
        raise ScriptError(f"consomme CIDR {cidr!r} leaves no usable guest address")
    return str(guest), str(network.netmask), str(gateway)


def _apply_hvf_consomme_policy(
    specs: list[str], fragments: list[str]
) -> tuple[str, str, str]:
    """Append policy options to every consomme spec in place.

    Returns the static guest identity derived from the first consomme CIDR.
    """
    indexes = [index for index, spec in enumerate(specs) if spec.startswith("consomme")]
    if not indexes:
        raise ScriptError(
            "--network-egress/--network-ingress policy on hvf requires "
            "--virtio-net with a consomme backend"
        )
    for index in indexes:
        for fragment in fragments:
            specs[index] = _append_consomme_option(specs[index], fragment)
    for index in indexes:
        cidr = _consomme_spec_cidr(specs[index])
        if cidr is not None:
            return _hvf_policy_static_ip(cidr)
    raise ScriptError(
        "hvf egress policy requires a CIDR on the consomme endpoint "
        "(e.g. --virtio-net consomme:192.168.127.0/24)"
    )


def _require_hvf_run_args(args: argparse.Namespace) -> None:
    # The microVM machine profile is x86-only (MP-table boot, fixed x86 APIC
    # topology, KVM/MSHV/WHP hypervisors). On macOS/HVF, run uses standard
    # Linux direct boot: OpenVMM synthesizes the boot tables (default ACPI
    # mode with stub DT + EFI on aarch64) from --kernel/--initrd/--cmdline.
    # MicroVM-only options are rejected with an actionable message.
    # Network-policy knobs (--network-egress/ingress and the allow/deny rule
    # lists) are enforced on hvf through the standard-profile consomme
    # endpoint, so they are accepted here and translated onto --virtio-net.
    rejected = (
        ("mount", args.mount),
        ("mount-deny", args.mount_deny or None),
        ("net", args.net),
        ("network-profile", args.network_profile),
        ("host-loopback", args.host_loopback),
        ("network-proxy", args.network_proxy),
        ("host-loopback-forward", args.host_loopback_forward or None),
        ("outcome-report", args.outcome_report),
        ("memory-capacity-mib", args.memory_capacity_mib),
    )
    for name, value in rejected:
        if value is not None and value is not False and value != []:
            raise ScriptError(
                f"--{name} requires the microVM machine profile, which is "
                "x86-only and unsupported with --hypervisor hvf; "
                "hvf uses standard Linux direct boot (--kernel/--initrd/--cmdline)"
            )
    # Snapshot restore itself works on hvf, but these restore companions are
    # not verified there yet.
    unverified = (
        ("restore-processors", args.restore_processors),
        ("restore-memory-mib", args.restore_memory_mib),
    )
    for name, value in unverified:
        if value is not None and value is not False and value != []:
            raise ScriptError(
                f"--{name} is not verified with --hypervisor hvf; "
                "restore with --restore-snapshot (and --restore-ready-path) only"
            )


def _extend_network_arguments(command: list[str], args: argparse.Namespace) -> None:
    if args.net is not None:
        command.extend(["--net", args.net, "--network-profile", args.network_profile])
    if args.network_egress is not None:
        command.extend(["--network-egress", args.network_egress])
    if args.network_ingress is not None:
        command.extend(["--network-ingress", args.network_ingress])
    for rule in args.network_egress_allow:
        command.extend(["--network-egress-allow", rule])
    for rule in args.network_egress_deny:
        command.extend(["--network-egress-deny", rule])
    if args.host_loopback is not None:
        command.extend(["--host-loopback", args.host_loopback])
    if args.network_proxy is not None:
        command.extend(["--network-proxy", args.network_proxy])
    for forward in args.host_loopback_forward:
        command.extend(["--host-loopback-forward", forward])


def command_run(args: argparse.Namespace) -> None:
    if getattr(args, "instance_id_file", None) is not None and (
        args.image is None or args.pool is not None
    ):
        raise ScriptError("--instance-id-file requires a fresh --image run")
    arm = _arm_direct(_hypervisor(args.hypervisor))
    if args.pool is not None:
        from nvx_tools.pool import command_run as command_pool_run

        command_pool_run(args)
        return
    if args.image is not None:
        from nvx_tools.image_run import command_image_run

        command_image_run(args, _hypervisor(args.hypervisor))
        return
    if args.receipt is not None or args.receipt_signing_key is not None:
        raise ScriptError("--receipt and --receipt-signing-key require --image")
    if (
        args.workspace
        or args.out
        or args.keep_alive
        or args.secret
        or args.env
        or args.proxy_log
        or args.proxy_ca
        or args.secret_header
    ):
        raise ScriptError(
            "workspace, outputs, retained instances and credentials require --image"
        )
    if (args.net is None) != (args.network_profile is None):
        raise ScriptError("--net and --network-profile must be specified together")
    if args.restore_ready_path is not None and args.restore_snapshot is None:
        raise ScriptError("--restore-ready-path requires --restore-snapshot")
    if args.restore_processors is not None and args.restore_snapshot is None:
        raise ScriptError("--restore-processors requires --restore-snapshot")
    if args.restore_memory_mib is not None and args.restore_snapshot is None:
        raise ScriptError("--restore-memory-mib requires --restore-snapshot")
    if args.memory_capacity_mib is not None and args.restore_snapshot is not None:
        raise ScriptError("--memory-capacity-mib is only valid for a fresh boot")
    if args.memory_backing_file is not None and args.restore_snapshot is not None:
        raise ScriptError("--memory-backing-file is only valid for a fresh boot")
    if args.kernel is not None and args.restore_snapshot is not None:
        raise ScriptError("--kernel is only valid for a fresh boot")
    if args.initrd is not None and args.restore_snapshot is not None:
        raise ScriptError("--initrd is only valid for a fresh boot")
    if args.disk is not None and not arm:
        raise ScriptError(
            "--disk is only valid with --hypervisor hvf; "
            "the microVM machine profile has no virtio-blk controller"
        )
    if args.save_snapshot is not None and args.save_on is None:
        raise ScriptError("--save-snapshot requires --save-on MARKER")
    if (args.save_exec is None) != (args.save_ready is None):
        raise ScriptError("--save-exec and --save-ready must be used together")
    if args.save_exec is not None and args.save_snapshot is None:
        raise ScriptError("--save-exec/--save-ready require --save-snapshot")
    if args.save_snapshot is not None:
        if args.restore_snapshot is None and arm:
            if args.memory_backing_file is None:
                raise ScriptError(
                    "--save-snapshot on a fresh hvf boot requires "
                    "--memory-backing-file (the REPL cannot save otherwise)"
                )
    descriptor = guest_descriptor(args.guest)
    if args.restore_snapshot is not None and descriptor.name != "alpine":
        raise ScriptError(
            "--guest is not accepted for restore; the snapshot already fixes the guest"
        )
    memory_mib = (
        descriptor.default_memory_mib if args.memory_mib is None else args.memory_mib
    )
    if args.memory_capacity_mib is not None and args.memory_capacity_mib < memory_mib:
        raise ScriptError("--memory-capacity-mib cannot be below --memory-mib")
    if args.restore_processors is not None:
        if args.restore_processors > args.processors:
            raise ScriptError(
                "--restore-processors cannot exceed --processors capacity"
            )
    hypervisor = _hypervisor(args.hypervisor)
    if args.memory_backing_file is not None and not arm:
        raise ScriptError(
            "--memory-backing-file is only valid with --hypervisor hvf; "
            "snapshot save on the microVM profile uses its own memory file scheme"
        )
    _require_apple_silicon(hypervisor)
    if arm and args.memory_mib is None:
        # The aarch64 debug kernel and node-bearing initramfs do not fit
        # below this (verified: 256M fails once nodejs is installed, 512M
        # boots and serves).
        memory_mib = max(memory_mib, 2048)
    executable = require_file(openvmm_binary_path(), "OpenVMM release binary")
    if arm:
        _require_hvf_run_args(args)
        command = [
            str(executable),
            "--single-process",
            "--processors",
            str(args.processors),
            "--hypervisor",
            hypervisor,
            # PL011 ttyAMA0 on aarch64; OpenVMM adds console= automatically.
            "--com1",
            "console",
        ]
    else:
        command = [
            str(executable),
            "--single-process",
            "--machine",
            args.machine,
            "--processors",
            str(args.processors),
            "--hypervisor",
            hypervisor,
        ]
    if args.restore_snapshot is not None:
        from nvx_tools.doctor import canonical_arch

        verify_snapshot(
            args.restore_snapshot.resolve(),
            expected_arch=canonical_arch(platform.machine()),
        )
        command.extend(["--restore-snapshot", str(args.restore_snapshot.resolve())])
        if not arm:
            # x86-only: the aarch64/HVF CLI has no entropy-restore flag.
            command.append("--restore-entropy")
        if args.restore_processors is not None:
            command.extend(["--restore-processors", str(args.restore_processors)])
        if args.restore_memory_mib is not None:
            command.extend(["--restore-memory", f"{args.restore_memory_mib}M"])
        if args.restore_ready_path is not None:
            command.extend(["--restore-ready-path", str(args.restore_ready_path)])
    else:
        kernel_name = (
            KernelBuildConstants.BINARY_NAME_AARCH64
            if arm
            else KernelBuildConstants.BINARY_NAME
        )
        kernel = (
            require_file(Path(args.kernel), "custom Linux direct kernel")
            if args.kernel is not None
            else require_file(artifact_path(kernel_name), "Linux direct kernel")
        )
        initrd = (
            require_file(Path(args.initrd), "custom initramfs")
            if args.initrd is not None
            else require_file(
                artifact_path(descriptor.initramfs_name),
                f"{descriptor.distribution} initramfs",
            )
        )
        command.extend(
            [
                "--memory",
                f"{memory_mib}M",
                "--kernel",
                str(kernel),
                "--initrd",
                str(initrd),
            ]
        )
        if arm and args.memory_backing_file is not None:
            # File-backed RAM: enables `snap <dir>` from the openvmm REPL.
            # The NIC also needs the `snapshot` option to be restorable.
            command.extend(["--memory-backing-file", str(args.memory_backing_file)])
        if args.memory_capacity_mib is not None:
            command.extend(["--memory-capacity", f"{args.memory_capacity_mib}M"])
    if args.mount is not None:
        if args.mount.count(",") not in (1, 2):
            raise ScriptError("--mount must be GUEST_TARGET,HOST_PATH[,ro|rw]")
        command.extend(["--mount", args.mount])
    for denied_path in args.mount_deny:
        command.extend(["--mount-deny", str(denied_path)])
    if args.net is not None:
        command.extend(["--net", args.net, "--network-profile", args.network_profile])
    if not arm:
        # MicroVM-namespaced policy flags; on hvf the policy knobs below are
        # translated onto the consomme endpoint instead.
        if args.network_egress is not None:
            command.extend(["--network-egress", args.network_egress])
        if args.network_ingress is not None:
            command.extend(["--network-ingress", args.network_ingress])
        for rule in args.network_egress_allow:
            command.extend(["--network-egress-allow", rule])
        for rule in args.network_egress_deny:
            command.extend(["--network-egress-deny", rule])
        if args.host_loopback is not None:
            command.extend(["--host-loopback", args.host_loopback])
        if args.network_proxy is not None:
            command.extend(["--network-proxy", args.network_proxy])
        for forward in args.host_loopback_forward:
            command.extend(["--host-loopback-forward", forward])
    virtio_specs = list(args.virtio_net)
    hvf_static_ip = None
    # Self-serve 9P servers listen on ephemeral loopback ports reached via
    # the gateway mapping; start them before policy translation so a deny
    # policy can punch exactly those guest->gateway holes below.
    serves = _start_serve_servers(args.serve) if args.serve else []
    if arm:
        policy_fragments = _hvf_consomme_policy_fragments(args)
        if serves and args.network_egress == "deny":
            cidr = next(
                (
                    cidr
                    for spec in virtio_specs
                    if spec.startswith("consomme")
                    for cidr in [_consomme_spec_cidr(spec)]
                    if cidr is not None
                ),
                None,
            )
            if cidr is None:
                _stop_serve_servers(serves)
                raise ScriptError(
                    "--serve with --network-egress deny needs a CIDR on the "
                    "consomme endpoint so the gateway allow-rules can be "
                    "derived (e.g. --virtio-net consomme:192.168.127.0/24)"
                )
            try:
                gateway = str(next(ipaddress.ip_network(cidr, strict=False).hosts()))
            except ValueError:
                _stop_serve_servers(serves)
                raise ScriptError(
                    f"invalid consomme CIDR {cidr!r} for --serve gateway allow"
                ) from None
            for _server, port, _mountpoint, _mode in serves:
                policy_fragments.append(f"egress-allow={gateway}:tcp:{port}")
        if policy_fragments:
            hvf_static_ip = _apply_hvf_consomme_policy(virtio_specs, policy_fragments)
    if args.share or args.serve:
        consomme = [spec for spec in virtio_specs if spec.startswith("consomme")]
        if not consomme:
            raise ScriptError(
                "--share/--serve requires --virtio-net with a consomme backend "
                "so the guest can reach the host 9P server"
            )
        # The guest reaches the host server through the gateway address, so
        # the gateway-to-loopback mapping must be on.
        virtio_specs = [
            spec
            if not spec.startswith("consomme") or "gwloopback" in spec
            else _append_consomme_option(spec, "gwloopback")
            for spec in virtio_specs
        ]
    if args.save_snapshot is not None and arm:
        # Snapshot save requires a save-capable NIC: the upstream `snapshot`
        # option derives the same network+2/network+1 identity the policy
        # path configures, so the two compose.
        if not any(spec.startswith("consomme") for spec in virtio_specs):
            raise ScriptError(
                "--save-snapshot on hvf requires --virtio-net with a "
                "consomme backend carrying a CIDR "
                "(e.g. consomme:192.168.127.0/24)"
            )
        virtio_specs = [
            spec
            if not spec.startswith("consomme")
            or ",snapshot" in spec
            or spec.endswith(":snapshot")
            else _append_consomme_option(spec, "snapshot")
            for spec in virtio_specs
        ]
    for spec in virtio_specs:
        command.extend(["--virtio-net", spec])
    if arm and args.virtio_net and args.restore_snapshot is None:
        # On macOS/HVF the guest configures the virtio NIC via DHCP served
        # by the backend (e.g. consomme). Fresh boot only: the cmdline is
        # baked into the snapshot and must not be re-supplied on restore.
        # With an egress policy the guest instead uses the static identity
        # the policy binds to, so DHCP must stay off.
        if hvf_static_ip is not None:
            guest_ip, netmask, gateway = hvf_static_ip
            command.extend(
                [
                    "--cmdline",
                    f"virtnet_ip={guest_ip} "
                    f"virtnet_mask={netmask} "
                    f"virtnet_gw={gateway}",
                ]
            )
        else:
            command.extend(["--cmdline", "virtnet_dhcp=1"])
    for spec in args.share:
        port, mountpoint, mode = parse_share_spec(spec)
        command.extend(["--cmdline", f"virt9p={port}:{mountpoint}:{mode}"])
    for _server, port, mountpoint, mode in serves:
        command.extend(["--cmdline", f"virt9p={port}:{mountpoint}:{mode}"])
    if args.disk is not None:
        disk_path = (
            Path(args.disk) if args.dry_run else _ensure_disk_image(Path(args.disk))
        )
        command.extend(["--virtio-blk", f"file:{disk_path}"])
        if args.restore_snapshot is None:
            # Fresh boot only: on restore the cmdline (including virtdisk)
            # is baked into the snapshot, but the device itself must still
            # be present for the saved inventory to match.
            command.extend(["--cmdline", f"virtdisk=vda:{args.disk_mount}"])
    if args.outcome_report is not None:
        command.extend(["--microvm-report", str(args.outcome_report)])
    if args.cmdline:
        command.extend(["--cmdline", args.cmdline])
    if args.restore_snapshot is None:
        command.extend(["--cmdline", f"nvx_host_epoch={int(time.time())}"])
    print(f">> {_format_command(command)}")
    try:
        if args.dry_run:
            return
        if args.save_snapshot is not None:
            raise SystemExit(_run_repl_driven(command, args))
        log = EventLog(args.events, args.events.parent.name) if args.events else None
        if log:
            log.emit("run.started", backend=hypervisor, memory_mib=memory_mib)
        status = subprocess.run(command).returncode
        if log:
            log.emit("run.exited", code=status)
        raise SystemExit(status)
    finally:
        _stop_serve_servers(serves)


def command_sandbox(args: argparse.Namespace) -> None:
    if args.image is not None:
        if args.sandbox_operation != "run" or args.layer or args.scratch:
            raise ScriptError(
                "--image requires sandbox run and replaces --layer/--scratch"
            )
        from nvx_tools.image_run import command_image_run

        command_image_run(args, _hypervisor(args.hypervisor), sandbox=True)
        return
    operation = args.sandbox_operation
    if (
        args.out
        or args.keep_alive
        or args.secret
        or args.env
        or args.proxy_log
        or args.proxy_ca
        or args.secret_header
    ):
        raise ScriptError("outputs, retained instances and credentials require --image")
    if args.workspace:
        if args.mount:
            raise ScriptError("use one of --workspace and --mount")
        from nvx_tools.workspace import parse_workspace

        args.mount = parse_workspace(args.workspace).openvmm_arguments()[1]
    if args.memory_mib is None:
        args.memory_mib = 1024 if _arm_direct(_hypervisor(args.hypervisor)) else 256
    if _hypervisor(args.hypervisor) == "hvf":
        _require_apple_silicon("hvf")
    if operation in ("run", "provision", "exec") and (
        args.entrypoint in SYSTEMD_ENTRYPOINTS
    ):
        raise ScriptError(
            "systemd entrypoints are unsupported by the sandbox security profile"
        )
    if args.outcome_report is not None and operation not in ("run", "exec"):
        raise ScriptError(
            "--outcome-report is only valid for one-shot run or managed exec"
        )
    if args.mount_deny and args.mount is None:
        raise ScriptError("--mount-deny requires --mount")
    if args.mount is not None and operation not in ("run", "provision"):
        raise ScriptError("--mount is only valid for sandbox run or provision")
    wall_timeout_ms = 60000
    if operation in ("run", "provision"):
        from nvx_tools.policy import for_launch

        policy = for_launch(args)
        wall_timeout_ms = policy.wall_timeout_ms
        args.network_egress = policy.egress
        args.network_ingress = args.network_ingress or "deny"
        args.network_egress_allow = list(policy.allow)
        if (args.net is None) != (args.network_profile is None):
            raise ScriptError("--net and --network-profile must be specified together")
        if args.net is None and not _arm_direct(_hypervisor(args.hypervisor)):
            args.net = "192.168.127.2/24"
            args.network_profile = "portable"
        if not args.layer or args.scratch is None:
            raise ScriptError(f"sandbox {operation} requires --layer and --scratch")
        launch = SandboxLaunch(
            layers=tuple(args.layer),
            scratch=args.scratch,
            entrypoint=args.entrypoint,
            args=tuple(args.sandbox_arg),
            hostname=args.hostname,
            workload_identity=(policy.uid, policy.gid),
            memory_max=policy.memory_max,
            pids_max=policy.pids_max,
            profile=policy.profile,
            seccomp=policy.seccomp,
            caps=policy.caps,
            device_filter=policy.device_filter,
            masked_paths=policy.masked_paths,
            no_new_privs=policy.no_new_privs,
            mount=(
                None
                if args.mount is None
                else SandboxMount.parse(args.mount, tuple(args.mount_deny))
            ),
        ).validated()
        _validate_sandbox_systemd_policy(launch)
    else:
        launch = None

    if operation == "provision":
        if args.state_dir is None:
            raise ScriptError("sandbox provision requires --state-dir")
        assert launch is not None
        sandbox_lifecycle.provision(
            args.state_dir,
            launch,
            hypervisor=_hypervisor(args.hypervisor),
            memory_mib=args.memory_mib,
            net=args.net,
            network_profile=args.network_profile,
            network_egress=args.network_egress,
            network_ingress=args.network_ingress,
            network_egress_allow=tuple(args.network_egress_allow),
            network_egress_deny=tuple(args.network_egress_deny),
            host_loopback=args.host_loopback,
            network_proxy=args.network_proxy,
            host_loopback_forward=tuple(args.host_loopback_forward),
            cmdline=args.cmdline,
        )
        return
    if operation == "start":
        if args.state_dir is None:
            raise ScriptError("sandbox start requires --state-dir")
        sandbox_lifecycle.start(args.state_dir, args.timeout)
        return
    if operation == "exec":
        if args.state_dir is None:
            raise ScriptError("sandbox exec requires --state-dir")
        if args.outcome_report is not None:
            sandbox_lifecycle.validate_outcome_destination(args.outcome_report)
        result = sandbox_lifecycle.exec_workload(
            args.state_dir,
            (args.entrypoint, *args.sandbox_arg),
            timeout_ms=args.exec_timeout_ms
            if args.exec_timeout_ms is not None
            else 60000,
            response_timeout=args.timeout,
        )
        sys.stdout.buffer.write(result.stdout)
        sys.stdout.buffer.flush()
        sys.stderr.buffer.write(result.stderr)
        sys.stderr.buffer.flush()
        if args.outcome_report is not None:
            sandbox_lifecycle.write_exec_outcome(args.outcome_report, result)
        raise SystemExit(result.returncode)
    if operation == "stop":
        if args.state_dir is None:
            raise ScriptError("sandbox stop requires --state-dir")
        sandbox_lifecycle.stop(args.state_dir, args.timeout)
        return
    if operation == "deprovision":
        if args.state_dir is None:
            raise ScriptError("sandbox deprovision requires --state-dir")
        sandbox_lifecycle.deprovision(args.state_dir)
        return
    if args.state_dir is not None:
        raise ScriptError(
            "one-shot sandbox execution rejects persistent --state-dir settings"
        )
    assert operation == "run"
    assert launch is not None
    executable = require_file(openvmm_binary_path(), "OpenVMM release binary")
    backend = _hypervisor(args.hypervisor)
    arm = _arm_direct(backend)
    architecture = "aarch64" if arm else "x86_64"
    kernel = require_file(
        artifact_path(
            KernelBuildConstants.BINARY_NAME_AARCH64
            if arm
            else KernelBuildConstants.BINARY_NAME
        ),
        "Linux direct kernel",
    )
    initrd = require_file(
        artifact_path(AlpineBuildConstants.INITRAMFS_NAME),
        "initramfs",
    )
    command = [
        str(executable),
        *launch.openvmm_arguments(backend, architecture=architecture),
        *(["--com1", "console"] if arm else ["--microvm-lifecycle", "one-shot"]),
        "--single-process",
        "--hypervisor",
        _hypervisor(args.hypervisor),
        "--memory",
        f"{args.memory_mib}M",
        "--kernel",
        str(kernel),
        "--initrd",
        str(initrd),
        "--cmdline",
        launch.kernel_command_line(args.cmdline, backend, architecture=architecture)
        + f" nvx_wall_timeout_ms={wall_timeout_ms}",
    ]
    if arm:
        network_args, network_cmdline = hvf_network_arguments(vars(args))
        command.extend(
            [*network_args, "--cmdline", hvf_boot_tokens(False) + " " + network_cmdline]
        )
    else:
        _extend_network_arguments(command, args)
        command.extend(["--cmdline", f"nvx_host_epoch={int(time.time())}"])
    if args.outcome_report is not None:
        command.extend(["--microvm-report", str(args.outcome_report)])
    print(f">> {_format_command(command)}")
    if not args.dry_run:
        status = subprocess.run(command).returncode
        raise SystemExit(status)


def command_collect_sources(_: argparse.Namespace) -> None:
    collect_release_sources(DockerBuildConfig())


def command_package(args: argparse.Namespace) -> None:
    if args.platform is not None or _arm_direct(_hypervisor("auto")):
        from nvx_tools.runtime_release import package

        if args.force:
            raise ScriptError(
                "self-contained packages require a new destination; omit --force"
            )

        selected = args.platform or (
            "darwin-arm64" if sys.platform == "darwin" else "linux-arm64"
        )
        version = (
            args.version or (BuildConstants.REPO_ROOT / "VERSION").read_text().strip()
        )
        destination = (
            args.destination
            or BuildConstants.REPO_ROOT / "release" / version / selected
        )
        print(
            package(
                destination,
                selected,
                version,
                development=args.development,
                source=args.include_source,
            )
        )
        return
    package_release(
        version=args.version,
        destination=args.destination,
        include_source=args.include_source,
        force=args.force,
    )


def command_archive_release(args: argparse.Namespace) -> None:
    create_release_archive(args.source, args.destination)


def command_verify(_: argparse.Namespace) -> None:
    verify_source_tree()


def command_snapshot_verify(args: argparse.Namespace) -> None:
    report = verify_snapshot(args.snapshot_dir)
    print(format_report(report))
    print("snapshot OK")


def _add_guest_options(
    parser: argparse.ArgumentParser,
    *,
    allow_all: bool,
) -> None:
    choices = (*GUEST_NAMES, "all") if allow_all else GUEST_NAMES
    parser.add_argument(
        "--guest",
        choices=choices,
        default=InitramfsBuildConstants.DEFAULT_GUEST,
        help=(
            "guest userland to build "
            f"(default: {InitramfsBuildConstants.DEFAULT_GUEST})"
        ),
    )
    parser.add_argument(
        "--native",
        action="store_true",
        help="build directly on Linux instead of using Docker",
    )


def _add_openvmm_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--skip-restore", action="store_true")
    parser.add_argument(
        "--backend",
        choices=OPENVMM_TEST_BACKENDS,
        help=(
            "select build target: kvm=GNU, mshv=musl, whp=MSVC "
            "(default: native target for the host OS)"
        ),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="initialize the private submodule")
    init.set_defaults(handler=command_init)

    guest = subparsers.add_parser("build-guest", help="build Linux guest artifacts")
    _add_guest_options(guest, allow_all=True)
    guest.set_defaults(handler=command_build_guest)

    kernel = subparsers.add_parser(
        "build-kernel",
        help="fetch, patch, and build the pinned kernel natively on Linux",
    )
    kernel.set_defaults(handler=command_build_kernel)

    initramfs = subparsers.add_parser(
        "build-initramfs",
        help="build a selected guest initramfs natively on Linux",
    )
    initramfs.add_argument(
        "--guest",
        choices=GUEST_NAMES,
        default=InitramfsBuildConstants.DEFAULT_GUEST,
        help=(
            "guest userland to build "
            f"(default: {InitramfsBuildConstants.DEFAULT_GUEST})"
        ),
    )
    initramfs.set_defaults(handler=command_build_initramfs)

    distro_layer = subparsers.add_parser(
        "build-distro-layer",
        help="build a deterministic EROFS distro layer natively on Linux",
    )
    distro_layer.add_argument("--guest", choices=GUEST_NAMES, required=True)
    distro_layer.add_argument(
        "--output",
        type=Path,
        default=artifact_path(UbuntuBuildConstants.DISTRO_NAME),
    )
    distro_layer.add_argument("--replace", action="store_true")
    distro_layer.set_defaults(handler=command_build_distro_layer)

    determinism = subparsers.add_parser(
        "verify-guest-determinism",
        help="build Ubuntu guest artifacts twice and compare them",
    )
    determinism.add_argument("--guest", choices=GUEST_NAMES, default="ubuntu")
    determinism.add_argument(
        "--work-dir",
        type=Path,
        default=(
            BuildConstants.BUILD_DIR
            / InitramfsBuildConstants.DETERMINISM_DIRECTORY_NAME
        ),
    )
    determinism.add_argument(
        "--image",
        action="append",
        help="reconvert an OCI image twice and compare every artifact digest (repeatable)",
    )
    determinism.set_defaults(handler=command_verify_guest_determinism)

    openvmm = subparsers.add_parser("build-openvmm", help="build OpenVMM")
    _add_openvmm_options(openvmm)
    openvmm.set_defaults(handler=command_build_openvmm)

    provenance = subparsers.add_parser(
        "record-openvmm-provenance",
        help="bind an existing OpenVMM binary to the pinned source revision",
    )
    provenance.set_defaults(handler=command_record_openvmm_provenance)

    kernel_provenance = subparsers.add_parser(
        "materialize-kernel-provenance-inputs",
        help="write kernel provenance inputs from raw run-head blobs",
    )
    kernel_provenance.set_defaults(handler=command_materialize_kernel_provenance_inputs)

    cache = subparsers.add_parser(
        "setup-cross-os-cache",
        help="install GNU tar and zstd for GitHub Actions cross-OS caches",
    )
    cache.set_defaults(handler=command_setup_cross_os_cache)

    required_ci = subparsers.add_parser(
        "check-required-ci",
        help="validate required GitHub Actions job results",
    )
    required_ci.add_argument(
        "--event-name",
        choices=("pull_request", "push"),
        required=True,
    )
    required_ci.add_argument(
        "--same-repository",
        choices=("false", "true"),
        required=True,
    )
    required_ci.add_argument("--self-hosted", choices=("false", "true"), default="true")
    required_ci.add_argument("--run-tests", required=True)
    required_ci.add_argument("--run-workloads", required=True)
    required_ci.set_defaults(handler=command_check_required_ci)

    openvmm_unit_tests = subparsers.add_parser(
        "test-openvmm-unit",
        help="run OpenVMM unit and documentation tests",
    )
    openvmm_unit_tests.set_defaults(handler=command_test_openvmm_unit)

    openvmm_tests = subparsers.add_parser(
        "test-openvmm",
        help="run OpenVMM Petri VMM tests",
    )
    openvmm_tests.add_argument(
        "--backend",
        choices=OPENVMM_TEST_BACKENDS,
        required=True,
    )
    openvmm_tests.set_defaults(handler=command_test_openvmm)

    microvm_tests = subparsers.add_parser(
        "test-microvm",
        help="run NVX-owned OpenVMM microVM correctness tests",
    )
    configure_microvm_test_parser(microvm_tests)

    adversarial_tests = subparsers.add_parser(
        "test-adversarial",
        help="run a brokered Copilot-driven adversarial campaign",
    )
    configure_adversarial_parser(adversarial_tests)

    build = subparsers.add_parser("build", help="build guest artifacts and OpenVMM")
    _add_guest_options(build, allow_all=True)
    _add_openvmm_options(build)
    build.set_defaults(handler=command_build)

    download = subparsers.add_parser(
        "download",
        help="download and install the latest matching GitHub release",
    )
    download.add_argument(
        "--repository",
        default=DEFAULT_RELEASE_REPOSITORY,
        metavar="OWNER/REPOSITORY",
    )
    download.add_argument("--hypervisor", choices=HYPERVISORS, default="auto")
    download.add_argument(
        "--allow-unsigned",
        action="store_true",
        help="explicitly permit an unsigned development or legacy upstream archive",
    )
    download.set_defaults(handler=command_download)

    run = subparsers.add_parser("run", help="run an OpenVMM microVM")
    run.add_argument("--guest", choices=GUEST_NAMES, default="alpine")
    run.add_argument("--events", type=Path, help="write host-owned JSONL run events")
    run.add_argument(
        "--image", help="run a converted OCI image through the managed sandbox agent"
    )
    run.add_argument(
        "workload", nargs=argparse.REMAINDER, help="image command after --"
    )
    run.add_argument("--hypervisor", choices=HYPERVISORS, default="auto")
    run.add_argument(
        "--machine",
        choices=("microvm",),
        default="microvm",
    )
    run.add_argument(
        "--memory-mib",
        type=int,
        help="guest RAM; defaults to 128 MiB for Alpine and 256 MiB for Ubuntu",
    )
    run.add_argument("--memory-capacity-mib", type=int)
    run.add_argument("--processors", type=int, choices=(1, 2, 4, 8), default=1)
    run.add_argument("--mount", help="GUEST_TARGET,HOST_PATH,ro|rw")
    run.add_argument("--workspace", help="HOST:GUEST[:ro|rw] (default read-only)")
    run.add_argument(
        "--out",
        type=Path,
        help="collect /out into a new host directory after clean completion",
    )
    run.add_argument(
        "--keep-alive",
        action="store_true",
        help="retain an image instance for exec/cp/stop",
    )
    run.add_argument("--mount-deny", action="append", type=Path, default=[])
    run.add_argument("--net", metavar="IPV4/PREFIX")
    run.add_argument("--network-profile", choices=NETWORK_PROFILES)
    run.add_argument("--network-egress", choices=("allow", "deny"))
    run.add_argument("--network-ingress", choices=("allow", "deny"))
    run.add_argument(
        "--network-egress-allow", "--egress-allow", action="append", default=[]
    )
    run.add_argument("--network-egress-deny", action="append", default=[])
    run.add_argument("--host-loopback", choices=("allow", "deny"))
    run.add_argument("--network-proxy", metavar="IPV4:TCP-PORT")
    run.add_argument("--host-loopback-forward", action="append", default=[])
    run.add_argument(
        "--virtio-net",
        action="append",
        default=[],
        metavar="BACKEND",
        help="expose a virtio NIC (e.g. 'consomme' or "
        "'consomme:192.168.127.0/24,hostfwd=tcp::18080-:3000'; "
        "on hvf the guest configures it via DHCP)",
    )
    run.add_argument(
        "--share",
        action="append",
        default=[],
        metavar="PORT:MNTPOINT[:ro|rw]",
        help="mount a host 9P server (see scripts/nvx_tools/nvx_9p.py) in the "
        "guest; requires a consomme --virtio-net (gwloopback is added "
        "automatically). The server must already be running. "
        "Example: --share 5564:/mnt/host",
    )
    run.add_argument(
        "--serve",
        action="append",
        default=[],
        metavar="HOSTDIR:MNTPOINT[:ro|rw]",
        help="self-serve variant of --share: start an in-process 9P server "
        "for HOSTDIR on an ephemeral loopback port and mount it in the "
        "guest; the server lives exactly as long as the run. "
        "Example: --serve ./payload:/mnt/host:ro",
    )
    run.add_argument(
        "--kernel",
        type=Path,
        help="custom direct-boot kernel image (fresh boot only; "
        "default: the built artifact for the hypervisor)",
    )
    run.add_argument(
        "--initrd",
        type=Path,
        help="custom initramfs image (fresh boot only; "
        "default: the built artifact for the guest)",
    )
    run.add_argument(
        "--disk",
        nargs="?",
        const=str(BuildConstants.REPO_ROOT / DEFAULT_DISK_NAME),
        metavar="PATH",
        help="attach a persistent virtio-blk disk (upstream file-backed "
        "disk, e.g. --virtio-blk file:...); a missing image is created as "
        f"a sparse {DEFAULT_DISK_MIB} MiB raw file. Bare --disk uses "
        f"{DEFAULT_DISK_NAME} in the repo root. hvf only; repeat the same "
        "--disk (and --virtio-net) on restore so the saved device "
        "inventory matches. The guest mounts it where --disk-mount says.",
    )
    run.add_argument(
        "--disk-mount",
        default="/data",
        help="guest mountpoint for --disk (default: /data)",
    )
    run.add_argument(
        "--save-snapshot",
        type=Path,
        metavar="DIR",
        help="scripted snapshot capture: wait for --save-on on the VM "
        "output, send `snap DIR` to the openvmm REPL on its stdin, wait "
        "for the upstream 'snapshot saved' marker, then shut down. "
        "Requires --save-on; fresh hvf boots also need --memory-backing-file.",
    )
    run.add_argument(
        "--save-on",
        metavar="MARKER",
        help="output marker that triggers --save-snapshot "
        "(e.g. VIRTDISK-OK or a payload READY line)",
    )
    run.add_argument(
        "--save-exec",
        metavar="CMD",
        help="guest shell line to run after --save-on and before capture "
        "(stdin is still in guest-forward mode, e.g. "
        "--save-exec 'echo hi > /data/stamp && echo STAMP-DONE'). "
        "Requires --save-ready.",
    )
    run.add_argument(
        "--save-ready",
        metavar="MARKER",
        help="output marker that --save-exec completed; capture starts here. "
        "Requires --save-exec.",
    )
    run.add_argument(
        "--save-timeout",
        type=float,
        default=600.0,
        help="seconds to wait for the save marker and the snapshot-saved "
        "marker each (default: 600)",
    )
    run.add_argument(
        "--outcome-report",
        type=Path,
        help="write a bounded local JSON outcome report",
    )
    run.add_argument("--instance-id-file", type=Path, help=argparse.SUPPRESS)
    run.add_argument(
        "--receipt", type=Path, help="write a version-1 run receipt (image runs)"
    )
    run.add_argument(
        "--receipt-signing-key", type=Path, help="Ed25519 private key for the receipt"
    )
    run.add_argument("--cmdline", default="")
    run.add_argument(
        "--memory-backing-file",
        type=Path,
        help="file-backed guest RAM for a fresh hvf boot; required to save "
        "a snapshot later from the openvmm REPL (`snap <dir>`). "
        "Only valid with --hypervisor hvf on a fresh boot.",
    )
    run.add_argument("--restore-snapshot", type=Path)
    run.add_argument("--restore-processors", type=int, choices=(1, 2, 4, 8))
    run.add_argument("--restore-memory-mib", type=int)
    run.add_argument("--restore-ready-path", type=Path)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--profile", choices=("default", "ci", "risky"))
    run.add_argument("--cap-add", action="append", choices=("MKNOD",))
    run.add_argument("--seccomp", choices=("nvx-default", "unconfined"))
    run.add_argument("--config", type=Path, default=Path("nvx.toml"))
    run.add_argument("--pids-max", type=int)
    run.add_argument("--memory-max", type=int)
    run.add_argument("--exec-timeout-ms", type=int)
    run.set_defaults(handler=command_run)

    sandbox = subparsers.add_parser(
        "sandbox",
        help="run or manage workloads over EROFS layers and private ext4 scratch",
    )

    def sandbox_layer(value: str) -> SandboxLayer:
        try:
            return SandboxLayer.parse(value)
        except ScriptError as error:
            raise argparse.ArgumentTypeError(str(error)) from error

    def sandbox_identity(value: str) -> tuple[int, int]:
        try:
            return parse_workload_identity(value)
        except ScriptError as error:
            raise argparse.ArgumentTypeError(str(error)) from error

    sandbox.add_argument(
        "sandbox_operation",
        nargs="?",
        choices=("run", "provision", "start", "exec", "stop", "deprovision"),
        default="run",
    )
    sandbox.add_argument(
        "--layer",
        action="append",
        default=[],
        type=sandbox_layer,
        metavar="ROLE,PATH,EROFS_UUID",
    )
    sandbox.add_argument("--scratch", type=Path)
    sandbox.add_argument("--image", help="use cached OCI layers and private scratch")
    sandbox.add_argument("--workspace", help="HOST:GUEST[:ro|rw]")
    sandbox.add_argument("--out", type=Path)
    sandbox.add_argument("--keep-alive", action="store_true")
    sandbox.add_argument("--state-dir", type=Path)
    sandbox.add_argument("--entrypoint", default="/bin/sh")
    sandbox.add_argument("--arg", action="append", default=[], dest="sandbox_arg")
    sandbox.add_argument("--hostname", default="nvx-sandbox")
    sandbox.add_argument(
        "--workload-user",
        type=sandbox_identity,
        default=None,
        metavar="UID:GID",
        help="fixed non-root workload identity (default: 65534:65534)",
    )
    sandbox.add_argument("--memory-max", type=int)
    sandbox.add_argument("--pids-max", type=int)
    sandbox.add_argument(
        "--memory-mib",
        type=int,
        help="guest RAM (HVF: 1024 MiB; other backends: 256 MiB)",
    )
    sandbox.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="control operation timeout in seconds (default: 60)",
    )
    sandbox.add_argument(
        "--exec-timeout-ms",
        type=int,
        default=None,
        help="guest workload timeout in milliseconds; zero disables it",
    )
    sandbox.add_argument("--hypervisor", choices=HYPERVISORS, default="auto")
    sandbox.add_argument(
        "--mount",
        metavar="GUEST_TARGET,HOST_PATH[,ro|rw]",
        help="live-share one host directory inside the container rootfs",
    )
    sandbox.add_argument(
        "--mount-deny",
        action="append",
        default=[],
        metavar="HOST_PATH",
        help="hide one existing path inside the --mount host directory",
    )
    sandbox.add_argument("--net", metavar="IPV4/PREFIX")
    sandbox.add_argument("--network-profile", choices=NETWORK_PROFILES)
    sandbox.add_argument("--network-egress", choices=("allow", "deny"))
    sandbox.add_argument("--network-ingress", choices=("allow", "deny"))
    sandbox.add_argument(
        "--network-egress-allow", "--egress-allow", action="append", default=[]
    )
    sandbox.add_argument("--network-egress-deny", action="append", default=[])
    sandbox.add_argument("--host-loopback", choices=("allow", "deny"))
    sandbox.add_argument("--network-proxy", metavar="IPV4:TCP-PORT")
    sandbox.add_argument("--host-loopback-forward", action="append", default=[])
    sandbox.add_argument(
        "--outcome-report",
        type=Path,
        help="write a bounded local JSON outcome report for run or exec",
    )
    sandbox.add_argument("--cmdline", default="")
    sandbox.add_argument("--dry-run", action="store_true")
    sandbox.add_argument("--profile", choices=("default", "ci", "risky"))
    sandbox.add_argument("--cap-add", action="append", choices=("MKNOD",))
    sandbox.add_argument("--seccomp", choices=("nvx-default", "unconfined"))
    sandbox.add_argument("--config", type=Path, default=Path("nvx.toml"))
    sandbox.set_defaults(handler=command_sandbox)

    benchmark = subparsers.add_parser(
        "benchmark",
        help="run the OpenVMM-native benchmark coordinator",
    )
    configure_benchmark_parser(benchmark, BuildConstants.REPO_ROOT)

    performance = subparsers.add_parser(
        "performance",
        help="collect, persist, and gate CI performance results",
    )
    configure_performance_parser(performance)

    sources = subparsers.add_parser(
        "collect-sources",
        help="materialize verified Linux, Alpine, and Ubuntu release sources",
    )
    sources.set_defaults(handler=command_collect_sources)

    alpine_sources = subparsers.add_parser(
        "collect-alpine-sources",
        help="collect exact Alpine recipes and upstream sources",
    )
    configure_alpine_sources_parser(alpine_sources)

    ubuntu_sources = subparsers.add_parser(
        "collect-ubuntu-sources",
        help="collect exact Ubuntu source packages",
    )
    configure_ubuntu_sources_parser(ubuntu_sources)

    linux_source_archive = subparsers.add_parser(
        "create-linux-source-archive",
        help="create the Linux corresponding-source archive from pinned inputs",
    )
    configure_linux_source_archive_parser(linux_source_archive)

    package = subparsers.add_parser("package", help="stage a binary distribution")
    package.add_argument("--version")
    package.add_argument(
        "--platform",
        choices=(
            "darwin-arm64",
            "darwin-x86_64",
            "linux-arm64",
            "linux-kvm",
            "linux-mshv",
            "windows-whp",
        ),
    )
    package.add_argument(
        "--development",
        action="store_true",
        help="label a local preview; publication remains gated",
    )
    package.add_argument("--destination", type=Path)
    source_mode = package.add_mutually_exclusive_group(required=True)
    source_mode.add_argument("--include-source", action="store_true")
    source_mode.add_argument(
        "--binary-only",
        action="store_true",
        help="stage binaries only; corresponding source must be published separately",
    )
    package.add_argument("--force", action="store_true")
    package.set_defaults(handler=command_package)

    archive_release = subparsers.add_parser(
        "archive-release",
        help="create a deterministic archive from a staged distribution",
    )
    archive_release.add_argument("--source", type=Path, required=True)
    archive_release.add_argument("--destination", type=Path, required=True)
    archive_release.set_defaults(handler=command_archive_release)

    verify = subparsers.add_parser("verify", help="verify source and submodule inputs")
    verify.set_defaults(handler=command_verify)

    snapshot = subparsers.add_parser(
        "snapshot", help="inspect and validate saved VM snapshots"
    )
    snapshot_subparsers = snapshot.add_subparsers(
        dest="snapshot_command", required=True
    )
    snapshot_verify = snapshot_subparsers.add_parser(
        "verify", help="validate a snapshot directory before restoring it"
    )
    snapshot_verify.add_argument("snapshot_dir", type=Path)
    snapshot_verify.set_defaults(handler=command_snapshot_verify)
    from nvx_tools.execdiff import configure_parser as configure_execdiff_parser

    configure_execdiff_parser(
        subparsers.add_parser(
            "execdiff",
            help="compare executable images in snapshot memory",
        )
    )
    from nvx_tools.containment import configure_parser as configure_containment_parser

    configure_containment_parser(
        subparsers.add_parser(
            "containment", help="run or render the scoped containment battery"
        )
    )

    from nvx_tools.receipt import configure_parser as configure_receipt_parser

    configure_receipt_parser(
        subparsers.add_parser(
            "receipt", help="verify run receipt integrity and signatures"
        )
    )

    from nvx_tools.policy import configure_parser as configure_policy_parser

    configure_policy_parser(
        subparsers.add_parser(
            "policy", help="show or lint the resolved security profile"
        )
    )

    from nvx_tools.image import configure_parser as configure_image_parser

    configure_image_parser(
        subparsers.add_parser(
            "image", help="pull, convert, list, verify, or remove OCI images"
        )
    )

    from nvx_tools.bundle import configure_parser as configure_bundle_parser
    from nvx_tools.mcp import configure_parser as configure_mcp_parser
    from nvx_tools.pool import configure_parser as configure_pool_parser
    from nvx_tools.registry import command_events
    from nvx_tools.registry import configure_parsers as configure_registry_parsers
    from nvx_tools.warm import configure_parser as configure_warm_parser

    configure_registry_parsers(subparsers)
    configure_warm_parser(subparsers)
    configure_pool_parser(subparsers)
    configure_mcp_parser(subparsers)
    from nvx_tools.runtime_release import configure_parser as configure_install_parser

    configure_install_parser(subparsers)
    run.add_argument("--pool", help="lease one repaired clone from a running pool")
    for image_parser in (run, sandbox):
        image_parser.add_argument(
            "--stream",
            action="store_true",
            help="write each managed output frame as it arrives",
        )
    for image_parser in (run, sandbox):
        image_parser.add_argument(
            "--secret",
            action="append",
            default=[],
            help="host environment NAME[@HOST:PORT], read only by the host proxy",
        )
        image_parser.add_argument(
            "--secret-header",
            action="append",
            default=[],
            help="NAME:HEADER (default Authorization: Bearer)",
        )
        image_parser.add_argument("--proxy-log", type=Path)
        image_parser.add_argument(
            "--proxy-ca", type=Path, help="additional trusted upstream TLS CA file"
        )
        image_parser.add_argument(
            "--env",
            action="append",
            default=[],
            help="explicit guest NAME=VALUE; values enter guest memory",
        )
    configure_bundle_parser(
        subparsers.add_parser(
            "bundle", help="create or verify a deterministic evidence bundle"
        )
    )
    events = subparsers.add_parser(
        "events", help="read ordered host events by instance ID"
    )
    events.add_argument("id")
    events.set_defaults(handler=command_events)

    doctor = subparsers.add_parser(
        "doctor", help="check host, artifacts, cache, and versions"
    )
    configure_doctor_parser(doctor)
    setup = subparsers.add_parser(
        "setup", help="sign and verify macOS Hypervisor entitlement"
    )
    setup.set_defaults(handler=command_setup)
    explain = subparsers.add_parser("explain", help="explain recorded policy denials")
    configure_explain_parser(explain)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = args.handler(args)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except (
        ScriptError,
        OSError,
        RuntimeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return result if result is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
