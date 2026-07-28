#!/usr/bin/env python3
"""Verify every manifest entry's content actually reached the assembly.

The tree-vs-baseline diff used during the applyPatches migration was the WRONG
oracle: the baseline was itself defective (it under-carried #4477, and encoded a
different design than the branch for #4505 and show-remote-host-name). Anything
resolved by copying from baseline silently inherited those defects.

This checks the right thing instead: for each entry, every substantive line the
BRANCH adds relative to upstream main should be present in the built tree.

Non-zero MISSING is not automatically a bug -- a later entry may legitimately
have rewritten those lines. But every one needs an explanation, and a large
count on an entry whose conflicts were resolved by copying from a reference tree
is a strong signal that the entry's content was dropped.

Run after every rebuild, before switching.
"""
import argparse
import hashlib
import json
import subprocess
import tomllib
import sys
from pathlib import Path
ASSEMBLY_ROOT=Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("assembly", nargs="?", default="HEAD", help="assembly revision to audit")
parser.add_argument(
    "--repo",
    default=ASSEMBLY_ROOT / "t3code",
    help="path to the T3 Code checkout (defaults to the repository submodule)",
)
args = parser.parse_args()
ASSEMBLY=args.assembly
REPO=str(Path(args.repo).resolve())
EXCEPTIONS=tomllib.load(open(ASSEMBLY_ROOT / "audit-exceptions.toml", "rb"))
EXCEPTIONS={exception["entry"]: exception for exception in EXCEPTIONS.get("exception", [])}
def sh(*a):
    return subprocess.run(["git","-C",REPO,*a],capture_output=True,text=True).stdout

entries=[]
for manifest_path, lock_path in [
    (ASSEMBLY_ROOT / "assembly.toml", ASSEMBLY_ROOT / "assembly.lock.json"),
    (ASSEMBLY_ROOT / "thread-picker.toml", ASSEMBLY_ROOT / "thread-picker.lock.json"),
]:
    lock=json.loads(lock_path.read_text())
    main=lock["upstream_main"]
    locks={
        result["entry"]: result["oid"]
        for result in lock.get("entries", [])
        if result.get("oid")
    }
    d=tomllib.load(open(manifest_path,"rb"))
    for e in d["entry"]:
        label=str(e.get("pr") or e.get("branch"))
        if e.get("pin"):
            entries.append((label, locks.get(label, e["pin"]), main))

def significant(line):
    s=line[1:].strip()
    return len(s)>12 and not s.startswith(("//","/*","*","import ","}",")","],"))

print(f"{'entry':<34} {'files':>5} {'addlines':>8} {'MISSING':>8}")
bad=[]
explained=[]
used_exceptions=set()
for label,pin,main in entries:
    oid=sh("rev-parse",f"{pin}^{{commit}}").strip()
    if not oid: print(f"{label:<34}  UNRESOLVED"); continue
    files=[f for f in sh("diff","--name-only",f"{main}...{oid}").split() if f.endswith((".ts",".tsx"))]
    tot=miss=0
    worst={}
    missing_lines=[]
    for f in files:
        added=[l for l in sh("diff","-U0",f"{main}...{oid}","--",f).splitlines()
               if l.startswith("+") and not l.startswith("+++") and significant(l)]
        if not added: continue
        cur=sh("show",f"{ASSEMBLY}:{f}")
        if not cur: 
            miss+=len(added); tot+=len(added); worst[f]=len(added); continue
        m2=[l for l in added if l[1:].strip() not in cur]
        tot+=len(added); miss+=len(m2)
        if m2:
            worst[f]=len(m2)
            missing_lines.extend((f, line[1:].strip()) for line in m2)
    exception=EXCEPTIONS.get(str(label))
    digest=hashlib.sha256(
        "\n".join(f"{file}\0{line}" for file,line in sorted(missing_lines)).encode()
    ).hexdigest()
    accepted = (
        miss > 0
        and exception is not None
        and exception["missing_count"] == miss
        and exception["missing_sha256"] == digest
    )
    flag="  <-- EXPLAINED" if accepted else "  <-- LOSS" if miss>0 else ""
    print(f"{label:<34} {len(files):>5} {tot:>8} {miss:>8}{flag}")
    if accepted:
        explained.append((label, exception["reason"]))
        used_exceptions.add(str(label))
    elif miss:
        bad.append((label,miss,digest,worst))

print("\n=== detail for entries with missing lines ===")
for label,miss,digest,worst in sorted(bad,key=lambda x:-x[1]):
    print(f"\n{label}: {miss} added lines absent from the built tree ({digest})")
    for f,n in sorted(worst.items(),key=lambda x:-x[1])[:4]:
        print(f"    {n:>4}  {f}")

if explained:
    print("\n=== reviewed later rewrites ===")
    for label, reason in explained:
        print(f"\n{label}: {reason}")

stale_exceptions=sorted(set(EXCEPTIONS) - used_exceptions)
if stale_exceptions:
    print("\n=== stale audit exceptions ===")
    for label in stale_exceptions:
        print(f"{label}: no longer matches an audited missing-line set")

if bad or stale_exceptions:
    raise SystemExit(1)
