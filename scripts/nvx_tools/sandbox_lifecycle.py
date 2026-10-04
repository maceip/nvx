"""Persistent lifecycle operations for managed NVX microVM sandboxes."""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import platform
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any, cast

from .build_constants import (
    AlpineBuildConstants,
    KernelBuildConstants,
)
from .common import (
    ScriptError,
    artifact_path,
    openvmm_binary_path,
    require_file,
)
from .control_session import (
    MANAGED_EXIT_CATEGORIES,
    ControlSession,
    ManagedExecResult,
)
from .events import EventLog, redact
from .hvf import boot_tokens, network_arguments
from .sandbox import SandboxLaunch, SandboxLayer, SandboxMount

CONFIG_NAME = "config.json"
RUNTIME_NAME = "runtime.json"
CAPABILITY_NAME = "control.capability"
LOG_NAME = "openvmm.log"
CONTROL_SOCKET_NAME = "control.sock"
OUTCOME_NAME = "outcome.json"
STATE_FORMAT = 1
CONFIG_FORMAT = 5
# Format-1 readers ignore unknown fields, so a configuration with a live share
# uses a format that older NVX releases reject instead of starting without it.
MOUNT_CONFIG_FORMAT = 6
CONFIG_FORMATS = (1, 2, 3, 4, CONFIG_FORMAT, MOUNT_CONFIG_FORMAT)
OUTCOME_SCHEMA_VERSION = 1


def startup_failure(log_path: Path, status: int) -> ScriptError:
    """Include a bounded, redacted diagnostic without reading the whole VM log."""
    with log_path.open("rb") as log:
        log.seek(0, os.SEEK_END)
        log.seek(max(0, log.tell() - 4096))
        tail = log.read(4096).decode("utf-8", errors="replace")
    return ScriptError(
        f"OpenVMM exited with status {status} before control readiness; "
        f"see {log_path}\n{redact(tail)}"
    )


def _write_json(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(
    path: Path,
    description: str,
    *,
    version_field: str = "format",
    version: int | tuple[int, ...] = STATE_FORMAT,
) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ScriptError(f"failed to read {description}: {path}") from error
    if not isinstance(value, dict):
        raise ScriptError(f"{description} has an unsupported format: {path}")
    typed = cast(dict[str, Any], value)
    accepted = (version,) if isinstance(version, int) else version
    if typed.get(version_field) not in accepted:
        raise ScriptError(f"{description} has an unsupported format: {path}")
    return typed


def _outcome_destination(path: Path) -> Path:
    candidate = path if path.is_absolute() else Path.cwd() / path
    parent = candidate.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ScriptError(f"outcome report parent is not a plain directory: {parent}")
    if not candidate.name:
        raise ScriptError("outcome report path has no filename")
    resolved = parent.resolve() / candidate.name
    if os.path.lexists(resolved):
        raise ScriptError(f"outcome report already exists: {resolved}")
    return resolved


def validate_outcome_destination(path: Path) -> None:
    _outcome_destination(path)


def _write_new_json(path: Path, value: dict[str, Any]) -> None:
    resolved = _outcome_destination(path)
    temporary = resolved.with_name(f".{resolved.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
    except FileExistsError as error:
        raise ScriptError("failed to reserve an outcome report staging file") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, resolved)
        except FileExistsError as error:
            raise ScriptError(f"outcome report already exists: {resolved}") from error
        except OSError as error:
            raise ScriptError(
                f"failed to publish outcome report: {resolved}"
            ) from error
    finally:
        temporary.unlink(missing_ok=True)


def publish_json(path: Path, value: dict[str, Any]) -> None:
    _write_new_json(path, value)


def replace_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically replace an internal state document, preserving private mode."""
    _write_json(path, value)


def _read_openvmm_outcome(path: Path) -> dict[str, Any]:
    typed = _read_json(
        path,
        "OpenVMM outcome report",
        version_field="schema_version",
        version=OUTCOME_SCHEMA_VERSION,
    )
    for name in ("outcome", "network_policy", "teardown"):
        if not isinstance(typed.get(name), dict):
            raise ScriptError(
                f"OpenVMM outcome report has an invalid {name} section: {path}"
            )
    return typed


def write_exec_outcome(path: Path, result: ManagedExecResult) -> None:
    if result.category not in MANAGED_EXIT_CATEGORIES:
        raise ScriptError("managed workload returned an unsupported outcome category")
    if not -(2**31) <= result.returncode < 2**31:
        raise ScriptError("managed workload returned an out-of-range status")
    _write_new_json(
        path,
        {
            "schema_version": OUTCOME_SCHEMA_VERSION,
            "operation_id": secrets.token_hex(16),
            "outcome": {
                "operation": "exec",
                "category": result.category,
                "status_code": result.returncode,
            },
        },
    )


def _prepare_state_directory(path: Path, *, create: bool) -> Path:
    if path.is_symlink():
        raise ScriptError(f"sandbox state path is not a plain directory: {path}")
    resolved = path.resolve()
    if create:
        resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(resolved, 0o700)
    if resolved.is_symlink() or not resolved.is_dir():
        raise ScriptError(f"sandbox state path is not a plain directory: {resolved}")
    return resolved


def _serialize_launch(
    launch: SandboxLaunch,
    *,
    hypervisor: str,
    memory_mib: int,
    net: str | None,
    network_profile: str | None,
    network_egress: str | None,
    network_ingress: str | None,
    network_egress_allow: tuple[str, ...],
    network_egress_deny: tuple[str, ...],
    host_loopback: str | None,
    network_proxy: str | None,
    host_loopback_forward: tuple[str, ...],
    cmdline: str,
) -> dict[str, Any]:
    return {
        "format": CONFIG_FORMAT if launch.mount is None else MOUNT_CONFIG_FORMAT,
        "layers": [
            {
                "role": layer.role,
                "path": os.fspath(layer.path.resolve()),
                "uuid": layer.uuid,
            }
            for layer in launch.ordered_layers()
        ],
        "scratch": os.fspath(launch.scratch.resolve()),
        "hostname": launch.hostname,
        "profile": launch.profile,
        "seccomp": launch.seccomp,
        "caps": list(launch.caps),
        "device_filter": launch.device_filter,
        "masked_paths": launch.masked_paths,
        "no_new_privs": launch.no_new_privs,
        "workload_uid": launch.workload_identity[0],
        "workload_gid": launch.workload_identity[1],
        "memory_max": launch.memory_max,
        "pids_max": launch.pids_max,
        "hypervisor": hypervisor,
        "memory_mib": memory_mib,
        "net": net,
        "network_profile": network_profile,
        "network_egress": network_egress,
        "network_ingress": network_ingress,
        "network_egress_allow": list(network_egress_allow),
        "network_egress_deny": list(network_egress_deny),
        "host_loopback": host_loopback,
        "network_proxy": network_proxy,
        "host_loopback_forward": list(host_loopback_forward),
        "cmdline": cmdline,
        "mount": _serialize_mount(launch.mount),
    }


def _serialize_mount(mount: SandboxMount | None) -> dict[str, Any] | None:
    if mount is None:
        return None
    absolute = mount.absolute()
    return {
        "guest_target": absolute.guest_target,
        "host_path": os.fspath(absolute.host_path),
        "access": absolute.access,
        "denied_paths": list(absolute.denied_paths),
    }


def _deserialize_mount(value: object) -> SandboxMount | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("sandbox mount configuration must be an object")
    mount = cast(dict[str, Any], value)
    denied_paths = mount["denied_paths"]
    if not isinstance(denied_paths, list):
        raise TypeError("sandbox mount denied paths must be a list")
    return SandboxMount(
        guest_target=str(mount["guest_target"]),
        host_path=Path(str(mount["host_path"])),
        access=str(mount["access"]),
        denied_paths=tuple(str(path) for path in cast(list[object], denied_paths)),
    )


def _deserialize_launch(config: dict[str, Any]) -> SandboxLaunch:
    try:
        layers = tuple(
            SandboxLayer(
                role=str(layer["role"]),
                path=Path(str(layer["path"])),
                uuid=str(layer["uuid"]),
            )
            for layer in config["layers"]
        )
        identity = (int(config["workload_uid"]), int(config["workload_gid"]))
        launch = SandboxLaunch(
            layers=layers,
            scratch=Path(str(config["scratch"])),
            hostname=str(config["hostname"]),
            profile=str(config.get("profile", "default")),
            seccomp=str(config.get("seccomp", "nvx-default")),
            caps=tuple(config.get("caps", ())),
            device_filter=config.get("device_filter", config.get("profile") != "risky"),
            masked_paths=config.get("masked_paths", config.get("profile") != "risky"),
            no_new_privs=config.get("no_new_privs", config.get("profile") != "risky"),
            workload_identity=identity,
            memory_max=(
                None if config["memory_max"] is None else int(config["memory_max"])
            ),
            pids_max=None if config["pids_max"] is None else int(config["pids_max"]),
            mount=_deserialize_mount(config.get("mount")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ScriptError("sandbox configuration is malformed") from error
    if (config.get("format") in (2, 4, MOUNT_CONFIG_FORMAT)) != (
        launch.mount is not None
    ):
        raise ScriptError("sandbox configuration format does not match its mount")
    return launch.validated()


def process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            # A caller can start and stop a VM within the same Python process.
            # Reap its terminated child before kill(0), which sees zombies alive.
            waited, _ = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                return False
        except ChildProcessError:
            pass
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_uint32()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise OSError(ctypes.get_last_error(), "GetExitCodeProcess failed")
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _load_running(state_dir: Path) -> tuple[dict[str, Any], bytes]:
    runtime_path = state_dir / RUNTIME_NAME
    if not runtime_path.is_file():
        raise ScriptError("sandbox is not running")
    runtime = _read_json(runtime_path, "sandbox runtime state")
    try:
        pid = int(runtime["pid"])
    except (KeyError, TypeError, ValueError) as error:
        raise ScriptError("sandbox runtime state has an invalid process ID") from error
    if not process_running(pid):
        raise ScriptError(
            "sandbox runtime state is stale because the OpenVMM process is not running"
        )
    capability = require_file(
        state_dir / CAPABILITY_NAME, "sandbox control capability"
    ).read_bytes()
    if len(capability) != 32 or capability == bytes(32):
        raise ScriptError("sandbox control capability is invalid")
    return runtime, capability


def _endpoint(runtime: dict[str, Any]) -> Path:
    try:
        return Path(str(runtime["control_endpoint"]))
    except KeyError as error:
        raise ScriptError("sandbox runtime state has no control endpoint") from error


def provision(
    state_path: Path,
    launch: SandboxLaunch,
    *,
    hypervisor: str,
    memory_mib: int,
    net: str | None,
    network_profile: str | None,
    network_egress: str | None,
    network_ingress: str | None,
    network_egress_allow: tuple[str, ...],
    network_egress_deny: tuple[str, ...],
    host_loopback: str | None,
    network_proxy: str | None,
    host_loopback_forward: tuple[str, ...],
    cmdline: str,
) -> None:
    if (net is None) != (network_profile is None):
        raise ScriptError("--net and --network-profile must be specified together")
    if (
        net is None
        and platform.machine().lower() not in ("aarch64", "arm64")
        and (
            network_egress is not None
            or network_ingress is not None
            or network_egress_allow
            or network_egress_deny
            or host_loopback is not None
            or network_proxy is not None
            or host_loopback_forward
        )
    ):
        net = "192.168.127.2/24"
        network_profile = "portable"
    state_dir = _prepare_state_directory(state_path, create=True)
    config_path = state_dir / CONFIG_NAME
    runtime_path = state_dir / RUNTIME_NAME
    if config_path.exists() or runtime_path.exists():
        raise ScriptError("sandbox is already provisioned")
    _write_json(
        config_path,
        _serialize_launch(
            launch.validated(),
            hypervisor=hypervisor,
            memory_mib=memory_mib,
            net=net,
            network_profile=network_profile,
            network_egress=network_egress,
            network_ingress=network_ingress,
            network_egress_allow=network_egress_allow,
            network_egress_deny=network_egress_deny,
            host_loopback=host_loopback,
            network_proxy=network_proxy,
            host_loopback_forward=host_loopback_forward,
            cmdline=cmdline,
        ),
    )


def microvm_network_endpoint(value: str) -> str:
    """Accept a subnet or guest CIDR and pass the actual guest address to core."""
    try:
        interface = ipaddress.IPv4Interface(value)
    except ValueError as error:
        raise ScriptError("microVM networking requires an IPv4 CIDR") from error
    network = interface.network
    if network.num_addresses < 4:
        raise ScriptError("microVM subnet must have room for gateway and guest")
    address = interface.ip
    if address == network.network_address:
        address = network.network_address + 2
    if address in (network.network_address + 1, network.broadcast_address):
        raise ScriptError("microVM guest address conflicts with gateway or broadcast")
    return f"{address}/{network.prefixlen}"


@contextlib.contextmanager
def sealed_capability_pipe(capability: bytes) -> Generator[int, None, None]:
    """Present the complete capability and EOF before the VMM can read either."""
    if len(capability) != 32 or capability == bytes(32):
        raise ScriptError("control capability must be 32 nonzero bytes")
    reader, writer = os.pipe()
    try:
        try:
            if os.write(writer, capability) != len(capability):
                raise ScriptError("failed to write the complete control capability")
        finally:
            os.close(writer)
        yield reader
    finally:
        os.close(reader)


def connect_when_ready(
    endpoint: Path,
    capability: bytes,
    process: subprocess.Popen[bytes],
    log_path: Path,
    timeout: float,
) -> ControlSession:
    deadline = time.monotonic() + timeout
    while True:
        status = process.poll()
        if status is not None:
            raise startup_failure(log_path, status)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("managed guest did not become ready")
        try:
            # WAIT belongs to this attachment. Reconnecting every second
            # resets the guest protocol and can starve a slower cold boot.
            return ControlSession.connect(endpoint, capability, remaining)
        except TimeoutError:
            continue
        except (ConnectionError, OSError):
            status = process.poll()
            if status is not None:
                raise startup_failure(log_path, status) from None
            raise


def start(
    state_path: Path,
    timeout: float,
    *,
    memory_backing: Path | None = None,
    restore: Path | None = None,
    keep_stdin: bool = False,
    snapshot_destination: Path | None = None,
) -> subprocess.Popen[bytes]:
    state_dir = _prepare_state_directory(state_path, create=False)
    config = _read_json(
        require_file(state_dir / CONFIG_NAME, "sandbox configuration"),
        "sandbox configuration",
        version=CONFIG_FORMATS,
    )
    if (state_dir / RUNTIME_NAME).exists():
        raise ScriptError("sandbox is already running or has stale runtime state")
    outcome_path = state_dir / OUTCOME_NAME
    outcome_path.unlink(missing_ok=True)
    launch = _deserialize_launch(config)
    backend = str(config["hypervisor"])
    arm = platform.machine().lower() in ("aarch64", "arm64")
    if arm and backend not in ("hvf", "kvm"):
        raise ScriptError("aarch64 managed guests require KVM or HVF")
    architecture = "aarch64" if arm else "x86_64"
    executable = require_file(openvmm_binary_path(), "OpenVMM release binary")
    kernel = require_file(
        artifact_path(
            KernelBuildConstants.BINARY_NAME_AARCH64
            if arm
            else KernelBuildConstants.BINARY_NAME
        ),
        "Linux direct kernel",
    )
    initrd = require_file(
        artifact_path(AlpineBuildConstants.INITRAMFS_NAME), "initramfs"
    )
    capability = secrets.token_bytes(32)
    if capability == bytes(32):
        raise AssertionError("secrets.token_bytes returned an all-zero capability")
    control_directory: Path | None = None
    control_parent = (
        restore.parent
        if restore is not None
        else snapshot_destination.parent
        if snapshot_destination is not None
        else None
    )
    control_path = (
        (control_parent / ("control-" + uuid.uuid4().hex[:16] + ".sock"))
        if control_parent is not None
        else state_dir / CONTROL_SOCKET_NAME
    )
    if os.name != "nt" and len(os.fsencode(control_path)) >= 100:
        control_directory = Path(tempfile.mkdtemp(prefix="nvxctl-", dir="/tmp"))
        if restore is not None:
            pinned = control_directory / "snapshot"
            pinned.mkdir()
            for name in ("manifest.bin", "state.bin", "memory.bin", "scratch.img"):
                if name == "scratch.img" and not (restore / name).exists():
                    continue
                os.link(restore / name, pinned / name)
            restore = pinned
        elif snapshot_destination is not None:
            snapshot_destination = control_directory / "snapshot"
        control_path = control_directory / CONTROL_SOCKET_NAME
    endpoint_value = (
        f"//./pipe/openvmm-microvm-{uuid.uuid4().hex}"
        if os.name == "nt"
        else os.fspath(control_path)
    )
    command = [
        os.fspath(executable),
        *launch.openvmm_arguments(
            backend, architecture=architecture, restore=restore is not None and not arm
        ),
        *(
            ["--com1", "stderr"]
            if arm
            else []
            if restore is not None
            else ["--microvm-lifecycle", "managed"]
        ),
        "--single-process",
        "--hypervisor",
        str(config["hypervisor"]),
        *(
            ["--memory", f"{int(config['memory_mib'])}M"]
            if arm or restore is None
            else []
        ),
        *(
            ["--restore-snapshot", os.fspath(restore)]
            if restore is not None
            else [
                "--kernel",
                os.fspath(kernel),
                "--initrd",
                os.fspath(initrd),
                "--cmdline",
                launch.kernel_command_line(
                    str(config["cmdline"]), backend, architecture=architecture
                ),
            ]
        ),
        "--virtio-console",
        "stderr",
        "--microvm-control-console",
        f"listen={endpoint_value}",
        "--microvm-control-auth-stdin",
    ]
    if arm:
        network_args, network_cmdline = network_arguments(config)
        if memory_backing is not None or restore is not None:
            network_args = [
                value + ",snapshot" if value.startswith("consomme:") else value
                for value in network_args
            ]
        command.extend(
            [
                *network_args,
                *(
                    []
                    if restore is not None
                    else ["--cmdline", boot_tokens(True) + " " + network_cmdline]
                ),
            ]
        )
        command.extend(["--microvm-report", os.fspath(outcome_path)])
    else:
        command.extend(
            [
                "--microvm-report",
                os.fspath(outcome_path),
                *(
                    []
                    if restore is not None
                    else ["--cmdline", f"nvx_host_epoch={int(time.time())}"]
                ),
            ]
        )
    net = config.get("net")
    network_profile = config.get("network_profile")
    if net is not None and not arm and restore is None:
        command.extend(
            [
                "--net",
                microvm_network_endpoint(str(net)),
                "--network-profile",
                str(network_profile),
            ]
        )
    for name in ("network_egress", "network_ingress", "host_loopback"):
        value = config.get(name)
        if value is not None and not arm:
            command.extend([f"--{name.replace('_', '-')}", str(value)])
    for name in (
        "network_egress_allow",
        "network_egress_deny",
        "host_loopback_forward",
    ):
        values = config.get(name, [])
        if not isinstance(values, list):
            raise ScriptError("sandbox configuration is malformed")
        for value in cast(list[object], values) if not arm else []:
            command.extend([f"--{name.replace('_', '-')}", str(value)])
    network_proxy = config.get("network_proxy")
    if network_proxy is not None and not arm:
        command.extend(["--network-proxy", str(network_proxy)])
    if keep_stdin:
        command.append("--microvm-control-repl")
    if memory_backing is not None:
        command.extend(["--memory-backing-file", os.fspath(memory_backing)])
    if snapshot_destination is not None:
        if arm or restore is not None:
            raise ScriptError("microVM snapshot capture requires a fresh x86 source")
        command.extend(
            [
                "--snapshot-destination",
                os.fspath(snapshot_destination),
                "--snapshot-tier",
                "workload-start",
            ]
        )
    if restore is not None and not arm:
        command.append("--restore-entropy")

    capability_path = state_dir / CAPABILITY_NAME
    capability_path.write_bytes(capability)
    os.chmod(capability_path, 0o600)
    environment = os.environ.copy()
    flow_path = state_dir / "flows.jsonl"
    if flow_path.exists():
        flow_path.replace(state_dir / f"flows-{time.time_ns()}.jsonl")
    environment["NVX_FLOW_LOG"] = os.fspath(flow_path)
    log_path = state_dir / LOG_NAME
    log = log_path.open("ab", buffering=0)
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    )
    process: subprocess.Popen[bytes] | None = None
    try:
        with (
            contextlib.nullcontext(subprocess.PIPE)
            if keep_stdin
            else sealed_capability_pipe(capability)
        ) as capability_input:
            process = subprocess.Popen(
                command,
                stdin=capability_input,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=creationflags,
                env=environment,
            )
        if keep_stdin:
            if process.stdin is None:
                raise ScriptError("failed to create the OpenVMM capability pipe")
            process.stdin.write(capability)
            process.stdin.flush()
        _write_json(
            state_dir / RUNTIME_NAME,
            {
                "format": STATE_FORMAT,
                "pid": process.pid,
                "control_endpoint": endpoint_value,
                "started_epoch": time.time(),
                "control_directory": str(control_directory)
                if control_directory
                else None,
                "snapshot_destination": str(snapshot_destination)
                if snapshot_destination is not None
                else None,
            },
        )
        session = connect_when_ready(
            Path(endpoint_value), capability, process, log_path, timeout
        )
        with session:
            session.ping(timeout)
        EventLog(state_dir / "events.jsonl", state_dir.name).emit(
            "run.started", backend=backend, pid=process.pid
        )
        return process
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        (state_dir / RUNTIME_NAME).unlink(missing_ok=True)
        capability_path.unlink(missing_ok=True)
        if os.name != "nt":
            Path(endpoint_value).unlink(missing_ok=True)
        if control_directory is not None:
            shutil.rmtree(control_directory)
        raise
    finally:
        log.close()


def exec_workload(
    state_path: Path,
    arguments: tuple[str, ...],
    *,
    timeout_ms: int,
    response_timeout: float,
    output: Callable[[str, bytes], None] | None = None,
) -> ManagedExecResult:
    state_dir = _prepare_state_directory(state_path, create=False)
    runtime, capability = _load_running(state_dir)
    with ControlSession.connect(
        _endpoint(runtime), capability, response_timeout
    ) as session:
        result = session.exec(
            arguments,
            timeout_ms=timeout_ms,
            response_timeout=response_timeout,
            output=output,
        )
        metrics = session.metrics(response_timeout)
        _write_json(state_dir / "resources.json", metrics)
        EventLog(state_dir / "events.jsonl", state_dir.name).emit(
            "workload.exited",
            code=result.returncode,
            category=result.category,
            resources=metrics,
        )
        return result


def cleanup_endpoint(runtime: dict[str, Any]) -> None:
    if os.name != "nt":
        _endpoint(runtime).unlink(missing_ok=True)
        directory = runtime.get("control_directory")
        if directory:
            path = Path(str(directory))
            if (
                path.parent != Path("/tmp")
                or not path.name.startswith("nvxctl-")
                or path.is_symlink()
            ):
                raise ScriptError("invalid private control directory")
            shutil.rmtree(path)


def stop(state_path: Path, timeout: float) -> dict[str, Any]:
    state_dir = _prepare_state_directory(state_path, create=False)
    runtime, capability = _load_running(state_dir)
    pid = int(runtime["pid"])
    forced = False

    def signal_owned(sig: int) -> None:
        if not process_running(pid):
            return

        if os.name == "nt":
            inspected = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
                ],
                capture_output=True,
                text=True,
            )
        else:
            inspected = subprocess.run(
                ["ps", "-o", "args=", "-p", str(pid)],
                capture_output=True,
                text=True,
            )
        if inspected.returncode or str(_endpoint(runtime)) not in inspected.stdout:
            raise ScriptError(
                "cannot confirm ownership for forced sandbox teardown"
            ) from None
        os.kill(pid, sig)

    try:
        with ControlSession.connect(_endpoint(runtime), capability, timeout) as session:
            session.stop(timeout)
    except (ScriptError, TimeoutError, ConnectionError, OSError):
        signal_owned(signal.SIGTERM)
        forced = True
    deadline = time.monotonic() + timeout
    while process_running(pid):
        if time.monotonic() >= deadline:
            if not forced:
                signal_owned(signal.SIGTERM)
                forced = True
                deadline = time.monotonic() + 5
                continue
            signal_owned(signal.SIGKILL if os.name != "nt" else signal.SIGTERM)
            deadline = time.monotonic() + 5
            while process_running(pid) and time.monotonic() < deadline:
                time.sleep(0.025)
            if process_running(pid):
                raise TimeoutError("OpenVMM did not terminate after forced stop")
        time.sleep(0.025)
    try:
        if forced and not (state_dir / OUTCOME_NAME).is_file():
            _write_json(
                state_dir / OUTCOME_NAME,
                {
                    "schema_version": 1,
                    "instance_id": state_dir.name,
                    "backend": json.loads((state_dir / CONFIG_NAME).read_bytes())[
                        "hypervisor"
                    ],
                    "outcome": {
                        "operation": "stop",
                        "category": "forced-termination",
                        "status_code": 143,
                    },
                    "network_policy": {"status": "unknown"},
                    "teardown": {
                        "guest_workload_stopped": False,
                        "vm_stopped": True,
                        "openvmm_process_terminated": True,
                        "virtiofs_released": True,
                        "network_released": True,
                        "temporary_storage_removed": False,
                        "control_channels_closed": True,
                    },
                },
            )
        outcome = _read_openvmm_outcome(state_dir / OUTCOME_NAME)
        EventLog(state_dir / "events.jsonl", state_dir.name).emit(
            "run.exited",
            code=outcome["outcome"]["status_code"],
            category=outcome["outcome"]["category"],
        )
    finally:
        (state_dir / RUNTIME_NAME).unlink(missing_ok=True)
        (state_dir / CAPABILITY_NAME).unlink(missing_ok=True)
        cleanup_endpoint(runtime)
    return outcome


def deprovision(state_path: Path) -> None:
    state_dir = _prepare_state_directory(state_path, create=False)
    runtime_path = state_dir / RUNTIME_NAME
    if runtime_path.is_file():
        runtime = _read_json(runtime_path, "sandbox runtime state")
        try:
            running = process_running(int(runtime["pid"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ScriptError(
                "sandbox runtime state has an invalid process ID"
            ) from error
        if running:
            raise ScriptError("sandbox must be stopped before deprovision")
    owned = {
        RUNTIME_NAME,
        CAPABILITY_NAME,
        CONTROL_SOCKET_NAME,
        OUTCOME_NAME,
        LOG_NAME,
        CONFIG_NAME,
        "events.jsonl",
        "events.lock",
        "resources.json",
    }
    unknown = tuple(path for path in state_dir.iterdir() if path.name not in owned)
    if unknown:
        raise ScriptError(
            "sandbox state directory contains files not owned by NVX: "
            + ", ".join(path.name for path in unknown)
        )
    for name in owned:
        (state_dir / name).unlink(missing_ok=True)
    state_dir.rmdir()
