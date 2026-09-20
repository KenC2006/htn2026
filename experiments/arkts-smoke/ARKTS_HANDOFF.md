# ArkTS handoff

> Migration status: app sources and official DevEco scripts were copied from
> htnTemp. Signing was configured locally in DevEco for this checkout
> (`experiments/arkts-smoke/build-profile.json5`, gitignored, absolute local
> paths only). The smoke test has been rerun in this checkout and passes:
> `ARKTS_SMOKE_PASS:10` on the connected wearable emulator.


Use the official DevEco Studio installation as the source of truth:
`C:\Program Files\Huawei\DevEco Studio`.

Run the verified end-to-end smoke test from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\experiments\arkts-smoke\scripts\run-arkts-smoke.ps1
```

The runner builds with DevEco's bundled Node, JBR, Hvigor, SDK, and `hdc.exe`.
It uses the already-running emulator returned by `hdc list targets`; it never
starts or repairs an emulator. A successful run must include this HiLog line:

```text
ARKTS_SMOKE_PASS:10
```

ArkTS execution begins in `entry/src/main/ets/entryability/EntryAbility.ets`.
It imports and calls `Smoke.ets` from `onCreate`, which emits the marker via
the `ArkTSSmoke` HiLog tag. The signed HAP is written to
`entry/build/default/outputs/default/entry-default-signed.hap`.

The connected official emulator is a wearable. Keep `wearable` in
`entry/src/main/module.json5` unless the target changes. The project uses
HarmonyOS 6.1.1 / API 24 and requires a local DevEco signing configuration.
If signing fails, configure Signing Configs in DevEco Studio instead of
creating a separate signing toolchain.

Do not use `.arkts-toolchain`, custom QEMU, or the retired emulator scripts.
Runtime logs are saved in `.smoke-logs/`.
