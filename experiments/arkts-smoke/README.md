# ArkTS smoke test with official DevEco Studio

From the repository root, with an official DevEco emulator already running:

```powershell
powershell -ExecutionPolicy Bypass -File .\experiments\arkts-smoke\scripts\run-arkts-smoke.ps1
```

The runner uses the Node, JBR, Hvigor, SDK and HDC installed under
`C:\Program Files\Huawei\DevEco Studio`. It builds the existing ArkTS project,
installs its signed HAP, launches the manifest's main ability, and succeeds only
when a fresh HiLog stream contains `ARKTS_SMOKE_PASS:10` from `ArkTSSmoke`.
Logs are in `.smoke-logs` beside this README. It does not start or modify emulators.

The project matches the DevEco-created project's HarmonyOS 6.1.1 / API 24 model.
Its manifest supports phone and wearable devices; the currently connected
DevEco emulator is a wearable. `Smoke.ets` is called in `EntryAbility.onCreate`.
The existing local signing material is reused by the official signer. If signing
material is missing, configure this project's Signing Configs in DevEco Studio.

Options: `-Target <hdc-target>` for multiple connected devices,
`-DevEcoHome <installation-directory>`, and `-BuildOnly` for just the signed HAP.
No npm build-tool installation is required: setup links the two build modules
to the official DevEco installation. Old npm modules are preserved locally in
`.legacy-node_modules`; the old custom emulator scripts are retired and are not
part of this flow.

## Signing after cloning

The local `build-profile.json5` and `signatures/` are intentionally ignored.
Signing credentials were not transferred from htnTemp. The local build profile
starts as a copy of the credential-free example. Open this folder in DevEco
Studio and configure Signing Configs before running the test. On a fresh clone,
first copy `build-profile.example.json5` to `build-profile.json5`.
No signing passwords or certificates belong in the shareable template.
