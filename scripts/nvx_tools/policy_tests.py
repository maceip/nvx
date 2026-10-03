"""Bounded guest probes with explicit controls; verdicts come from guest results."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, cast

from .build_constants import BuildConstants
from .common import ScriptError
from .image import ImageCache, ensure

SCENARIOS = ("seccomp-profile", "egress-fast-fail", "resource-caps", "device-policy")
SECCOMP = r"""
import ctypes, errno, json, os, platform, signal, socket, threading
status=dict(line.split(':',1) for line in open('/proc/self/status') if ':' in line)
c=ctypes.CDLL(None,use_errno=True)
r=c.syscall(141 if platform.machine()=='aarch64' else 140,0,0)
e=ctypes.get_errno()
record=dict(uid=os.getuid(),caps=status['CapEff'].strip(),nnp=int(status['NoNewPrivs']),seccomp=int(status['Seccomp']),syscall_result=r,errno=e)
record['namespace_clones']={}
record['namespace_filter_errno']={}
for name,flag in [('user',0x10000000),('mount',0x20000),('net',0x40000000),('pid',0x20000000)]:
 ctypes.set_errno(0)
 child=c.syscall(220 if platform.machine()=='aarch64' else 56,flag|signal.SIGCHLD,0,0,0,0)
 error=ctypes.get_errno()
 if child==0: os._exit(0)
 if child>0: os.waitpid(child,0)
 record['namespace_clones'][name]=dict(created=child>0,errno=error)
 # CLONE_THREAD without CLONE_SIGHAND is EINVAL before kernel permission
 # checks. A flag-filtered call is EPERM instead, even where chroot itself
 # prevents a valid CLONE_NEWUSER call in the unconfined control.
 ctypes.set_errno(0)
 c.syscall(220 if platform.machine()=='aarch64' else 56,flag|0x10000|signal.SIGCHLD,0,0,0,0)
 record['namespace_filter_errno'][name]=ctypes.get_errno()
ctypes.set_errno(0)
c.syscall(220 if platform.machine()=='aarch64' else 56,0x10000|signal.SIGCHLD,0,0,0,0)
record['ordinary_invalid_clone_errno']=ctypes.get_errno()
args=(ctypes.c_ulonglong*11)();args[4]=signal.SIGCHLD
ctypes.set_errno(0); child=c.syscall(435,ctypes.byref(args),ctypes.sizeof(args));error=ctypes.get_errno()
if child==0: os._exit(0)
if child>0: os.waitpid(child,0)
record['clone3']=dict(created=child>0,errno=error)
record['socket_domains']={}
for domain in [socket.AF_UNIX,socket.AF_INET,socket.AF_INET6,socket.AF_NETLINK,socket.AF_PACKET]:
 try:
  s=socket.socket(domain,socket.SOCK_DGRAM);s.close();record['socket_domains'][str(int(domain))]=0
 except OSError as error:record['socket_domains'][str(int(domain))]=error.errno
seen=[];thread=threading.Thread(target=lambda:seen.append('thread'));thread.start();thread.join()
child=os.fork()
if child==0:os._exit(0)
os.waitpid(child,0);record['ordinary_fork_thread_ok']=seen==['thread']
print(json.dumps(record))
"""
NETWORK = r"""
import errno, json, socket, time
s=socket.socket();s.settimeout(3)
start=time.monotonic()
try:
    s.connect(('192.0.2.1',443)); verdict='connected'; error=0
except OSError as e:
    verdict='timeout' if isinstance(e,TimeoutError) else 'refused'; error=e.errno
finally:
    s.close()
print(json.dumps(dict(verdict=verdict,errno=error,latency_ms=(time.monotonic()-start)*1000)))
"""
RESOURCES = r"""
import errno, json, os
r,w=os.pipe(); children=[]; error=0
try:
    for i in range(260):
        try: pid=os.fork()
        except OSError as e: error=e.errno; break
        if pid==0:
            os.close(w); os.read(r,1); os._exit(0)
        children.append(pid)
finally:
    os.close(w);os.close(r)
    for child in children: os.waitpid(child,0)
print(json.dumps(dict(children=len(children),errno=error,heartbeat='alive')))
"""

MEMORY = r"""
import json,os
child=os.fork()
if child==0:
    allocations=[bytearray(1<<20) for _ in range(320)]
    os._exit(0 if len(allocations)==320 else 1)
_,status=os.waitpid(child,0)
print(json.dumps(dict(signal=os.WTERMSIG(status) if os.WIFSIGNALED(status) else 0,
    exit=os.WEXITSTATUS(status) if os.WIFEXITED(status) else None,heartbeat='alive')))
"""
WALL = r"""
import json,time
print(json.dumps(dict(phase='started')),flush=True)
time.sleep(2)
print(json.dumps(dict(phase='completed')),flush=True)
"""

STANDARD_DEVICES = r"""
import errno,fcntl,json,os,pty,signal,stat,termios
result={'permitted':{}}
for name in ['null','zero','urandom','full']:
 try:
  fd=os.open('/dev/'+name,os.O_RDWR)
  try:
   if name=='null': assert os.write(fd,b'null')==4
   if name=='zero': assert os.read(fd,8)==b'\0'*8
   if name=='urandom': assert len(os.read(fd,8))==8
   if name=='full':
    try:os.write(fd,b'x');raise AssertionError('/dev/full accepted a write')
    except OSError as error:assert error.errno==errno.ENOSPC
   result['permitted'][name]='ok'
  finally:os.close(fd)
 except OSError as error:result['permitted'][name]=error.errno
r,w=os.pipe();child=os.fork()
if child==0:
 os.close(r);record={}
 try:
  signal.signal(signal.SIGHUP,signal.SIG_IGN)
  os.setsid();master,slave=pty.openpty()
  path=os.ttyname(slave);fd=os.open(path,os.O_RDWR);os.close(fd);record['pts']='ok'
  fcntl.ioctl(slave,termios.TIOCSCTTY,0)
  fd=os.open('/dev/tty',os.O_RDWR);os.close(fd);record['tty']='ok'
  os.close(master);os.close(slave)
 except OSError as error:record['error']=error.errno
 os.write(w,json.dumps(record).encode());os.close(w);os._exit(0)
os.close(w);result['permitted'].update(json.loads(os.read(r,4096)));os.close(r);os.waitpid(child,0)
"""
DEVICES = (
    STANDARD_DEVICES
    + r"""
for name,major,minor in [('null',1,3),('mem',1,1),('kmsg',1,11),('disk',253,0)]:
    path='/dev/shm/nvx-probe-'+name
    try:
        os.mknod(path,(stat.S_IFBLK if name=='disk' else stat.S_IFCHR)|0o666,os.makedev(major,minor))
        result[name]='created';os.unlink(path)
    except OSError as e:result[name]=e.errno
try:
    fd=os.open('/proc/sysrq-trigger',os.O_WRONLY);os.close(fd);result['masked']='open'
except OSError as e:result['masked']=e.errno
status=dict(line.split(':',1) for line in open('/proc/self/status') if ':' in line)
result.update(caps=status['CapEff'].strip(),nnp=int(status['NoNewPrivs']))
print(json.dumps(result))
"""
)


def execute(
    backend: str,
    script: str,
    output: Path,
    label: str,
    timeout: float,
    *,
    profile: str | None = "default",
    legacy_drop: bool = False,
    extra: tuple[str, ...] = (),
    expected_status: int = 0,
) -> dict[str, Any]:
    env = os.environ.copy()
    env.pop("NVX_NETWORK_LEGACY_DROP", None)
    if legacy_drop:
        env["NVX_NETWORK_LEGACY_DROP"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            str(BuildConstants.REPO_ROOT / "scripts/nvx.py"),
            "run",
            "--image",
            "python:3.12-slim",
            "--hypervisor",
            backend,
            *(["--profile", profile] if profile is not None else []),
            *extra,
            "--",
            "python",
            "-c",
            script,
        ],
        capture_output=True,
        timeout=timeout * 3,
        env=env,
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{label}.stdout").write_bytes(result.stdout)
    (output / f"{label}.stderr").write_bytes(result.stderr)
    if result.returncode != expected_status:
        raise ScriptError(
            f"{label} failed with status {result.returncode}; see {output}"
        )
    try:
        value: object = json.loads(result.stdout.splitlines()[-1])
    except ValueError as error:
        raise ScriptError(f"{label} did not return a probe record") from error
    if not isinstance(value, dict):
        raise ScriptError(f"{label} probe record is not an object")
    return cast(dict[str, Any], value)


def validate(scenario: str, protected: dict[str, Any], control: dict[str, Any]) -> None:
    if scenario == "seccomp-profile":
        ok = (
            protected["uid"] == 65534
            and int(protected["caps"], 16) == 0
            and protected["nnp"] == 1
            and protected["seccomp"] == 2
            and protected["syscall_result"] == -1
            and protected["errno"] == 1
            and control["uid"] == 0
            and int(control["caps"], 16) != 0
            and control["nnp"] == 0
            and control["seccomp"] == 0
            and control["syscall_result"] >= 0
            and control["errno"] == 0
            and all(
                item["errno"] == 1 for item in protected["namespace_clones"].values()
            )
            and all(
                control["namespace_clones"][name]["created"]
                for name in ("mount", "net", "pid")
            )
            and control["namespace_clones"]["user"]["errno"] in (0, 1)
            and protected["namespace_filter_errno"]
            == dict.fromkeys(("user", "mount", "net", "pid"), 1)
            and control["namespace_filter_errno"]
            == dict.fromkeys(("user", "mount", "net", "pid"), 22)
            and protected["ordinary_invalid_clone_errno"]
            == control["ordinary_invalid_clone_errno"]
            == 22
            and protected["clone3"] == {"created": False, "errno": 38}
            and control["clone3"]["created"]
            and protected["socket_domains"]["1"]
            == protected["socket_domains"]["2"]
            == 0
            and protected["socket_domains"]["10"] == control["socket_domains"]["10"]
            and protected["socket_domains"]["10"] in (0, 97)
            and protected["socket_domains"]["16"]
            == protected["socket_domains"]["17"]
            == 1
            and control["socket_domains"]["1"]
            == control["socket_domains"]["2"]
            == control["socket_domains"]["16"]
            == control["socket_domains"]["17"]
            == 0
            and protected["ordinary_fork_thread_ok"]
            and control["ordinary_fork_thread_ok"]
        )
    elif scenario == "egress-fast-fail":
        ok = (
            protected["verdict"] == "refused"
            and protected["latency_ms"] < 2000
            and control["verdict"] == "timeout"
        )
    elif scenario == "device-policy":
        ok = (
            protected["null"] == "created"
            and protected["mem"] == protected["disk"] == protected["kmsg"] == 1
            and protected["permitted"]
            == control["permitted"]
            == dict.fromkeys(("null", "zero", "urandom", "full", "pts", "tty"), "ok")
            and int(protected["caps"], 16) == (1 << 27)
            and protected["nnp"] == 1
            and protected["masked"] in (13, 30)
            and control["null"]
            == control["mem"]
            == control["disk"]
            == control["kmsg"]
            == "created"
            and control["masked"] == "open"
            and control["nnp"] == 0
        )
    elif scenario == "resource-caps":
        ok = (
            0 < protected["children"] < 128
            and protected["errno"] == 11
            and control["children"] == 260
            and control["errno"] == 0
            and protected["heartbeat"] == control["heartbeat"] == "alive"
        )
    else:
        raise ScriptError(f"unknown policy probe: {scenario}")
    if not ok:
        raise ScriptError(
            f"{scenario} protection/control verdict did not flip: {protected}, {control}"
        )


def one_shot_wall(backend: str, output: Path, timeout: float) -> list[dict[str, Any]]:
    cache = ImageCache()
    value, image = ensure("alpine:3.20")
    records: list[dict[str, Any]] = []
    for limit, expected in ((100, 124), (0, 0)):
        owner = "wall-probe-" + uuid.uuid4().hex
        scratch = cache.acquire(owner, value)
        arguments = [
            sys.executable,
            str(BuildConstants.REPO_ROOT / "scripts/nvx.py"),
            "sandbox",
            "run",
            "--hypervisor",
            backend,
            "--memory-mib",
            "512",
            "--scratch",
            str(scratch),
            "--entrypoint",
            "/bin/sleep",
            "--arg",
            "2",
            "--exec-timeout-ms",
            str(limit),
        ]
        for layer in image["layers"]:
            arguments.extend(
                [
                    "--layer",
                    ",".join(
                        (
                            layer["role"],
                            str(cache.verify_blob(layer["digest"])),
                            layer["uuid"],
                        )
                    ),
                ]
            )
        try:
            started = time.monotonic()
            result = subprocess.run(arguments, capture_output=True, timeout=timeout * 3)
            (output / f"one-shot-wall-{limit}.log").write_bytes(
                result.stdout + result.stderr
            )
            marker = f"NVX-SANDBOX-EXIT: status={expected}".encode()
            if result.returncode != expected or marker not in result.stdout:
                raise ScriptError(
                    "one-shot wall cap protection/control verdict did not flip"
                )
            records.append(
                {
                    "wall_timeout_ms": limit,
                    "returncode": result.returncode,
                    "elapsed_seconds": time.monotonic() - started,
                }
            )
        finally:
            cache.release(owner)
    return records


def run(backend: str, scenarios: tuple[str, ...], output: Path, timeout: float) -> None:
    for scenario in scenarios:
        script = {
            "seccomp-profile": SECCOMP,
            "egress-fast-fail": NETWORK,
            "resource-caps": RESOURCES,
            "device-policy": DEVICES,
        }[scenario]
        protected = execute(
            backend,
            script,
            output,
            scenario + "-protected",
            timeout,
            profile="ci" if scenario == "device-policy" else "default",
            extra=("--cap-add", "MKNOD") if scenario == "device-policy" else (),
        )
        control = execute(
            backend,
            script,
            output,
            scenario + "-control",
            timeout,
            profile="default" if scenario == "egress-fast-fail" else "risky",
            legacy_drop=scenario == "egress-fast-fail",
        )
        validate(scenario, protected, control)
        if scenario == "device-policy":
            standard = execute(
                backend,
                STANDARD_DEVICES + "\nprint(json.dumps(result))\n",
                output,
                "standard-devices-default",
                timeout,
            )
            if standard["permitted"] != dict.fromkeys(
                ("null", "zero", "urandom", "full", "pts", "tty"), "ok"
            ):
                raise ScriptError("default profile denied a permitted private device")
            protected["default_permitted"] = standard["permitted"]
        if scenario == "resource-caps":
            memory_protected = execute(
                backend,
                MEMORY,
                output,
                "memory-protected",
                timeout,
                extra=("--memory-mib", "1024"),
            )
            memory_control = execute(
                backend,
                MEMORY,
                output,
                "memory-control",
                timeout,
                profile="risky",
                extra=("--memory-mib", "1024"),
            )
            wall_protected = execute(
                backend,
                WALL,
                output,
                "wall-protected",
                timeout,
                extra=("--exec-timeout-ms", "100"),
                expected_status=124,
            )
            wall_control = execute(
                backend, WALL, output, "wall-control", timeout, profile="risky"
            )
            if (
                memory_protected["signal"] != 9
                or memory_control["exit"] != 0
                or wall_protected["phase"] != "started"
                or wall_control["phase"] != "completed"
            ):
                raise ScriptError(
                    "memory or wall cap protection/control verdict did not flip"
                )
            protected.update(memory=memory_protected, wall=wall_protected)
            control.update(memory=memory_control, wall=wall_control)
            protected["one_shot_wall"] = one_shot_wall(backend, output, timeout)
        (output / f"{scenario}.json").write_text(
            json.dumps(
                dict(
                    scenario=scenario,
                    backend=backend,
                    protected=protected,
                    control=control,
                ),
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        print("NVX-" + scenario.upper() + "-OK")
