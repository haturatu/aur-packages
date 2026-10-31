#!/usr/bin/env bash
set -euo pipefail

# Run only in a disposable Arch container. No publishing credentials enter it.
cd /repo
pacman -Syu --noconfirm --needed python git nvchecker pacman-contrib namcap
dependency_lines=$(python aurctl deps --all)
if [[ -n "$dependency_lines" ]]; then
  mapfile -t dependencies <<< "$dependency_lines"
  pacman -S --noconfirm --needed "${dependencies[@]}"
fi
useradd --create-home builder
chown -R builder:builder /repo
runuser -u builder -- bash -c 'cd /repo && python aurctl update --all && python aurctl validate --all --build'
