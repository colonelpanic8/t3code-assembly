# t3code-assembly

Ivan’s reproducible [fork-assembler](https://github.com/colonelpanic8/fork-assembler)
stack for T3 Code.

The repository pins upstream T3 Code as `t3code/`, records the ordered topics
in `manifest.toml`, pins their exact OIDs and resulting tree in
`manifest.lock.json`, and tracks conflict-hunk resolutions under
`resolutions/rerere/`. The assembled T3 Code branch is generated output; this
repository is the source of truth.

```sh
git clone --recurse-submodules https://github.com/colonelpanic8/t3code-assembly
cd t3code-assembly
direnv allow
just locked
```

`just locked` is network-free once every pinned object is present in the
submodule object database. Run `just build` first when a fresh checkout needs
to fetch pinned topic objects.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for the model and
[BUILDING.md](BUILDING.md) for the authoritative operating procedure.

## Build the Railbird iOS beta

Run `bin/build-ios-testflight.py` on a Mac with Xcode installed. The script checks
out the locked assembly in `.worktrees/ios-railbird` and applies the Railbird
bundle ID and team to that build directory. It preserves the assembly's widget
and share extensions and disables Expo OTA updates.

Provide App Store profiles for `ai.railbird.t3code`,
`ai.railbird.t3code.widgets`, and `ai.railbird.t3code.sharing` as
`<bundle-id>.mobileprovision` files in one directory. Provide the distribution
private key and certificate together as an age-encrypted PEM. The script streams
the decrypted identity into the signer without writing a plaintext key.

```sh
python3 bin/build-ios-testflight.py --build-number 1 \
  --profiles "$T3CODE_IOS_PROFILES" \
  --identity "$T3CODE_IOS_SIGNING_IDENTITY"
```

Choose a build number that App Store Connect has not received. The signed IPA
and its assembly provenance are written under `.worktrees/ios-railbird/ios-release/`.
Use `--skip-prebuild` to reuse the native project. To sign an existing archive
again, also pass `--skip-archive` with the same build number.

To validate and upload the IPA, add `--upload` and set `ASC_KEY_ID` and
`ASC_ISSUER_ID`. Apple's upload tool reads the API private key from
`~/.appstoreconnect/private_keys/AuthKey_<ASC_KEY_ID>.p8`.

After Apple finishes processing, add the build to the **Internal** group in
[T3 Code Assembly TestFlight](https://appstoreconnect.apple.com/apps/6824648403/testflight).
That group carries the existing Paseo Assembly testers.
