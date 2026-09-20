# Setting up TypeScript to ArkTS on a Windows laptop

`parity doctor` has one line, "TypeScript to ArkTS", that names the first thing still missing. Repeat until it says `DevEco target: ...`.

Needs a HUAWEI ID (free) and about 15 GB of disk. None of this can be scripted: every download and the signing step sit behind the HUAWEI ID sign-in.

1. **DevEco Studio.** Download the newest release from <https://developer.huawei.com/consumer/en/download/> (sign in first). It must offer HarmonyOS 6.1.1 (API 24). Do not use `winget install Huawei.DevEco`: that is a 3.1 release and cannot build this project. Install to the default `C:\Program Files\Huawei\DevEco Studio`. Anywhere else: set `PARITY_DEVECO_HOME` to it.
2. **Emulator.** In DevEco Studio: Tools > Device Manager > sign in > New Emulator > Wearable > download the API 24 image > start it. Windows must have Hyper-V on ("Virtual Machine Platform" and "Windows Hypervisor Platform" in Windows Features). Keep exactly one emulator running, or set `PARITY_HDC_TARGET` to the one to use (`hdc list targets`).
3. **Signing.** In DevEco Studio open `experiments/arkts-smoke`. File > Project Structure > Signing Configs > tick "Automatically generate signature" > sign in > OK. That fills in the local `experiments/arkts-smoke/build-profile.json5` (git-ignored, already created from the example). Parity finds that file by itself; `PARITY_ARKTS_SIGNING_PROFILE` points it somewhere else.
4. **Check the device path without any models:**
   `powershell -ExecutionPolicy Bypass -File .\experiments\arkts-smoke\scripts\run-arkts-smoke.ps1` should end with `ARKTS_SMOKE_PASS:10`.
5. **Check Parity on it without any models** (known-good ArkTS through the checker):
   `parity` then `/mode arkts` then `/verify tests/arkts_known_good`.
6. **The team run:** `/mode arkts` then `/migrate`.
