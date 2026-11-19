# FinaSU + SUSFS exact kernel automation

This workflow builds complete kernels and AnyKernel3 packages for the kernel
profiles in `targets.json`. The inventory contains kernel facts only.

## Routine operation

`FinaSU exact kernel fleet` checks the latest published, non-prerelease FinaSU
release at minute 17 and 47 each hour. The workflow must remain on the default
branch. Each batch resolves the release commit and its official manager once;
all target jobs use that same commit and manager certificate. SUSFS and patch
dependencies are pinned by `policy.json`.

Successful identical inputs are skipped. Failed targets retry after six hours,
at most three times per input fingerprint. Manual dispatch supports a target
list and a force switch. Matrix jobs run independently, at most four at once.
State is stored in `state.json` on the `finasu-build-state` branch. Scheduled
runs share a concurrency group and do not cancel an active build.

Validated packages and manager APKs are retained as a GitHub prerelease.
Detailed logs, configurations, symbol tables and receipts are Actions artifacts.
Only a target that passed compilation, full release-string validation, requested
configuration validation and baseline symbol-CRC comparison is published.
Successful static validation does not record a phone boot test.

## Fixed options

- FinaSU built into the kernel, SUSFS v2.4.0 and SUSFS Inline Hook.
- ThinLTO, ZRAM/LZ4KD, Baseband Guard and BBRv3/IPSet/FQ/CIFS enabled.
- KPM, Re-Kernel, Droidspaces and the separate NoMount kernel integration off.
- Magic Mount is a runtime mounting choice; no device mounting configuration is
  changed by this cloud build. FinaSU's manager also has its own mounting engine.
- Requested feature failures stop that target; no fallback to a different hook,
  smaller feature set or different kernel version.

## Updating the WSL inventory

Run `python3 scripts/fleet/export_wsl.py /home/shizhe/hk-driver-builds/index.json`
from WSL, using this checkout's absolute script path when needed. Review and
commit the resulting `targets.json`, `configs/` and `abi/` files. New target IDs
remain disabled until `source-overrides.json` contains a reviewed source mapping.
Local paths, accounts, ADB addresses and account-to-kernel mappings are not
exported. The GitHub runner builds the committed inventory independently of WSL.

The build reads saved kernel configs, not module-package metadata. It fetches
the pinned source and matching kernel toolchain, applies the FinaSU/SUSFS and
requested feature patches, and uses native Kbuild with the saved configuration.
Source commits differing from the original kernel banner are explicitly marked
as recorded vendor/reference sources and must pass the target symbol baseline.

`opd2508-66118-ref` is recorded but disabled because the available saved source
is a different build and its target config is missing. It is shown as
`source-pending` in every selection summary.

## Checks and rollback

Run `python3 -m unittest discover -s tests -v`, Bash syntax validation and workflow
YAML validation before changing the workflow. Keep `recipe_revision` current
when changing compilation semantics so successful old inputs are rebuilt.

Disable `finasu-fleet.yml` to stop scheduled runs. Previous packages remain in
their releases; revert the automation commits to restore the earlier workflow.
No boot partition, installed manager or device module is modified by this job.
