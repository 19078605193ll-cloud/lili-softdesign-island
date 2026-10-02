#!/bin/sh
set -eu
umask 077
cd /opt/softdesign-island
exec 9>/run/lock/softdesign-island-backup.lock
flock -n 9 || exit 0
destination=/opt/softdesign-island-backups/daily/$(date +%Y%m%d_%H%M%S)
python3 scripts/paired_backup.py --project softdesign-island --env-file deploy/compose.env --compose-file compose.http.yml --destination "$destination"
chown -R ubuntu:ubuntu "$destination"
df -h / > /opt/softdesign-island-backups/disk-status.txt
used=$(df -P / | awk 'NR==2 {gsub(/%/, "", $5); print $5}')
if [ "$used" -ge 85 ]; then
    logger -p daemon.warning 'softdesign-island: disk usage exceeds 85 percent'
fi
find /opt/softdesign-island-backups/daily -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf -- {} +
