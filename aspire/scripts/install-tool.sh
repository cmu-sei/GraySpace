#!/bin/bash
# Download a development tool for the Aspire AppHost, once, into ~/.cache/grayspace-dev.
#
#   install-tool.sh prometheus 3.6.0     ->  ~/.cache/grayspace-dev/prometheus-3.6.0/prometheus
#
# The version matches what the base deploys (ansible/main.yml). No container runtime is needed.
set -euo pipefail

tool="${1:?Usage: install-tool.sh prometheus VERSION}"
version="${2:?Usage: install-tool.sh prometheus VERSION}"
cache="${GRAYSPACE_DEV_CACHE:-$HOME/.cache/grayspace-dev}"
dest="$cache/$tool-$version"

case "$(uname -m)" in
    x86_64 | amd64) arch=amd64 ;;
    aarch64 | arm64) arch=arm64 ;;
    *) echo "Unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

case "$tool" in
    prometheus)
        url="https://github.com/prometheus/prometheus/releases/download/v$version/prometheus-$version.linux-$arch.tar.gz"
        binary="$dest/prometheus" ;;
    *) echo "Unknown tool $tool" >&2; exit 1 ;;
esac

if [ -x "$binary" ]; then
    echo "$tool $version is installed at $dest"
    exit 0
fi

echo "Downloading $url"
mkdir -p "$cache"
tmp="$(mktemp -d "$cache/.download-XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL --retry 3 "$url" -o "$tmp/archive.tar.gz"
mkdir "$tmp/unpacked"
tar -xzf "$tmp/archive.tar.gz" -C "$tmp/unpacked" --strip-components=1
rm -rf "$dest"
mv "$tmp/unpacked" "$dest"
echo "$tool $version installed at $dest"
