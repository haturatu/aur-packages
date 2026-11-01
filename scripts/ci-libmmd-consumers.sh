#!/usr/bin/env bash
# The single-quoted bash programs are evaluated by the builder shell.
# shellcheck disable=SC2016
set -euo pipefail

# Run as root in the disposable Arch container created by ci-validate.sh.
# Compile unprivileged, then install the exact package under test with pacman.
cd /repo
pacman -S --noconfirm --needed cmake ninja bullet sdl3 vulkan-headers \
  vulkan-icd-loader shaderc stb nlohmann-json argparse
work=$(mktemp -d /tmp/libmmd-consumers.XXXXXX)
trap 'rm -rf "$work"' EXIT
mkdir "$work/recipe"
cp packages/libmmd-cpp/PKGBUILD packages/libmmd-cpp/.SRCINFO "$work/recipe/"
chown -R builder:builder "$work"
export LIBMMD_CONSUMER_REF=${LIBMMD_CONSUMER_REF:-main}
# Sources prepared outside the container (with the caller's credentials) are
# preferred; credentials themselves never enter the container.
export LIBMMD_CONSUMER_DIR=${LIBMMD_CONSUMER_DIR:-/repo/.cache/consumers}
export CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL:-2}
runuser -u builder -- bash -euo pipefail -c '
  cd "$1/recipe"
  makepkg --force --noconfirm --cleanbuild
' bash "$work"
mapfile -t artifacts < <(runuser -u builder -- bash -c 'cd "$1/recipe" && makepkg --packagelist' bash "$work")
pacman -U --noconfirm "${artifacts[@]}"

runuser -u builder -- env LIBMMD_CONSUMER_DIR="$LIBMMD_CONSUMER_DIR" bash -euo pipefail -c '
  work=$1
  declare -A available=()
  for app in mikumikudesu pmxer vmd-trace; do
    if [[ -d "$LIBMMD_CONSUMER_DIR/$app/.git" ]]; then
      cp -a "$LIBMMD_CONSUMER_DIR/$app" "$work/$app"
    elif ! git clone --quiet --depth 1 --branch "$LIBMMD_CONSUMER_REF" \
      "https://github.com/haturatu/$app.git" "$work/$app"; then
      printf "WARN %s: source is not accessible anonymously; skipping its consumer build\n" "$app" >&2
      rm -rf "$work/$app"
      continue
    fi
    available[$app]=1
    printf "INFO %s commit: %s\n" "$app" "$(git -C "$work/$app" rev-parse HEAD)"
  done
  if [[ -z ${available[mikumikudesu]:-} || -z ${available[pmxer]:-} ]]; then
    printf "ERROR the public consumer applications could not be prepared\n" >&2
    exit 1
  fi

  common=(-G Ninja -DCMAKE_BUILD_TYPE=Release -DLIBMMD_PROVIDER=SYSTEM -DBUILD_TESTING=ON)
  binaries=()
  cmake -S "$work/mikumikudesu" -B "$work/miku-build" "${common[@]}" \
    -DDAYO_ENABLE_IMGUI=OFF -DDAYO_ENABLE_MEDIA=OFF \
    -DDAYO_ENABLE_OIDN=OFF -DDAYO_ENABLE_VMA=OFF
  cmake --build "$work/miku-build" --target mikumikudesu dayo_core_tests \
    dayo_cli_tests dayo_output_tests dayo_interpolation_bezier_tests
  ctest --test-dir "$work/miku-build" --output-on-failure --no-tests=error \
    -R "^(core|cli|cli_help|cli_version|output|interpolation_bezier)$"
  binaries+=("$work/miku-build/mikumikudesu")

  cmake -S "$work/pmxer" -B "$work/pmxer-build" "${common[@]}" -DPMXER_BUILD_GUI=OFF
  cmake --build "$work/pmxer-build"
  ctest --test-dir "$work/pmxer-build" --output-on-failure --no-tests=error
  "$work/pmxer-build/pmxer" --help
  binaries+=("$work/pmxer-build/pmxer")

  if [[ -n ${available[vmd-trace]:-} ]]; then
    cmake -S "$work/vmd-trace" -B "$work/vmd-build" "${common[@]}" \
      -DVMDTRACE_BUILD_DESKTOP=OFF -DVMDTRACE_WITH_CAPTURE=OFF \
      -DVMDTRACE_WITH_MNN=OFF -DVMDTRACE_WITH_REMOTE=OFF -DVMDTRACE_IPO=OFF
    cmake --build "$work/vmd-build"
    ctest --test-dir "$work/vmd-build" --output-on-failure --no-tests=error
    "$work/vmd-build/vmd-tool" --help
    binaries+=("$work/vmd-build/vmd-tool")
  else
    printf "WARN vmd-trace: consumer build skipped\n" >&2
  fi

  # Check real dynamic linkage, and reject accidental bundled SDK builds.
  for binary in "${binaries[@]}"; do
    ldd "$binary" > "$work/linked-libraries"
    if ! grep -Eq "libmmd_core.so[^ ]* => /usr/lib/" "$work/linked-libraries"; then
      printf "ERROR %s does not link the installed libmmd\n" "$binary" >&2
      exit 1
    fi
  done
' bash "$work"
printf 'INFO libmmd system-package consumer checks passed\n'
