#!/bin/sh
set -eu

# A named volume is mounted after image construction and may be root-owned on
# first use. Hand it to the unprivileged service account before starting the
# API so the operations endpoints can create their SQLite ledger.
mkdir -p /app/var
chown -R finrecon:finrecon /app/var

exec su -s /bin/sh finrecon -c 'exec "$0" "$@"' -- "$@"
