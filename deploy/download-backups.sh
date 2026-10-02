#!/bin/bash
set -euo pipefail
# Invoked by download-backups.ps1 inside WSL; keep temporary key copies private.
key_source=$1
known_hosts_source=$2
destination=$3
backup_name=$4
[[ "$backup_name" =~ ^[0-9]{8}_[0-9]{6}$ ]]
runtime=$(mktemp -d)
trap 'rm -f "$runtime/key" "$runtime/known_hosts"; rmdir "$runtime"' EXIT
install -m 600 "$key_source" "$runtime/key"
install -m 600 "$known_hosts_source" "$runtime/known_hosts"
rsync -a --no-whole-file --inplace --stats \
  -e "ssh -i $runtime/key -o UserKnownHostsFile=$runtime/known_hosts -o BatchMode=yes -o StrictHostKeyChecking=yes" \
  "ubuntu@159.75.2.196:/opt/softdesign-island-backups/daily/$backup_name/" "$destination/"
