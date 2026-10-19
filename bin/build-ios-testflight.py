#!/usr/bin/env python3
"""Archive the locked assembly for Railbird and optionally upload to TestFlight."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tomllib


ROOT = Path(__file__).resolve().parent.parent
BUNDLE_ID = "ai.railbird.t3code"
TEAM_ID = "K86D8LK4W6"


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def tool(name, package):
    executable = shutil.which(name)
    return [executable] if executable else ["nix", "shell", f"nixpkgs#{package}", "--command", name]


def plist(path):
    return plistlib.loads(path.read_bytes())


def profile(path, bundle_id):
    raw = path.read_bytes()
    data = plistlib.loads(raw[raw.index(b"<?xml"):raw.index(b"</plist>") + 8])
    if data["ExpirationDate"] <= datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None):
        raise SystemExit(f"Expired provisioning profile: {path}")
    if data["Entitlements"]["application-identifier"] != f"{TEAM_ID}.{bundle_id}":
        raise SystemExit(f"Provisioning profile does not match {bundle_id}: {path}")
    if data["Entitlements"].get("get-task-allow") or "ProvisionedDevices" in data:
        raise SystemExit(f"Expected an App Store distribution profile: {path}")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, default=ROOT / ".worktrees/ios-railbird")
    parser.add_argument("--build-number", required=True)
    parser.add_argument("--profiles", type=Path,
                        help="Directory containing <bundle-id>.mobileprovision for each target")
    parser.add_argument("--identity", type=Path, help="Age-encrypted signing PEM")
    parser.add_argument("--ssh-key", type=Path, default=Path.home() / ".ssh/id_ed25519")
    parser.add_argument("--skip-prebuild", action="store_true")
    parser.add_argument("--skip-archive", action="store_true")
    parser.add_argument("--prebuild-only", action="store_true")
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,2}", args.build_number):
        parser.error("--build-number must contain one to three dot-separated numbers")
    lock = json.loads((ROOT / "manifest.lock.json").read_text())["build"]
    worktree = args.worktree.resolve()
    if not worktree.is_relative_to(ROOT / ".worktrees"):
        parser.error("Build worktree must be under .worktrees/")
    if not worktree.exists():
        worktree.parent.mkdir(exist_ok=True)
        source = ROOT / "t3code"
        available = subprocess.run(
            ["git", "-C", str(source), "cat-file", "-e", lock["commit"] + "^{commit}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if available.returncode:
            manifest = tomllib.loads((ROOT / "manifest.toml").read_text())
            remote = manifest["remotes"][manifest["publish"]["remote"]]
            run(["git", "-C", str(source), "fetch", remote, lock["commit"]])
        run(["git", "-C", str(ROOT / "t3code"), "worktree", "add", "--detach",
             str(worktree), lock["commit"]])
    head = subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"], text=True).strip()
    if head != lock["commit"]:
        parser.error("Build worktree must be at the exact manifest.lock.json commit")
    changed = subprocess.check_output(
        ["git", "-C", str(worktree), "diff", "HEAD", "--name-only"], text=True
    ).splitlines()
    if set(changed) - {"apps/mobile/app.config.ts"}:
        parser.error("Build worktree has source changes outside the Railbird configuration")
    if not args.prebuild_only and (args.profiles is None or args.identity is None):
        parser.error("--profiles and --identity are required for signing")
    if args.upload and not all(os.environ.get(key) for key in ("ASC_KEY_ID", "ASC_ISSUER_ID")):
        parser.error("--upload requires ASC_KEY_ID and ASC_ISSUER_ID")
    if not args.prebuild_only:
        profiles = args.profiles.resolve()
        for suffix in ("", ".widgets", ".sharing"):
            profile(profiles / f"{BUNDLE_ID}{suffix}.mobileprovision", BUNDLE_ID + suffix)
    mobile = worktree / "apps/mobile"
    config_path = mobile / "app.config.ts"
    original = subprocess.check_output(["git", "-C", str(worktree), "show",
                                        "HEAD:apps/mobile/app.config.ts"], text=True)
    overlay = original
    for before, after in [( 'iosBundleIdentifier: "com.t3tools.t3code.assembly"',
                           f'iosBundleIdentifier: "{BUNDLE_ID}"'),
                          ('appleTeamId: "ARK85ZXQ4Z"', f'appleTeamId: "{TEAM_ID}"')]:
        if overlay.count(before) != 1:
            parser.error(f"Upstream configuration changed; review overlay: {before}")
        overlay = overlay.replace(before, after)
    if config_path.read_text() not in (original, overlay):
        parser.error("Refusing to replace unrelated changes to app.config.ts")
    config_path.write_text(overlay)
    env = dict(os.environ, APP_VARIANT="assembly", T3CODE_MOBILE_UPDATES_ENABLED="0",
               CI="1", LC_ALL="en_US.UTF-8", LANG="en_US.UTF-8",
               NODE_BINARY=shutil.which("node"), EXPO_NO_GIT_STATUS="1")
    output = worktree / "ios-release"
    output.mkdir(exist_ok=True)
    (output / ".gitignore").write_text("*\n")
    if not args.skip_prebuild:
        run(tool("pnpm", "pnpm") + ["install", "--frozen-lockfile"], cwd=worktree, env=env)
        run(tool("pnpm", "pnpm") + ["exec", "expo", "prebuild", "--platform", "ios", "--no-install"],
            cwd=mobile, env=env)
        run(tool("pod", "cocoapods") + ["install"], cwd=mobile / "ios", env=env)
    if args.prebuild_only:
        return
    expo_settings = mobile / "ios/T3CodeAssembly/Supporting/Expo.plist"
    if plist(expo_settings).get("EXUpdatesEnabled") is not False:
        parser.error("The assembly binary must have Expo OTA updates disabled")
    workspace, = (mobile / "ios").glob("*.xcworkspace")
    scheme = workspace.stem
    archive = output / f"{scheme}-{args.build_number}.xcarchive"
    if not args.skip_archive:
        if archive.exists():
            parser.error(f"Archive already exists; use --skip-archive or a new build number: {archive}")
        for target in (scheme, "ExpoWidgetsTarget", "expo-sharing-extension"):
            info_path = mobile / "ios" / target / "Info.plist"
            info = plist(info_path)
            info["CFBundleVersion"] = args.build_number
            info_path.write_bytes(plistlib.dumps(info))
        run(["xcodebuild", "-workspace", str(workspace), "-scheme", scheme,
             "-hideShellScriptEnvironment",
             "-configuration", "Release", "-destination", "generic/platform=iOS",
             "-archivePath", str(archive), "-derivedDataPath", str(output / "DerivedData"),
             f"CURRENT_PROJECT_VERSION={args.build_number}", "CODE_SIGNING_ALLOWED=NO",
             "CODE_SIGNING_REQUIRED=NO", "CODE_SIGN_IDENTITY=", "CODE_SIGN_ENTITLEMENTS=", "archive"],
            cwd=mobile, env=env)
    app, = (archive / "Products/Applications").glob("*.app")
    metadata = plist(app / "Info.plist")
    if metadata["CFBundleIdentifier"] != BUNDLE_ID or metadata["CFBundleVersion"] != args.build_number:
        parser.error("Archived app identity/build number differs from the requested release")
    stage = output / f"stage-{args.build_number}"
    if stage.exists():
        shutil.rmtree(stage)
    staged_app = stage / "Payload" / app.name
    shutil.copytree(app, staged_app, symlinks=True)
    signing_args = tool("rcodesign", "rcodesign") + ["sign", "--pem-source", "/dev/stdin"]
    for target in [staged_app, *sorted(staged_app.glob("PlugIns/*.appex"))]:
        target_metadata = plist(target / "Info.plist")
        identifier = target_metadata["CFBundleIdentifier"]
        for key in ("CFBundleVersion", "CFBundleShortVersionString"):
            if target_metadata[key] != metadata[key]:
                parser.error(f"Extension {identifier} has a different {key} from its app")
        source = profiles / f"{identifier}.mobileprovision"
        granted = profile(source, identifier)["Entitlements"]
        native_entitlements = mobile / "ios" / target.stem / f"{target.stem}.entitlements"
        requested = plist(native_entitlements)
        entitlements = {
            key: granted[key]
            for key in ("application-identifier", "com.apple.developer.team-identifier",
                        "beta-reports-active", "get-task-allow")
        }
        for key, value in requested.items():
            if key not in granted:
                parser.error(f"Profile does not authorize {key} for {identifier}")
            if key == "aps-environment":
                value = granted[key]
            if key == "keychain-access-groups":
                value = [group.replace("$(AppIdentifierPrefix)", TEAM_ID + ".") for group in value]
            entitlements[key] = value
        entitlement_path = output / f"{identifier}.entitlements.plist"
        entitlement_path.write_bytes(plistlib.dumps(entitlements))
        shutil.copyfile(source, target / "embedded.mobileprovision")
        executable = target / target_metadata["CFBundleExecutable"]
        scope = str(executable.relative_to(staged_app))
        signing_args += ["--entitlements-xml-file", f"{scope}:{entitlement_path}"]
    identity = subprocess.check_output(tool("age", "age") + ["--decrypt", "-i", str(args.ssh_key),
                                                            str(args.identity.resolve())])
    run(signing_args + [str(staged_app)], input=identity, env=env)
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(staged_app)])
    ipa = output / f"T3CodeAssembly-{metadata['CFBundleShortVersionString']}-{args.build_number}.ipa"
    if ipa.exists():
        ipa.unlink()
    run(["/usr/bin/zip", "-qry", str(ipa), "Payload"], cwd=stage)
    with ipa.open("rb") as artifact:
        digest = hashlib.file_digest(artifact, "sha256").hexdigest()
    (output / "provenance.json").write_text(json.dumps({
        "assemblyCommit": head, "assemblyTree": lock["built_tree"], "bundleId": BUNDLE_ID,
        "appleTeamId": TEAM_ID, "version": metadata["CFBundleShortVersionString"],
        "buildNumber": args.build_number, "ipa": str(ipa), "sha256": digest,
    }, indent=2) + "\n")
    print(f"Signed IPA: {ipa}", flush=True)
    if args.upload:
        auth = ["--apiKey", os.environ["ASC_KEY_ID"], "--apiIssuer", os.environ["ASC_ISSUER_ID"]]
        run(["xcrun", "altool", "--validate-app", "-f", str(ipa), "-t", "ios", *auth])
        run(["xcrun", "altool", "--upload-app", "-f", str(ipa), "-t", "ios", *auth])


if __name__ == "__main__":
    main()
