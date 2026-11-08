#!/usr/bin/env bash
# Peak and current memory per running container of this project (cgroup v2 memory.peak / memory.current), for the
# resource measurements of NFR-RES-01 (docs/02 §10). The peak counts from the container's start. Images without
# `cat` report n/a (use `make stats` for those).
set -uo pipefail
mb() { [[ "$1" =~ ^[0-9]+$ ]] && echo $(( $1 / 1048576 )) || echo "$1"; }
printf '%-40s %10s %10s %10s\n' container current_MB peak_MB limit_MB
for id in $(docker ps -q --filter "label=com.docker.compose.project=leave-assistant"); do
  name=$(docker inspect -f '{{.Name}}' "$id" | sed 's#^/##')
  values=$(docker exec "$id" cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.max 2>/dev/null | tr '\n' ' ')
  read -r current peak limit <<< "${values:-n/a n/a n/a}"
  printf '%-40s %10s %10s %10s\n' "$name" "$(mb "${current:-n/a}")" "$(mb "${peak:-n/a}")" "$(mb "${limit:-n/a}")"
done | sort
