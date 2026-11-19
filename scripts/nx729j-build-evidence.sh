#!/usr/bin/env bash
set -euo pipefail

evidence="$GITHUB_WORKSPACE/nx729j-build-evidence"
output="$KERNEL_ROOT/out/android13-5.15"
mkdir -p "$evidence"
cp "$output/common/.config" "$evidence/kernel.config"
cp "$output/common/Module.symvers" "$evidence/Module.symvers"
cp "$output/common/include/config/kernel.release" "$evidence/kernel.release"
cp "$KERNEL_ROOT/common/include/linux/susfs.h" "$evidence/susfs.h"

{
  printf 'workflow=%s\n' "$GITHUB_SHA"
  printf 'run=%s/%s/actions/runs/%s\n' "$GITHUB_SERVER_URL" "$GITHUB_REPOSITORY" "$GITHUB_RUN_ID"
  for path in "$KERNEL_ROOT/common" "$KERNEL_ROOT/KernelSU" "$SUSFS4KSU" "$KERNEL_PATCHES" "$SUKISU_PATCHES" "$ACTION_BUILD"; do
    printf '%s=%s\n' "$(basename "$path")" "$(git -C "$path" rev-parse HEAD)"
  done
  printf 'KSU_VERSION=%s\n' "${KSU_VERSION:-unknown}"
  printf 'KSU_FULL_VERSION=%s\n' "${KSU_FULL_VERSION:-unknown}"
} > "$evidence/sources.txt"

for setting in \
  CONFIG_ARM64_4K_PAGES=y \
  CONFIG_KSU=y \
  CONFIG_KSU_SUSFS=y \
  CONFIG_LTO_CLANG_THIN=y \
  CONFIG_ZRAM=y \
  CONFIG_CRYPTO_LZ4KD=y \
  CONFIG_BBG=y \
  CONFIG_IP_SET=y \
  CONFIG_NET_SCH_FQ=y \
  CONFIG_CIFS=y; do
  grep -qxF "$setting" "$evidence/kernel.config" || { echo "::error::Missing requested configuration: $setting"; exit 1; }
done
grep -qxF 'CONFIG_TCP_CONG_BBR3=y' "$evidence/kernel.config"
if grep -Eq '^CONFIG_(KPM|REKERNEL|NOMOUNT)=y$' "$evidence/kernel.config"; then
  echo '::error::An unrequested optional feature is enabled'
  exit 1
fi
grep -qF '#define SUSFS_VERSION "v2.4.0"' "$evidence/susfs.h"
test "$(git -C "$KERNEL_ROOT/common" rev-parse HEAD)" = bfd2ab010d7b36dd753731915553c4f37b98c6f6
test "$(git -C "$SUSFS4KSU" rev-parse HEAD)" = c1d1da42b74771944207ec45aaceff9c57e84985

sha256sum "$output/dist/Image" "$output/dist/Image.lz4" > "$evidence/kernel-sha256.txt"
grep -E '^(CONFIG_(ARM64_4K_PAGES|KSU|LTO|ZRAM|CRYPTO_LZ4KD|BBG|IP_SET|NET_SCH_FQ|CIFS|TCP_CONG_BBR|KPM|REKERNEL|NOMOUNT)|# CONFIG_(KPM|REKERNEL|NOMOUNT))' "$evidence/kernel.config" > "$evidence/config-summary.txt"
printf 'All requested kernel configuration checks passed.\n' | tee "$evidence/validation.txt"
