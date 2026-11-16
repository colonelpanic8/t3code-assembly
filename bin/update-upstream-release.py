#!/usr/bin/env python3
"""Pin the upstream Nightly release used to version the Nix assembly."""

import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "manifest.lock.json").read_text())
BASE = LOCK["pins"]["base"]
NAMESPACE = "refs/fork-assembler/base-tags/"


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT / "t3code"), *args], text=True)


decorations = git(
    "log", "--first-parent", "--simplify-by-decoration",
    f"--decorate-refs={NAMESPACE}v*-nightly.*", "--decorate=full", "--format=%D", BASE,
)
tag = re.search(re.escape(NAMESPACE) + r"(v[^,\s]+-nightly\.[^,\s]+)", decorations)[1]
release = {"commit": BASE, "tag": tag, "distance": int(git("rev-list", "--count", f"{NAMESPACE}{tag}..{BASE}"))}
replacement = """  upstream = let
    recorded = buildInfo.upstream or {};
    release = builtins.fromJSON (builtins.readFile ./assembly-upstream-release.json);
  in
    assert lib.assertMsg (release.commit == recorded.commit)
    "Refresh the assembly release metadata with bin/update-upstream-release.py";
      recorded // release;
"""
path = ROOT / "patches/assembly-update-notice-nix-wrapper.patch"
patch = path.read_text().split("diff --git a/nix/assembly-upstream-release.json")[0]
needle = "+  upstream = buildInfo.upstream or {};\n"
if needle in patch:
    patch = patch.replace(needle, "".join("+" + line for line in replacement.splitlines(True)))
else:
    assert "+      recorded // release;" in patch
parts = patch.splitlines(True)
offset = 0
for index, line in enumerate(parts):
    match = re.match(r"@@ -(\d+),(\d+) \+(\d+),(\d+) @@(.*)", line)
    if not match:
        continue
    end = next((i for i in range(index + 1, len(parts)) if parts[i].startswith(("@@", "diff --git"))), len(parts))
    body = parts[index + 1:end]
    old_count = sum(line.startswith((" ", "-")) for line in body)
    new_count = sum(line.startswith((" ", "+")) for line in body)
    parts[index] = f"@@ -{match[1]},{old_count} +{int(match[3]) + offset},{new_count} @@{match[5]}\n"
    offset += new_count - int(match[4])
patch = "".join(parts)
lines = json.dumps(release, indent=2).splitlines()
patch += "diff --git a/nix/assembly-upstream-release.json b/nix/assembly-upstream-release.json\nnew file mode 100644\n--- /dev/null\n+++ b/nix/assembly-upstream-release.json\n"
patch += f"@@ -0,0 +1,{len(lines)} @@\n" + "".join("+" + line + "\n" for line in lines)
path.write_text(patch)
print(f"Nix release: {tag}+{release['distance']}.g{BASE[:8]}")
