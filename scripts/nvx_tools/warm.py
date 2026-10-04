"""Integrity-bound managed workload-start templates and repaired private clones."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, cast

from . import receipt
from . import sandbox_lifecycle as lifecycle
from .build_constants import BuildConstants
from .common import ScriptError, sha256_file
from .control_session import ControlSession
from .host import default_backend
from .image import ImageCache, canonical, digest, ensure
from .image_run import workload_argv
from .policy import Policy, resolve
from .sandbox import SandboxLaunch, SandboxLayer
from .snapshot import verify_snapshot


def _repl(
    process: subprocess.Popen[bytes],
    state: Path,
    command: str,
    marker: bytes,
    timeout: float,
) -> None:
    if process.stdin is None:
        raise ScriptError("warm source has no snapshot control pipe")
    offset = (state / "openvmm.log").stat().st_size
    process.stdin.write((command + "\n").encode())
    process.stdin.flush()
    deadline = time.monotonic() + timeout
    while True:
        with (state / "openvmm.log").open("rb") as log:
            log.seek(offset)
            output = log.read()
        if marker in output:
            return
        if b"error:" in output or process.poll() is not None:
            raise ScriptError("warm snapshot REPL failed; inspect source/openvmm.log")
        if time.monotonic() >= deadline:
            raise ScriptError("warm snapshot publication timed out")
        time.sleep(0.01)


def capture(
    image: str,
    backend: str,
    output: Path,
    policy: Policy,
    *,
    memory_mib: int = 512,
    timeout: float = 90,
    prelude: tuple[str, ...] = (),
    repair: bool = True,
    secret_args: argparse.Namespace | None = None,
    runtime: str = "auto",
    host_loopback: str | None = "deny",
    cancel: threading.Event | None = None,
) -> Path:
    if output.exists() or output.is_symlink():
        raise ScriptError("warm template output already exists")
    if not repair and policy.profile != "risky":
        raise ScriptError("disabled restore repair requires the risky test control")
    output = output.resolve()
    output.mkdir(mode=0o700, parents=True)
    cache = ImageCache()
    value, manifest = ensure(image)
    if runtime not in ("auto", "python", "dispatcher"):
        raise ScriptError("warm runtime must be auto, python, or dispatcher")
    python_runtime = runtime == "python" or (
        runtime == "auto"
        and any(
            str(item).startswith("PYTHON_VERSION=")
            for item in manifest["config"].get("Env", ())
        )
    )
    runtime_argv: tuple[str, ...] = ()
    if python_runtime:
        shim = (
            BuildConstants.REPO_ROOT / "guest/common/nvx-warm-python.py"
        ).read_text()
        runtime_argv = workload_argv(manifest, ("python3", "-u", "-c", shim))
    owner = "warm-" + uuid.uuid4().hex
    scratch = cache.acquire(owner, value)
    state = output / "source"
    process: subprocess.Popen[bytes] | None = None
    retained = False
    proxy_process: subprocess.Popen[bytes] | None = None
    proxy_environment: tuple[str, ...] = ()
    proxy_endpoint: str | None = None
    credential_metadata: dict[str, Any] | None = None
    finished = threading.Event()

    def watch_cancel() -> None:
        while not finished.wait(0.05):
            if (
                cancel is not None
                and cancel.is_set()
                and process is not None
                and process.poll() is None
            ):
                process.terminate()
                return

    watcher = threading.Thread(target=watch_cancel, daemon=True)
    watcher.start()
    try:
        if cancel is not None and cancel.is_set():
            raise ScriptError("warm capture cancelled")
        if secret_args is not None:
            from . import secrets as host_secrets

            scopes, bindings = host_secrets.bindings(secret_args)
            proxy_process, capability, port = host_secrets.start(
                state, secret_args, scopes, bindings
            )
            proxy_endpoint = f"192.168.127.1:{port}"
            proxy_environment = (
                f"NVX_PROXY_URL=http://{proxy_endpoint}",
                f"NVX_PROXY_CAPABILITY={capability}",
            )
            credential_metadata = {
                "names": [item.name for item in bindings],
                "scopes": [f"{host}:{port}" for host, port in sorted(scopes)],
                "restore": "requires fresh host proxy binding",
            }
        launch = SandboxLaunch(
            tuple(
                SandboxLayer(
                    item["role"], cache.verify_blob(item["digest"]), item["uuid"]
                )
                for item in manifest["layers"]
            ),
            scratch,
            profile=policy.profile,
            seccomp=policy.seccomp,
            caps=policy.caps,
            device_filter=policy.device_filter,
            masked_paths=policy.masked_paths,
            no_new_privs=policy.no_new_privs,
            workload_identity=(policy.uid, policy.gid),
            memory_max=policy.memory_max,
            pids_max=policy.pids_max,
        )
        lifecycle.provision(
            state,
            launch,
            hypervisor=backend,
            memory_mib=memory_mib,
            net="192.168.127.0/24",
            network_profile="portable",
            network_egress=policy.egress,
            network_ingress="deny",
            network_egress_allow=policy.allow,
            network_egress_deny=(),
            host_loopback=host_loopback,
            network_proxy=proxy_endpoint,
            host_loopback_forward=(),
            cmdline="",
        )
        (state / "image.json").write_bytes(
            canonical({"ref": image, "manifest_digest": value, "manifest": manifest})
        )
        (state / "policy.json").write_bytes(canonical(policy.document()))
        (state / "versions.json").write_bytes(
            canonical(receipt.versions(manifest["architecture"]))
        )
        microvm_snapshot = platform.machine().lower() not in ("aarch64", "arm64")
        snapshot = output / "snapshot"
        process = lifecycle.start(
            state,
            timeout,
            memory_backing=output / "source-ram.bin",
            keep_stdin=True,
            snapshot_destination=snapshot if microvm_snapshot else None,
        )
        command = workload_argv(manifest, prelude or ("/bin/true",), proxy_environment)
        result = lifecycle.exec_workload(
            state, command, timeout_ms=60_000, response_timeout=timeout
        )
        if result.returncode or result.category != "exit":
            raise ScriptError("warm runtime initialization failed")
        (state / "prelude.stdout").write_bytes(result.stdout)
        (state / "prelude.stderr").write_bytes(result.stderr)
        running = json.loads((state / "runtime.json").read_bytes())
        capability = (state / "control.capability").read_bytes()
        with ControlSession.connect(
            Path(running["control_endpoint"]), capability, timeout
        ) as session:
            source_generation = session.generation(timeout)
            session.warm(
                timeout,
                repair=repair,
                runtime=runtime_argv,
                microvm_snapshot=microvm_snapshot,
            )
        if microvm_snapshot:
            if process.wait(timeout=timeout):
                raise lifecycle.startup_failure(
                    state / "openvmm.log", process.returncode
                )
        else:
            _repl(
                process,
                state,
                "snap " + shlex.quote(str(snapshot)),
                b"snapshot saved",
                timeout,
            )
        report = verify_snapshot(snapshot)
        if report.version != 6:
            raise ScriptError("warm templates require integrity-bearing snapshot v6")
        shutil.copyfile(scratch, output / "scratch.ext4")
        os.chmod(output / "scratch.ext4", 0o400)
        if not microvm_snapshot:
            assert process.stdin is not None
            process.stdin.write(b"quit\n")
            process.stdin.flush()
            process.wait(timeout=timeout)
        lifecycle.cleanup_endpoint(running)
        for name in ("runtime.json", "control.capability", "control.sock"):
            (state / name).unlink(missing_ok=True)
        if proxy_process is not None:
            proxy_process.wait(timeout=10)
            (state / "proxy.capability").unlink(missing_ok=True)
        (output / "source-ram.bin").unlink(missing_ok=True)
        files = {
            name: sha256_file(output / name)
            for name in (
                "scratch.ext4",
                "source/image.json",
                "source/policy.json",
                "source/versions.json",
                "snapshot/manifest.bin",
                "snapshot/state.bin",
                "snapshot/memory.bin",
            )
        }
        if microvm_snapshot:
            files["snapshot/scratch.img"] = sha256_file(snapshot / "scratch.img")
        document: dict[str, Any] = {
            "warm_version": 1,
            "owner": owner,
            "image": value,
            "backend": backend,
            "architecture": report.architecture,
            "source_generation": source_generation,
            "files": files,
            "config": json.loads((state / "config.json").read_bytes()),
            "policy": policy.document(),
            "prelude": list(prelude),
            "repair": repair,
            "runtime_checkpoint": "single-threaded Python fork server at command barrier"
            if python_runtime
            else "single-threaded managed dispatcher",
            "runtime": "python" if python_runtime else "dispatcher",
        }
        if credential_metadata is not None:
            document["credential_proxy"] = credential_metadata
        document["sha256"] = digest(canonical(document))
        (output / "warm.json").write_bytes(canonical(document))
        if cancel is not None and cancel.is_set():
            raise ScriptError("warm capture cancelled")
        cache.hold(owner + "-snapshot", value, "snapshot")
        retained = True
        return output
    finally:
        finished.set()
        watcher.join(timeout=1)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        cache.release(owner)
        if proxy_process is not None and proxy_process.poll() is None:
            proxy_process.terminate()
            proxy_process.wait(timeout=10)
        (state / "proxy.capability").unlink(missing_ok=True)
        if not retained:
            (output / "source-ram.bin").unlink(missing_ok=True)
            (output / "scratch.ext4").unlink(missing_ok=True)
            if (output / "snapshot").is_dir():
                shutil.rmtree(output / "snapshot")
            for name in ("runtime.json", "control.capability", "control.sock"):
                (state / name).unlink(missing_ok=True)


def admit(template: Path, backend: str | None = None) -> dict[str, Any]:
    if template.is_symlink():
        raise ScriptError("warm template must be a plain directory")
    document = cast(dict[str, Any], json.loads((template / "warm.json").read_bytes()))
    unsigned = {key: value for key, value in document.items() if key != "sha256"}
    if document.get("warm_version") != 1 or document.get("sha256") != digest(
        canonical(unsigned)
    ):
        raise ScriptError("warm template manifest failed integrity verification")
    if backend is not None and backend != document["backend"]:
        raise ScriptError(
            "warm templates are bound to their architecture and hypervisor"
        )
    required = {
        "scratch.ext4",
        "source/image.json",
        "source/policy.json",
        "source/versions.json",
        "snapshot/manifest.bin",
        "snapshot/state.bin",
        "snapshot/memory.bin",
    }
    if document["architecture"] == "x86_64":
        required.add("snapshot/scratch.img")
    if set(document["files"]) != required:
        raise ScriptError("warm template has an invalid inventory")
    for name, expected in document["files"].items():
        path = template / name
        if path.is_symlink() or not path.is_file() or sha256_file(path) != expected:
            raise ScriptError(
                "warm template file failed integrity verification: " + name
            )
    cache = ImageCache()
    _, image = cache.resolve(document["image"])
    for item in image["layers"]:
        cache.verify_blob(item["digest"])
    report = verify_snapshot(template / "snapshot")
    if report.architecture != document["architecture"]:
        raise ScriptError("warm template architecture differs from its snapshot")
    return document


def clone(template: Path, *, timeout: float = 90) -> Path:
    document = admit(template)
    if document.get("credential_proxy"):
        raise ScriptError(
            "credential snapshots exclude host proxy state; a fresh host binding is required"
        )
    cache = ImageCache()
    identifier = uuid.uuid4().hex
    scratch = cache.acquire(identifier, document["image"])
    state = cache.root / "instances" / identifier
    try:
        shutil.copyfile(template / "scratch.ext4", scratch)
        state.mkdir(mode=0o700, parents=True)
        config = {**document["config"], "scratch": str(scratch)}
        lifecycle.replace_json(state / "config.json", config)
        for name in ("image.json", "policy.json", "versions.json"):
            shutil.copyfile(template / "source" / name, state / name)
        (state / "argv.json").write_bytes(b"[]")
        lifecycle.start(state, timeout, restore=template / "snapshot")
        (state / "warm-source.json").write_bytes(
            canonical(
                {"template": str(template.resolve()), "sha256": document["sha256"]}
            )
        )
        return state
    except BaseException:
        cache.release(identifier)
        raise


def command(args: argparse.Namespace) -> None:
    policy = resolve({"profile": args.profile, "allow": args.egress_allow})
    path = args.output or ImageCache().root / "warm" / uuid.uuid4().hex
    prelude = tuple(args.prelude)
    if prelude[:1] == ("--",):
        prelude = prelude[1:]
    print(
        capture(
            args.image,
            args.backend,
            path,
            policy,
            memory_mib=args.memory_mib,
            timeout=args.timeout,
            prelude=prelude,
            runtime=args.runtime,
            host_loopback=args.host_loopback,
        )
    )


def configure_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "warm", help="capture a managed workload-start template"
    )
    parser.set_defaults(handler=command)
    parser.add_argument("--image", required=True)
    parser.add_argument(
        "--runtime", choices=("auto", "python", "dispatcher"), default="auto"
    )
    parser.add_argument("--host-loopback", choices=("allow", "deny"), default="deny")
    parser.add_argument(
        "--backend", choices=("hvf", "kvm", "mshv", "whp"), default=default_backend()
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--profile", choices=("default", "ci", "risky"), default="default"
    )
    parser.add_argument("--egress-allow", action="append", default=[])
    parser.add_argument("--memory-mib", type=int, default=512)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("prelude", nargs=argparse.REMAINDER)
