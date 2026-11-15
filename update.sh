#!/bin/bash
# Updates the sonosyt server: fetches the latest code, rebuilds the image and
# restarts the container with the same options and cache folder as before.
#
#   bash update.sh                              # keep the current options
#   bash update.sh --speaker-ip 192.168.1.50    # or set new ones
set -euo pipefail
cd "$(dirname "$0")"

NAME=sonosyt
options=()
cache=""
if docker container inspect "$NAME" >/dev/null 2>&1; then
    # Reuse what the running container was started with.
    while IFS= read -r line; do
        [ -n "$line" ] && options+=("$line")
    done < <(docker container inspect --format '{{range .Config.Cmd}}{{println .}}{{end}}' "$NAME")
    cache=$(docker container inspect --format \
        '{{range .Mounts}}{{if eq .Destination "/cache"}}{{if eq .Type "bind"}}{{.Source}}{{else}}{{.Name}}{{end}}{{end}}{{end}}' \
        "$NAME")
fi
[ $# -gt 0 ] && options=("$@")
[ -n "$cache" ] || cache="$(dirname "$PWD")/sonosyt-cache"

git pull --ff-only
docker build -t "$NAME" .
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped --network host \
    -v "$cache:/cache" "$NAME" ${options[@]+"${options[@]}"}
echo "sonosyt läuft mit: ${options[*]:-(Standardoptionen)}  |  Cache: $cache"
