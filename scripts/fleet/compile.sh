#!/usr/bin/env bash
set -euo pipefail
cd "$KERNEL_ROOT"
OUT="$KERNEL_ROOT/fleet-out"
mkdir -p "$OUT" "$GITHUB_WORKSPACE/fleet-evidence" "$KERNEL_ROOT/fleet-dist"

CLANG_VERSION=$(sed -n 's/^CLANG_VERSION=//p' common/build.config.constants | tr -d '"')
CLANG_BIN="$KERNEL_ROOT/prebuilts/clang/host/linux-x86/$CLANG_VERSION/bin"
test -x "$CLANG_BIN/clang"
export PATH="$CLANG_BIN:/usr/bin:/bin:$PATH"
export ARCH=arm64 LLVM=1 LLVM_IAS=1
export CROSS_COMPILE=aarch64-linux-gnu-
export KBUILD_BUILD_USER=build-user KBUILD_BUILD_HOST=build-host
export KBUILD_BUILD_TIMESTAMP="$(date -u '+%a %b %e %T UTC %Y')"

cp "$DEFCONFIG" "$OUT/.config"
python3 - "$GITHUB_WORKSPACE" "$KERNEL_ROOT" "$OUT" <<'PY'
import json, pathlib, re, sys
repo, kernel, out = map(pathlib.Path, sys.argv[1:])
t=json.loads((repo/'fleet-target.json').read_text())
symbols=[line.split()[1] for line in (repo/t['symvers']).read_text().splitlines()]
(kernel/'fleet-abi-symbols').write_text('\n'.join(sorted(set(symbols)))+'\n')
p=out/'.config'; c=p.read_text()
settings={
 'LOCALVERSION':'""', 'LOCALVERSION_AUTO':'n',
 'LTO_NONE':'n','LTO_CLANG_FULL':'n','LTO_CLANG_THIN':'y',
 'KSU':'y','KSU_SUSFS':'y','KSU_HOOK_SUSFS_INLINE':'y','KSU_HOOK_TRACEPOINT':'n',
 'KSU_SUSFS_PRIORITY_UID_FILTER':'y',
 'KPM':'n','REKERNEL':'n','NOMOUNT':'n',
 'UNUSED_KSYMS_WHITELIST':json.dumps(str(kernel/'fleet-abi-symbols')),
}
lsm=re.search(r'^CONFIG_LSM="([^"]+)"',c,re.M)
if lsm:
 values=lsm[1].split(',')
 if 'baseband_guard' not in values:values.append('baseband_guard')
 settings['LSM']=json.dumps(','.join(values))
for k,v in settings.items():
 c=re.sub(rf'^(?:CONFIG_{k}=.*|# CONFIG_{k} is not set)\n?', '', c, flags=re.M)
 c+=f'CONFIG_{k}={v}\n'
p.write_text(c)
suffix=t['release'].removeprefix(t['series']+'.'+t['sublevel'])
script=kernel/'common/scripts/setlocalversion'
script.write_text('#!/bin/sh\nprintf "%s\\n" '+repr(suffix)+'\n')
script.chmod(0o755)
PY

# Direct Kbuild retains the target configuration, including OEM features, rather
# than replacing it with a generic GKI configuration during a Kleaf sync.
make -C common O="$OUT" -j"$(nproc)" CC="ccache clang" HOSTCC=clang HOSTCXX=clang++ HOSTLDFLAGS=-fuse-ld=lld olddefconfig
cp "$OUT/.config" "$GITHUB_WORKSPACE/fleet-evidence/kernel.config"
bash KernelSU/kernel/tools/check_susfs_inline.sh common
make -C common O="$OUT" -j"$(nproc)" CC="ccache clang" HOSTCC=clang HOSTCXX=clang++ HOSTLDFLAGS=-fuse-ld=lld Image modules
cp "$OUT/arch/arm64/boot/Image" "$KERNEL_ROOT/fleet-dist/Image"
lz4 -l -f "$KERNEL_ROOT/fleet-dist/Image" "$KERNEL_ROOT/fleet-dist/Image.lz4"
cp "$OUT/Module.symvers" "$OUT/include/config/kernel.release" "$GITHUB_WORKSPACE/fleet-evidence/"
"$CLANG_BIN/clang" --version > "$GITHUB_WORKSPACE/fleet-evidence/toolchain.txt"
