"""Image workloads use the authenticated managed path for arbitrary argv."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, cast

from . import receipt, sandbox_lifecycle
from .common import ScriptError
from .control_session import ManagedExecResult
from .events import EventLog, redact
from .image import ImageCache, ensure
from .output import write_stream
from .policy import for_launch
from .sandbox import SandboxLaunch, SandboxLayer, SandboxMount
from .workspace import (
    MAX_OUTPUT_BYTES,
    collect_outputs,
    output_destination,
    parse_workspace,
    unpack_outputs,
)


def workload_argv(
    manifest: dict[str, Any],
    command: tuple[str, ...],
    environment_extra: tuple[str, ...] = (),
) -> tuple[str, ...]:
    config = cast(dict[str, Any], manifest["config"])
    selected = command or tuple(config.get("Entrypoint") or ()) + tuple(
        config.get("Cmd") or ()
    )
    if not selected:
        raise ScriptError("image has no default command; supply -- COMMAND [ARG...]")
    environment = tuple(config.get("Env") or ())
    if any("=" not in value or "\0" in value for value in environment):
        raise ScriptError("image has invalid environment defaults")
    return (
        "/bin/sh",
        "-c",
        'cd "$1"; shift; exec env "$@"',
        "nvx",
        config.get("WorkingDir") or "/",
        *environment,
        *environment_extra,
        *selected,
    )


def run_image(
    args: argparse.Namespace, backend: str, *, sandbox: bool = False
) -> ManagedExecResult:
    instance_id_file = getattr(args, "instance_id_file", None)
    if instance_id_file is not None:
        sandbox_lifecycle.validate_outcome_destination(instance_id_file)
    cache = ImageCache()
    value, manifest = ensure(args.image, quiet=instance_id_file is not None)
    from . import secrets as host_secrets

    secret_specs = getattr(args, "secret", ())
    scopes, selected = (
        host_secrets.bindings(args)
        if secret_specs
        else (frozenset[tuple[str, int]](), ())
    )
    policy_args = argparse.Namespace(**vars(args))
    if secret_specs:
        policy_args.network_egress_allow = []
    policy = for_launch(policy_args)
    instance = uuid.uuid4().hex
    state = cache.root / "instances" / instance
    layers = tuple(
        SandboxLayer(layer["role"], cache.verify_blob(layer["digest"]), layer["uuid"])
        for layer in manifest["layers"]
    )
    mount = (
        None
        if args.mount is None
        else SandboxMount.parse(
            args.mount, tuple(str(path) for path in args.mount_deny)
        )
    )
    workspace = getattr(args, "workspace", None)
    if workspace:
        if mount is not None:
            raise ScriptError("use one of --workspace and --mount")
        mount = parse_workspace(workspace)
    output = getattr(args, "out", None)
    keep_alive = getattr(args, "keep_alive", False)
    if output is not None:
        output_destination(output)
        if keep_alive:
            raise ScriptError("--out requires a completed run; omit --keep-alive")
        if mount is not None and (
            mount.guest_target == "/out" or mount.guest_target.startswith("/out/")
        ):
            raise ScriptError("--out requires private /out without a workspace mount")
    for name in ("outcome_report", "receipt"):
        destination = getattr(args, name, None)
        if destination is not None:
            sandbox_lifecycle.validate_outcome_destination(destination)
    proxy_log = getattr(args, "proxy_log", None)
    if proxy_log is not None:
        if not secret_specs:
            raise ScriptError("--proxy-log requires --secret")
        sandbox_lifecycle.validate_outcome_destination(proxy_log)
    memory = args.memory_mib or (
        1024 if platform.machine().lower() in ("arm64", "aarch64") else 256
    )
    supplied = tuple(args.sandbox_arg) if sandbox else tuple(args.workload)
    if supplied and supplied[0] == "--":
        supplied = supplied[1:]
    if sandbox and (supplied or args.entrypoint != "/bin/sh"):
        supplied = (args.entrypoint, *supplied)
    environment_extra = tuple(getattr(args, "env", ()))
    if any(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", item, re.S) is None or "\0" in item
        for item in environment_extra
    ):
        raise ScriptError("--env requires NAME=VALUE without NUL")
    command = workload_argv(manifest, supplied, environment_extra)
    scratch = cache.acquire(instance, value)
    launch = SandboxLaunch(
        layers,
        scratch,
        mount=mount,
        profile=policy.profile,
        seccomp=policy.seccomp,
        caps=policy.caps,
        device_filter=policy.device_filter,
        masked_paths=policy.masked_paths,
        no_new_privs=policy.no_new_privs,
        workload_identity=(policy.uid, policy.gid),
        pids_max=policy.pids_max,
        memory_max=policy.memory_max,
    )
    started = False
    retained = False
    output_stage: tempfile.TemporaryDirectory[str] | None = None
    proxy_process: subprocess.Popen[bytes] | None = None
    redactions = tuple(item.value for item in selected)
    try:
        proxy_endpoint = args.network_proxy
        net = args.net or "192.168.127.0/24"
        if secret_specs:
            if (
                proxy_endpoint is not None
                or args.host_loopback_forward
                or args.host_loopback == "allow"
            ):
                raise ScriptError(
                    "credential proxy requires its exclusive gateway endpoint"
                )
            proxy_process, capability, port = host_secrets.start(
                state, args, scopes, selected
            )
            gateway = str(ipaddress.ip_network(net).network_address + 1)
            proxy_endpoint = f"{gateway}:{port}"
            command = workload_argv(
                manifest,
                supplied,
                (
                    *environment_extra,
                    f"NVX_PROXY_URL=http://{gateway}:{port}",
                    f"NVX_PROXY_CAPABILITY={capability}",
                ),
            )
            redactions += (capability,)
        policy_document = policy.document()
        policy_document["mounts"] = (
            []
            if mount is None
            else [{"guest": mount.guest_target, "access": mount.access}]
        )
        if secret_specs:
            policy_document["credential_proxy"] = {
                "scopes": [f"{host}:{port}" for host, port in sorted(scopes)],
                "names": [item.name for item in selected],
            }
        sandbox_lifecycle.provision(
            state,
            launch,
            hypervisor=backend,
            memory_mib=memory,
            net=net,
            network_profile=args.network_profile or "portable",
            network_egress=policy.egress,
            network_ingress=args.network_ingress or "deny",
            network_egress_allow=policy.allow,
            network_egress_deny=tuple(args.network_egress_deny),
            host_loopback=args.host_loopback,
            network_proxy=proxy_endpoint,
            host_loopback_forward=tuple(args.host_loopback_forward),
            cmdline=args.cmdline,
        )
        (state / "policy.json").write_text(
            json.dumps(policy_document, sort_keys=True) + "\n"
        )
        (state / "argv.json").write_text(
            json.dumps(redact(command, secrets=redactions)) + "\n"
        )
        (state / "image.json").write_text(
            json.dumps(
                {"ref": args.image, "manifest_digest": value, "manifest": manifest},
                sort_keys=True,
            )
            + "\n"
        )
        if instance_id_file is not None:
            sandbox_lifecycle.publish_json(
                instance_id_file, {"version": 1, "instance_id": instance}
            )
        else:
            print(f"NVX-ID: {instance}", file=sys.stderr)
        (state / "versions.json").write_text(
            json.dumps(receipt.versions(manifest["architecture"]), sort_keys=True)
            + "\n"
        )
        timeout = getattr(args, "timeout", 60.0)
        sandbox_lifecycle.start(state, timeout)
        started = True
        result = sandbox_lifecycle.exec_workload(
            state,
            command,
            timeout_ms=policy.wall_timeout_ms,
            response_timeout=max(timeout, policy.wall_timeout_ms / 1000 + 10),
            output=write_stream if getattr(args, "stream", False) else None,
        )
        (state / "last_exec.json").write_text(
            json.dumps({"category": result.category, "status_code": result.returncode})
            + "\n"
        )
        if not getattr(args, "stream", False):
            write_stream("stdout", result.stdout)
            write_stream("stderr", result.stderr)
        if args.outcome_report:
            sandbox_lifecycle.write_exec_outcome(args.outcome_report, result)
        flows = receipt.parse_flows(state / "flows.jsonl")
        events = EventLog(state / "events.jsonl", instance)
        for flow in flows["flows"]:
            if flow["verdict"] == "denied":
                events.emit(
                    "network.denied",
                    dst=flow["dst"],
                    port=flow["dst_port"],
                    proto=flow["proto"],
                )
        if result.category == "timeout":
            events.emit("resource.denied", resource="wall")
        resources = json.loads((state / "resources.json").read_bytes())
        for resource, key in (("pids", "pids_denials"), ("memory", "memory_oom_kills")):
            if resources.get(key, 0):
                events.emit("resource.denied", resource=resource)
        if keep_alive:
            sandbox_lifecycle.replace_json(
                state / "finish.json",
                {
                    "receipt": str(args.receipt.resolve()) if args.receipt else None,
                    "signing_key": str(args.receipt_signing_key.resolve())
                    if args.receipt_signing_key
                    else None,
                    "proxy_log": str(proxy_log.resolve()) if proxy_log else None,
                },
            )
            retained = True
            return result
        if output is not None and result.returncode == 0 and result.category == "exit":
            from .registry import download

            output_stage = tempfile.TemporaryDirectory(prefix="nvx-output-")
            archive = Path(output_stage.name) / "output.tar"
            guest_archive = "/tmp/.nvx-output-" + uuid.uuid4().hex + ".tar"
            packed = sandbox_lifecycle.exec_workload(
                state,
                (
                    "/bin/sh",
                    "-c",
                    'umask 077; tar -cf "$1" -C /out .',
                    "nvx",
                    guest_archive,
                ),
                timeout_ms=policy.wall_timeout_ms,
                response_timeout=max(timeout, policy.wall_timeout_ms / 1000 + 10),
            )
            if packed.returncode:
                raise ScriptError("guest output archive failed")
            download(
                state, guest_archive, archive, maximum=MAX_OUTPUT_BYTES + (16 << 20)
            )
            sandbox_lifecycle.replace_json(state / "resources.json", resources)
        outcome = sandbox_lifecycle.stop(state, timeout)
        started = False
        if proxy_process is not None:
            proxy_process.wait(timeout=40)
            if proxy_log is not None:
                with proxy_log.open("xb") as log:
                    log.write((state / "proxy.jsonl").read_bytes())
                os.chmod(proxy_log, 0o600)
        if output_stage is not None and output is not None:
            extracted = Path(output_stage.name) / "extracted"
            extracted.mkdir(mode=0o700)
            unpack_outputs(Path(output_stage.name) / "output.tar", extracted)
            collect_outputs(extracted, output, clean=True)
        document = receipt.build(
            state,
            policy_document,
            command,
            {
                "workload": {
                    "category": result.category,
                    "status_code": result.returncode,
                },
                "teardown": outcome,
            },
            secrets=redactions,
        )
        signing_key = getattr(args, "receipt_signing_key", None)
        if signing_key is not None:
            receipt.sign(document, signing_key)
        receipt.publish(state / "receipt.json", document)
        destination = getattr(args, "receipt", None)
        if destination is not None:
            receipt.publish(destination, document)
        # Keep host evidence at the printed instance ID; remove only capabilities
        # and scratch. The logs remain available for inspect/bundle operations.
        return result
    finally:
        if started and not retained:
            sandbox_lifecycle.stop(state, getattr(args, "timeout", 60.0))
        if not retained:
            cache.release(instance)
            if proxy_process is not None and proxy_process.poll() is None:
                proxy_process.terminate()
                proxy_process.wait(timeout=10)
            (state / "proxy.capability").unlink(missing_ok=True)
        if output_stage is not None:
            output_stage.cleanup()
        if state.exists():
            os.chmod(state, 0o700)


def command_image_run(
    args: argparse.Namespace, backend: str, *, sandbox: bool = False
) -> None:
    if not sandbox:
        for name in (
            "kernel",
            "initrd",
            "restore_snapshot",
            "save_snapshot",
            "memory_backing_file",
            "disk",
            "memory_capacity_mib",
            "restore_processors",
            "restore_memory_mib",
        ):
            if getattr(args, name, None) is not None:
                raise ScriptError(
                    f"--{name.replace('_', '-')} is unsupported with --image; use the explicit sandbox snapshot API"
                )
        if (
            args.processors != 1
            or args.share
            or args.serve
            or getattr(args, "virtio_blk", None)
            or args.virtio_net
        ):
            raise ScriptError(
                "--image uses its fixed one-vCPU managed device inventory"
            )
    if args.dry_run:
        value, manifest = ImageCache().resolve(args.image)
        supplied = tuple(args.sandbox_arg) if sandbox else tuple(args.workload)
        if supplied and supplied[0] == "--":
            supplied = supplied[1:]
        print(
            json.dumps(
                {
                    "image": value,
                    "backend": backend,
                    "policy": for_launch(args).document(),
                    "argv": workload_argv(
                        manifest,
                        supplied,
                    ),
                },
                sort_keys=True,
            )
        )
        return
    result = run_image(args, backend, sandbox=sandbox)
    raise SystemExit(result.returncode)
