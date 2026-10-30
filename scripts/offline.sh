#!/usr/bin/env bash
# Run a command with the network switched off: a new network namespace holding only an unconfigured loopback.
# Synthea runs this way: it needs no network, so a flaw found later in one of its libraries cannot be reached.
#   scripts/offline.sh java -jar synthea.jar ...
#
# Linux: unshare, as root, through passwordless sudo (dropping back to the calling user), or in an
# unprivileged user namespace. macOS: sandbox-exec with outbound and inbound networking denied. Before the
# command runs, a probe confirms nothing outside is reachable. Anywhere isolation is not possible the script
# refuses, unless CADUCEUS_ALLOW_NETWORK=1 says to run with the network on.
set -euo pipefail
[ $# -gt 0 ] || { echo "usage: $0 command [args...]" >&2; exit 2; }

# Runs inside the isolated environment: refuse if a connection out still succeeds, then become the command.
guard='if timeout 3 bash -c "exec 3<>/dev/tcp/1.1.1.1/443" 2>/dev/null; then
  echo "offline.sh: the network is still reachable; refusing to run" >&2; exit 97
fi
exec "$@"'

case "$(uname -s)" in
  Linux)
    if [ "$(id -u)" = 0 ]; then
      exec unshare --net -- bash -c "$guard" offline "$@"
    elif sudo -n true 2>/dev/null; then
      exec sudo -n --preserve-env unshare --net -- \
        setpriv --reuid="$(id -u)" --regid="$(id -g)" --init-groups -- \
        env "PATH=$PATH" bash -c "$guard" offline "$@"
    elif unshare --net --map-root-user true 2>/dev/null; then
      exec unshare --net --map-root-user -- bash -c "$guard" offline "$@"
    fi
    ;;
  Darwin)
    exec sandbox-exec -p '(version 1) (allow default) (deny network-outbound) (deny network-inbound)' \
      bash -c "$guard" offline "$@"
    ;;
esac

if [ "${CADUCEUS_ALLOW_NETWORK:-}" = 1 ]; then
  echo "offline.sh: cannot switch the network off here; running with it on, as CADUCEUS_ALLOW_NETWORK=1 asks" >&2
  exec "$@"
fi
echo "offline.sh: cannot switch the network off on this machine. Set CADUCEUS_ALLOW_NETWORK=1 to run anyway." >&2
exit 1
