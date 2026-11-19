#!/usr/bin/env bash
set -euo pipefail
cd "$KERNEL_ROOT"
OUT="$KERNEL_ROOT/fleet-out"
mkdir -p "$OUT" "$GITHUB_WORKSPACE/fleet-evidence" "$KERNEL_ROOT/fleet-dist"

CLANG_VERSION=$(sed -n 's/^CLANG_VERSION=//p' common/build.config.constants | tr -d '"')
CLANG_BIN="$KERNEL_ROOT/prebuilts/clang/host/linux-x86/clang-$CLANG_VERSION/bin"
if [ ! -x "$CLANG_BIN/clang" ]; then
  echo "::error::Recorded toolchain is missing: $CLANG_BIN/clang"
  ls "$KERNEL_ROOT/prebuilts/clang/host/linux-x86"
  exit 1
fi
echo "Using exact source toolchain: $CLANG_BIN"
export PATH="$CLANG_BIN:/usr/bin:/bin:$PATH"
export ARCH=arm64 LLVM=1 LLVM_IAS=1
export CROSS_COMPILE=aarch64-linux-gnu-
export KBUILD_BUILD_USER=build-user KBUILD_BUILD_HOST=build-host
export KBUILD_BUILD_TIMESTAMP="$(date -u '+%a %b %e %T UTC %Y')"

cp "$DEFCONFIG" "$OUT/.config"
# The imported userspace LZ4 header adds ELF visibility annotations to public
# prototypes. Kernel exports already carry their visibility; genksyms 6.6 would
# otherwise encode the extra annotation and change existing vendor-module CRCs.
python3 - <<'PY'
from pathlib import Path
import re
p=Path('common/lib/lz4/lz4.h')
text=p.read_text()
text=text.replace('#define LZ4LIB_VISIBILITY __attribute__((visibility("default")))',
                  '#define LZ4LIB_VISIBILITY /* kernel EXPORT_SYMBOL owns visibility */')
p.write_text(text)
if Path('common/fs/f2fs/lz4armv8/lz4accel.h').exists():
    names=['lz4_decompress_accel_enable','lz4_decompress_asm','lz4_decompress_asm_fn',
           '_lz4_decompress_asm','_lz4_decompress_asm_noprfm',
           '__cfi_lz4_decompress_asm','__cfi_lz4_decompress_asm_noprfm']
    for file in Path('common/lib/lz4').rglob('*'):
        if file.suffix not in ['.c','.h','.S']:continue
        text=file.read_text()
        for name in names:text=re.sub(r'\b'+re.escape(name)+r'\b','fleet_'+name,text)
        file.write_text(text)
    print('Namespaced imported LZ4 acceleration internals alongside OEM F2FS')
PY
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
makefile=(kernel/'common/Makefile').read_text()
release_value=t['release'] if 'filechk_kernel.release = $(srctree)/scripts/setlocalversion' in makefile else suffix
script.write_text('#!/bin/sh\nprintf "%s\\n" '+repr(release_value)+'\n')
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
