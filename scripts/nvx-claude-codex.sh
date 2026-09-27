#!/bin/sh
# Boot an NVX Alpine guest on macOS/HVF with networking and install the
# Claude Code and/or Codex CLIs inside it.
#
# Usage:
#   scripts/nvx-claude-codex.sh [--agent claude|codex|both]
#                               [--memory-mib N] [--wait-secs N]
#                               [--tcp HOSTPORT:GUESTPORT]... [--udp HOSTPORT:GUESTPORT]...
#
# What it does:
#   1. boots with 2 GiB RAM (installer payloads need tmpfs room) and a
#      consomme virtio NIC (DHCP, DNS, and NAT come up automatically);
#   2. feeds a setup script into the guest console in two phases. The
#      serial console drops input while the guest is busy, so the install
#      phase and the verify phase are separated by a wait;
#   3. hands the console to you interactively when setup finishes
#      (Ctrl-C here kills the VM).
#
# Guest facts the setup handles: the guest boots with a stale clock, which
# breaks TLS certificate validation, so the clock is set from the host
# first; bash and curl are not in the initramfs, so they arrive via apk.
# Everything installed lives in guest RAM and vanishes when the VM exits.
# To actually use the agents you still need credentials in the guest
# (e.g. export ANTHROPIC_API_KEY / OPENAI_API_KEY at the guest shell).
set -u

AGENT=both
MEMORY_MIB=2048
WAIT_SECS=420
NET_CIDR=192.168.127.0/24
EXTRA_FWDS=

usage() {
    echo "usage: $0 [--agent claude|codex|both] [--memory-mib N] [--wait-secs N]" >&2
    echo "          [--tcp HOSTPORT:GUESTPORT]... [--udp HOSTPORT:GUESTPORT]..." >&2
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --agent) AGENT=${2:?}; shift 2 ;;
        --memory-mib) MEMORY_MIB=${2:?}; shift 2 ;;
        --wait-secs) WAIT_SECS=${2:?}; shift 2 ;;
        --tcp|--udp)
            proto=${1#--}
            spec=${2:?}
            hp=${spec%%:*}; gp=${spec##*:}
            [ -n "$hp" ] && [ -n "$gp" ] && [ "$hp" != "$spec" ] || {
                echo "--$proto needs HOSTPORT:GUESTPORT, got '$spec'" >&2
                usage
            }
            EXTRA_FWDS="$EXTRA_FWDS,hostfwd=$proto::$hp-:$gp"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "unknown option: $1" >&2; usage ;;
    esac
done

case "$AGENT" in
    claude|codex|both) ;;
    *) echo "unknown agent: $AGENT" >&2; usage ;;
esac

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TMPDIR_WORK=$(mktemp -d "${TMPDIR:-/tmp}/nvx-agents.XXXXXX")
PHASE1=$TMPDIR_WORK/phase1.sh
PHASE2=$TMPDIR_WORK/phase2.sh
trap 'rm -rf "$TMPDIR_WORK"' EXIT INT TERM

# Host UTC in busybox date(1) format (MMDDhhmmYYYY); the guest clock is set
# a couple of minutes ahead so TLS handshakes during setup validate.
STAMP=$(date -u -v+2M "+%m%d%H%M%Y" 2>/dev/null || date -u -d "+2 minutes" "+%m%d%H%M%Y")

APK_BASE="bash curl"
case "$AGENT" in
    codex|both) APK_BASE="$APK_BASE npm" ;;
esac
{
    echo "date -u $STAMP; apk -q update; apk -q add $APK_BASE; echo SETUP-BASE-OK"
    case "$AGENT" in
        claude|both)
            echo "curl -fsSL https://claude.ai/install.sh -o /tmp/ci.sh && echo CURL-OK"
            echo "bash /tmp/ci.sh 2>&1 | tail -n 4; echo CLAUDE-INSTALL-DONE"
            ;;
    esac
    case "$AGENT" in
        codex|both)
            echo "npm install -g @openai/codex 2>&1 | tail -n 3; echo CODEX-INSTALL-DONE"
            # Alpine npm installs global bins under /usr/local/bin, which is
            # not in the guest's default PATH.
            echo "export PATH=\"\$PATH:/usr/local/bin\""
            ;;
    esac
} >"$PHASE1"

{
    case "$AGENT" in
        claude|both)
            echo "~/.local/bin/claude --version 2>&1 | head -n 1"
            ;;
    esac
    case "$AGENT" in
        codex|both)
            echo "/usr/local/bin/codex --version 2>&1 | head -n 1"
            ;;
    esac
    echo "echo AGENTS-READY"
} >"$PHASE2"

echo ">> guest setup is two console phases separated by a ${WAIT_SECS}s wait; then this terminal becomes the guest shell" >&2
{
    sleep 20
    cat "$PHASE1"
    sleep "$WAIT_SECS"
    cat "$PHASE2"
    cat
} | python3 "$REPO_ROOT/scripts/nvx.py" run --hypervisor hvf \
    --memory-mib "$MEMORY_MIB" \
    --virtio-net "consomme:$NET_CIDR$EXTRA_FWDS"
