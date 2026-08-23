#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="${project_root}/build/native"
package_native="${project_root}/discord_local_proxy/native"
test_python_bin="${DLP_TEST_PYTHON:-python3}"
build_python_bin="${DLP_BUILD_PYTHON:-${test_python_bin}}"

if command -v cmake >/dev/null 2>&1; then
  cmake -S "${project_root}/native" -B "${build_dir}" -DCMAKE_BUILD_TYPE=Release
  cmake --build "${build_dir}" --config Release --parallel
  cmake --install "${build_dir}" --config Release --prefix "${package_native}"
elif [ -f "${package_native}/libdiscord_udp_shim.so" ]; then
  echo "Aviso: CMake ausente; reutilizando o componente nativo Linux existente." >&2
else
  echo "Erro: CMake não foi encontrado e não existe componente nativo pré-compilado." >&2
  exit 1
fi

if command -v strip >/dev/null 2>&1; then
  strip --strip-unneeded "${package_native}/libdiscord_udp_shim.so"
fi

shim_test_dir="$(mktemp -d)"
trap 'rm -rf -- "${shim_test_dir}"' EXIT
cp "${package_native}/libdiscord_udp_shim.so" "${shim_test_dir}/libdiscord_udp_shim.so"

DLP_LINUX_SHIM="${shim_test_dir}/libdiscord_udp_shim.so" \
  "${test_python_bin}" -m unittest discover -s "${project_root}/tests" -v
"${build_python_bin}" -m PyInstaller --noconfirm "${project_root}/DiscordLocalProxy.spec"
"${project_root}/dist/DiscordLocalProxy" check-gui
if command -v xvfb-run >/dev/null 2>&1; then
  xvfb-run -a "${project_root}/dist/DiscordLocalProxy" check-font
elif [ -n "${DISPLAY:-}" ]; then
  "${project_root}/dist/DiscordLocalProxy" check-font
else
  echo "Aviso: check-font ignorado porque não há DISPLAY nem xvfb-run." >&2
fi
install -m 0755 "${project_root}/INICIAR-LINUX.sh" "${project_root}/dist/INICIAR-LINUX.sh"
install -m 0755 "${project_root}/INSTALAR-LINUX.sh" "${project_root}/dist/INSTALAR-LINUX.sh"
install -m 0644 "${project_root}/LICENSE" "${project_root}/dist/LICENSE.txt"
install -m 0644 "${project_root}/NOTICE.md" "${project_root}/dist/NOTICE.md"

release_root="${project_root}/release"
release_dir="${release_root}/DiscordLocalProxy-Linux-x64"
internal_dir="${release_dir}/.discord-local-proxy"
archive="${release_root}/DiscordLocalProxy-Linux-x64.tar.gz"
archive_epoch="${SOURCE_DATE_EPOCH:-0}"

rm -rf -- "${release_dir}"
mkdir -p -- "${internal_dir}"
install -m 0755 "${project_root}/packaging/linux/INICIAR-LINUX.sh" "${release_dir}/INICIAR-LINUX.sh"
install -m 0755 "${project_root}/packaging/linux/INSTALAR-LINUX.sh" "${release_dir}/INSTALAR-LINUX.sh"
install -m 0755 "${project_root}/dist/DiscordLocalProxy" "${internal_dir}/DiscordLocalProxy"
install -m 0644 "${project_root}/LICENSE" "${internal_dir}/LICENSE.txt"
install -m 0644 "${project_root}/NOTICE.md" "${internal_dir}/NOTICE.md"
tar \
  --sort=name \
  --mtime="@${archive_epoch}" \
  --owner=0 \
  --group=0 \
  --numeric-owner \
  -czf "${archive}" \
  -C "${release_root}" \
  "$(basename "${release_dir}")"
rm -rf -- "${release_dir}"
echo "Pacote Linux criado em ${archive}"
