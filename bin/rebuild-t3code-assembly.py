#!/usr/bin/env python3
"""Build the personal T3 Code assembly from an ordered manifest.

The integration branch is a build artifact. Refresh and reproduce regenerate it
from scratch; extend preserves the locked prefix and merges only newly appended
entries. Nothing is ever committed to it directly and nothing is based on it.

Modes:
  reproduce  merge each entry at the OID recorded in the manifest/lock.
             Deterministic; used to prove a rebuild reproduces a known tree.
  refresh    merge each entry at its current branch head, picking up movement.
  extend     start at the locked pre-epilogue commit, merge only entries
             appended to the locked manifest, then reapply epilogues.

On a merge conflict the script stops, leaving the conflicted merge in the build
worktree for a human or agent to resolve, then resumes with --continue.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

# The assembly repository owns the manifests, locks, epilogue patches, and a
# pinned T3 Code checkout. Build worktrees live beside (not inside) the
# submodule so the submodule remains clean.
ASSEMBLY_ROOT = Path(__file__).resolve().parent.parent
SOURCE_REPO = ASSEMBLY_ROOT / "t3code"
WORKTREE_ROOT = ASSEMBLY_ROOT / ".worktrees"
DEFAULT_MANIFEST = ASSEMBLY_ROOT / "assembly.toml"

# Set from --manifest. A GROUP manifest (e.g. thread-picker.toml) is an
# ordinary manifest whose output branch is pinned as a single entry in the main
# one -- the same tool, two levels, like a subsystem tree under linux-next.
MANIFEST = DEFAULT_MANIFEST
LOCK = ASSEMBLY_ROOT / "assembly.lock.json"


def set_manifest(path: Path) -> None:
    global MANIFEST, LOCK, STATE_NAME, WORKTREE_NAME
    MANIFEST = path.resolve()
    LOCK = MANIFEST.parent / f"{MANIFEST.stem}.lock.json"
    # Per-manifest worktree and state, so a group build and the main build can
    # be in progress at the same time without clobbering each other.
    STATE_NAME = f"{MANIFEST.stem}-state.json"
    WORKTREE_NAME = f"{MANIFEST.stem}-build"


STATE_NAME = "t3code-assembly-state.json"
WORKTREE_NAME = "t3code-assembly-build"

# Written into the integration branch as its final commit, so the built app can
# show what it is made of. It is deliberately NOT the lock: the lock is intent
# plus run metadata and lives here, while this is the subset that has to travel
# with the artifact. Keep it a pure function of (upstream base, resolved entry
# OIDs) -- no timestamps, no per-run counters -- or an unchanged rebuild starts
# producing a changed tree and the "no flake bump needed" signal dies.
# Compatibility interface consumed by the currently carried provenance topic.
# Renaming it requires a coordinated T3 Code topic change; it is not a name for
# this repository or workflow.
PROVENANCE_FILE = "stack-build-info.json"
PROVENANCE_SCHEMA_VERSION = 1


class Fail(Exception):
    pass


def git(repo: Path, *args: str, check: bool = True, capture: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=capture,
        text=True,
    )
    if check and proc.returncode != 0:
        raise Fail(
            f"git {' '.join(args)} failed ({proc.returncode})\n"
            f"{(proc.stderr or '').strip()}"
        )
    return (proc.stdout or "").strip()


def git_ok(repo: Path, *args: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    ).returncode == 0


def ensure_remote(repo: Path, name: str, url: str) -> None:
    current = git(repo, "remote", "get-url", name, check=False)
    if current:
        if current != url:
            git(repo, "remote", "set-url", name, url)
    else:
        git(repo, "remote", "add", name, url)


def configure_source_repo(repo: Path, manifest: dict) -> None:
    if not git_ok(repo, "rev-parse", "--git-dir"):
        raise Fail(
            f"{repo} is not an initialized T3 Code checkout; "
            "run `git submodule update --init`"
        )
    ensure_remote(repo, "origin", manifest["upstream"])
    ensure_remote(repo, "fork", manifest["fork"])
    git(repo, "config", "rerere.enabled", "true")
    git(repo, "config", "rerere.autoupdate", "false")


def pin_source_checkout(repo: Path, upstream_commit: str) -> None:
    if repo != SOURCE_REPO.resolve():
        return
    dirty = git(repo, "status", "--porcelain")
    if dirty:
        raise Fail(
            "the T3 Code submodule has local changes; cannot update its pin:\n"
            f"{dirty}"
        )
    if git(repo, "rev-parse", "HEAD") != upstream_commit:
        git(repo, "checkout", "--detach", upstream_commit, capture=False)
    print(f"pinned t3code submodule to upstream base {upstream_commit}")


def load_manifest() -> dict:
    with MANIFEST.open("rb") as handle:
        return tomllib.load(handle)


def entry_label(entry: dict) -> str:
    if entry.get("pr"):
        return f"#{entry['pr']}"
    return entry.get("branch", entry.get("ref", "<unnamed>"))


def resolve_entry(repo: Path, entry: dict, mode: str) -> str:
    """Return the commit OID this entry should be merged at."""
    kind = entry.get("kind")
    pin = entry.get("pin")

    if mode == "reproduce":
        if not pin:
            raise Fail(
                f"{entry_label(entry)}: reproduce mode needs a `pin`, none recorded"
            )
        # External PR heads are not reachable from any local branch, so the
        # pinned OID may simply not be fetched yet.
        if kind == "external" and not git_ok(repo, "cat-file", "-e", f"{pin}^{{commit}}"):
            ref = entry["ref"]
            git(repo, "fetch", "origin",
                f"+{ref}:refs/t3code-assembly/{ref.replace('/', '-')}", check=False)
        if not git_ok(repo, "cat-file", "-e", f"{pin}^{{commit}}"):
            raise Fail(
                f"{entry_label(entry)}: pinned OID {pin} not present locally; "
                "fetch the branch or the PR ref first"
            )
        return git(repo, "rev-parse", f"{pin}^{{commit}}")

    if kind == "external":
        ref = entry["ref"]
        git(repo, "fetch", "origin", f"+{ref}:refs/t3code-assembly/{ref.replace('/', '-')}")
        return git(repo, "rev-parse", f"refs/t3code-assembly/{ref.replace('/', '-')}")

    branch = entry["branch"]
    # Both PR topics and fork-local carry topics are published on the fork.
    # Prefer the remote-tracking ref so refresh does not depend on which local
    # branches happen to exist in this checkout.
    candidates = [f"fork/{branch}", branch]
    for candidate in candidates:
        if git_ok(repo, "rev-parse", "--verify", candidate):
            return git(repo, "rev-parse", candidate)
    raise Fail(f"{entry_label(entry)}: none of {candidates} resolve")


def check_absorbed(repo: Path, oid: str, main: str) -> bool:
    """True when upstream already contains this entry -- a drop candidate."""
    return git_ok(repo, "merge-base", "--is-ancestor", oid, main)


def prepare_worktree(repo: Path, path: Path, main: str) -> None:
    if path.exists():
        git(repo, "worktree", "remove", "--force", str(path), check=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "--detach", str(path), main)


def topic_results(lock: dict) -> list[dict]:
    return [result for result in lock.get("entries", []) if result.get("status") != "epilogue"]


def prepare_extend(
    repo: Path, worktree: Path, manifest: dict, entries: list[dict]
) -> tuple[str, list[dict], int]:
    if not LOCK.exists():
        raise Fail("extend mode needs an existing lock; run refresh first")

    lock = json.loads(LOCK.read_text())
    locked_manifest = lock.get("manifest_entries")
    if locked_manifest is None:
        raise Fail(
            "the lock predates extend-mode manifest snapshots; run refresh first"
        )
    if len(entries) <= len(locked_manifest):
        raise Fail("extend mode needs at least one newly appended manifest entry")
    if entries[: len(locked_manifest)] != locked_manifest:
        raise Fail(
            "extend requires the previous manifest to be an exact unchanged prefix; "
            "run refresh because an existing entry changed, moved, or was removed"
        )

    branch = manifest["integration_branch"]
    git(repo, "fetch", "fork", branch, capture=False)
    published = git(repo, "rev-parse", f"fork/{branch}")
    if published != lock.get("commit"):
        raise Fail(
            f"fork/{branch} is {published}, but the lock records {lock.get('commit')}; "
            "run refresh or repair the lock before extending"
        )

    base = lock.get("pre_epilogue_commit")
    if not base or not git_ok(repo, "cat-file", "-e", f"{base}^{{commit}}"):
        raise Fail("the lock has no usable pre_epilogue_commit; run refresh first")

    main = lock["upstream_main"]
    if not git_ok(repo, "cat-file", "-e", f"{main}^{{commit}}"):
        raise Fail(f"locked upstream commit {main} is not available locally")

    locked_results = topic_results(lock)
    if len(locked_results) != len(locked_manifest):
        raise Fail(
            "the lock's topic results do not match its manifest snapshot; run refresh"
        )

    prepare_worktree(repo, worktree, base)
    print(f"extending locked assembly: {lock['commit']}")
    print(f"locked upstream main: {main}")
    print(f"new entries: {len(entries) - len(locked_manifest)}")
    return main, locked_results, len(locked_manifest)


def provenance_entry(entry: dict, result: dict, groups: dict) -> dict:
    """One manifest entry as the built app should describe it.

    `entry` is intent from the manifest; `result` is what the rebuild actually
    did with it, which is not the same thing -- refresh resolves branch heads
    that have moved past the recorded pin, and an entry can land ABSORBED or
    EMPTY. Record the resolved OID, not the pin.
    """
    record = {
        "label": entry_label(entry),
        "kind": entry.get("kind", "fork"),
        "status": result.get("status", "merged"),
        "commit": result.get("oid", ""),
    }
    for key in ("pr", "branch", "ref", "summary"):
        if entry.get(key):
            record[key] = entry[key]
    if entry.get("note"):
        record["note"] = entry["note"].strip()
    # A group entry is one merge here and a whole sub-manifest of its own, so
    # inline its members -- otherwise six PRs disappear behind one row.
    children = groups.get(entry.get("branch"))
    if children:
        record["entries"] = children
    return record


def load_group_provenance() -> dict[str, list[dict]]:
    """Group members, keyed by the branch the main manifest pins them as.

    Read from each group's own lock rather than rebuilt here: the group is
    always built first, so its lock is the authority on what went into it.
    Nesting stops at one level, which is as deep as the manifests go.
    """
    groups: dict[str, list[dict]] = {}
    for path in sorted(ASSEMBLY_ROOT.glob("*.lock.json")):
        if path == LOCK:
            continue
        try:
            lock = json.loads(path.read_text())
        except (OSError, ValueError):
            print(f"  warning: ignoring unreadable group lock {path.name}")
            continue

        branch = lock.get("integration_branch")
        manifest_entries = lock.get("manifest_entries")
        results = topic_results(lock)
        if not branch or not manifest_entries:
            continue
        if len(results) != len(manifest_entries):
            print(f"  warning: {path.name} results do not match its manifest; not inlining")
            continue

        groups[branch] = [
            provenance_entry(entry, result, {})
            for entry, result in zip(manifest_entries, results)
        ]
    return groups


def build_provenance(
    repo: Path, manifest: dict, entries: list[dict], results: list[dict], main: str
) -> dict:
    topics = topic_results({"entries": results})
    if len(topics) != len(entries):
        raise Fail(
            f"cannot describe the build: {len(topics)} topic results for "
            f"{len(entries)} manifest entries"
        )

    groups = load_group_provenance()
    records = [
        provenance_entry(entry, result, groups)
        for entry, result in zip(entries, topics)
    ]

    # Epilogues are patches with no branch or OID, but they are content in the
    # build, so the page has to be able to say they are there.
    # Results record the patch basename; the manifest lists it with its
    # `patches/` prefix.
    described = {
        Path(epilogue["patch"]).name: epilogue
        for epilogue in manifest.get("epilogue", [])
    }
    for result in results:
        if result.get("status") != "epilogue":
            continue
        name = result["entry"]
        record = {"label": name, "kind": "epilogue", "status": "epilogue", "commit": ""}
        epilogue = described.get(name, {})
        if epilogue.get("summary"):
            record["summary"] = epilogue["summary"]
        if epilogue.get("note"):
            record["note"] = epilogue["note"].strip()
        records.append(record)

    return {
        "schemaVersion": PROVENANCE_SCHEMA_VERSION,
        "manifest": MANIFEST.name,
        "upstream": {
            "remote": manifest["upstream"],
            "ref": manifest["upstream_ref"],
            "commit": main,
            "subject": git(repo, "log", "-1", "--format=%s", main),
            "date": git(repo, "log", "-1", "--format=%cI", main),
        },
        "fork": {
            "remote": manifest["fork"],
            "branch": manifest["integration_branch"],
        },
        "entries": records,
    }


def commit_provenance(worktree: Path, provenance: dict) -> str:
    """Land the provenance record as the integration branch's final commit.

    It cannot name its own commit -- that OID does not exist until this commit
    is written. The running app gets that from the build instead (T3CODE_BUILD_*
    in apps/web/vite.config.ts); this file supplies everything else.
    """
    (worktree / PROVENANCE_FILE).write_text(json.dumps(provenance, indent=2) + "\n")
    git(worktree, "add", PROVENANCE_FILE)
    if git(worktree, "status", "--porcelain", "--", PROVENANCE_FILE):
        git(worktree, "commit", "-q", "-m", "assembly: record build provenance")
    return git(worktree, "rev-parse", "HEAD")


def read_state(repo: Path, worktree: Path) -> dict | None:
    common = Path(git(worktree, "rev-parse", "--git-dir"))
    if not common.is_absolute():
        common = worktree / common
    candidate = common / STATE_NAME
    if candidate.exists():
        return json.loads(candidate.read_text())
    return None


def write_state(worktree: Path, state: dict) -> None:
    common = Path(git(worktree, "rev-parse", "--git-dir"))
    if not common.is_absolute():
        common = worktree / common
    (common / STATE_NAME).write_text(json.dumps(state, indent=2))


def clear_state(worktree: Path) -> None:
    common = Path(git(worktree, "rev-parse", "--git-dir"))
    if not common.is_absolute():
        common = worktree / common
    (common / STATE_NAME).unlink(missing_ok=True)


def merge_entry(worktree: Path, entry: dict, oid: str) -> bool:
    """Merge one entry. Returns True on clean merge, False if conflicted."""
    label = entry_label(entry)
    message = f"assembly: merge {label} ({entry.get('summary', '')})".strip()
    proc = subprocess.run(
        [
            "git", "-C", str(worktree), "merge", "--no-ff", "--no-edit",
            "-m", message, oid,
        ],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def conflicted_files(worktree: Path) -> list[str]:
    out = git(worktree, "diff", "--name-only", "--diff-filter=U")
    return [line for line in out.splitlines() if line]


def record_epilogue(results: list[dict], patch: Path) -> None:
    """Record an epilogue once, even if a completed state is resumed."""
    if not any(
        result.get("entry") == patch.name and result.get("status") == "epilogue"
        for result in results
    ):
        results.append({"entry": patch.name, "status": "epilogue"})


def run(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    repo = Path(args.repo).resolve()
    configure_source_repo(repo, manifest)
    worktree = WORKTREE_ROOT / WORKTREE_NAME
    entries = manifest["entry"]
    epilogues = manifest.get("epilogue", [])

    if args.cont:
        state = read_state(repo, worktree)
        if not state:
            raise Fail("no in-progress rebuild found; run without --continue")
        if conflicted_files(worktree):
            raise Fail(
                "the build worktree still has unresolved conflicts:\n  "
                + "\n  ".join(conflicted_files(worktree))
            )
        # Commit the resolved merge if one is in progress.
        common = Path(git(worktree, "rev-parse", "--git-dir"))
        if not common.is_absolute():
            common = worktree / common
        main = state["upstream_main"]
        mode = state["mode"]
        results = state["results"]
        conflicts = state["conflicts"]
        stalled = state["next_index"]
        pre_epilogue_commit = state.get("pre_epilogue_commit")

        if (common / "MERGE_HEAD").exists():
            git(worktree, "commit", "--no-edit")
            # The stalled entry is now merged; record it and advance past it.
            # Re-running it would be a no-op merge that falsely reports EMPTY.
            entry = entries[stalled]
            results.append(
                {
                    "entry": entry_label(entry),
                    "oid": resolve_entry(repo, entry, mode),
                    "status": "merged",
                    "conflicted": True,
                }
            )
            print(f"  [{stalled + 1:2}/{len(entries)}] {entry_label(entry):<10} resolved and committed")
            start = stalled + 1
            # Persist immediately: if the very next entry raises (e.g. a bad
            # pin), a stale index would re-merge this one and report it EMPTY.
            write_state(
                worktree,
                {
                    "next_index": start,
                    "upstream_main": main,
                    "mode": mode,
                    "results": results,
                    "conflicts": conflicts,
                },
            )
        else:
            start = stalled
        print(f"resuming at entry {start + 1}/{len(entries)}")
    else:
        print("fetching upstream main and fork...")
        upstream_ref = manifest["upstream_ref"]
        git(repo, "fetch", "origin", upstream_ref, capture=False)
        git(repo, "fetch", "fork", capture=False)
        mode = args.mode
        conflicts = 0
        pre_epilogue_commit = None
        if mode == "extend":
            main, results, start = prepare_extend(repo, worktree, manifest, entries)
        else:
            if mode == "reproduce" and LOCK.exists():
                locked_main = json.loads(LOCK.read_text()).get("upstream_main")
                if not locked_main:
                    raise Fail("reproduce mode needs an upstream_main recorded in the lock")
                if not git_ok(repo, "cat-file", "-e", f"{locked_main}^{{commit}}"):
                    raise Fail(
                        f"locked upstream commit {locked_main} is not available locally"
                    )
                main = locked_main
            else:
                main = git(repo, "rev-parse", f"origin/{upstream_ref}")
            results = []
            start = 0
            print(f"upstream main: {main}")
            prepare_worktree(repo, worktree, main)

    for index in range(start, len(entries)):
        entry = entries[index]
        label = entry_label(entry)
        oid = resolve_entry(repo, entry, mode)

        if check_absorbed(repo, oid, main):
            print(f"  [{index + 1:2}/{len(entries)}] {label:<10} ABSORBED upstream -- drop candidate")
            results.append({"entry": label, "oid": oid, "status": "absorbed"})
            continue

        before = git(worktree, "rev-parse", "HEAD^{tree}")
        clean = merge_entry(worktree, entry, oid)

        if not clean:
            files = conflicted_files(worktree)
            conflicts += 1
            write_state(
                worktree,
                {
                    "next_index": index,
                    "upstream_main": main,
                    "mode": mode,
                    "results": results,
                    "conflicts": conflicts,
                },
            )
            print(f"\n  [{index + 1:2}/{len(entries)}] {label} CONFLICT in {len(files)} file(s):")
            for name in files:
                print(f"      {name}")
            print(f"\n  Resolve in: {worktree}")
            print(f"  Then: {sys.argv[0]} --continue")
            return 2

        after = git(worktree, "rev-parse", "HEAD^{tree}")
        if before == after:
            print(f"  [{index + 1:2}/{len(entries)}] {label:<10} EMPTY -- merge changed nothing, drop candidate")
            results.append({"entry": label, "oid": oid, "status": "empty"})
        else:
            print(f"  [{index + 1:2}/{len(entries)}] {label:<10} merged {oid[:9]}")
            results.append({"entry": label, "oid": oid, "status": "merged"})

        # Persist after EVERY entry, not just on conflict. Otherwise a crash
        # mid-run resumes at a stale index, re-merges already-merged entries,
        # and falsely reports them EMPTY -- corrupting the drop-candidate signal.
        write_state(
            worktree,
            {
                "next_index": index + 1,
                "upstream_main": main,
                "mode": mode,
                "results": results,
                "conflicts": conflicts,
            },
        )

    # Persist this boundary before applying epilogues. A resume after an
    # epilogue conflict must retain the original topic-only base for extend.
    if pre_epilogue_commit is None:
        pre_epilogue_commit = git(worktree, "rev-parse", "HEAD")
    write_state(
        worktree,
        {
            "next_index": len(entries),
            "upstream_main": main,
            "mode": mode,
            "results": results,
            "conflicts": conflicts,
            "pre_epilogue_commit": pre_epilogue_commit,
        },
    )

    # Epilogues are patches, not topics: they are functions of the ASSEMBLED
    # tree (e.g. a migration ID that depends on what the assembly already used),
    # so they cannot exist as branches based on upstream main.
    for epilogue in epilogues:
        patch = ASSEMBLY_ROOT / epilogue["patch"]
        label = epilogue.get("summary", patch.name)
        reverse = subprocess.run(
            ["git", "-C", str(worktree), "apply", "--reverse", "--check", str(patch)],
            capture_output=True,
            text=True,
        )
        # Reverse-applicability alone does not mean "already applied". A patch
        # that DELETES a duplicated block reverse-applies against the surviving
        # copy, because the copies are byte-identical and context matching
        # cannot tell them apart. That silently skipped the EnvironmentIcon
        # dedupe and shipped a tree with two declarations of it. Only trust the
        # reverse check when the patch also cannot be applied forwards.
        forward = subprocess.run(
            ["git", "-C", str(worktree), "apply", "--check", str(patch)],
            capture_output=True,
            text=True,
        )
        if reverse.returncode == 0 and forward.returncode != 0:
            print(f"  epilogue {patch.name} already applied, skipping")
            record_epilogue(results, patch)
            write_state(worktree, {"next_index": len(entries), "upstream_main": main, "mode": mode, "results": results, "conflicts": conflicts, "pre_epilogue_commit": pre_epilogue_commit})
            continue
        proc = subprocess.run(
            ["git", "-C", str(worktree), "apply", "--3way", str(patch)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            write_state(worktree, {"next_index": len(entries), "upstream_main": main, "mode": mode, "results": results, "conflicts": conflicts, "pre_epilogue_commit": pre_epilogue_commit})
            print(f"  epilogue {patch.name} FAILED to apply:\n{proc.stderr}")
            print(f"  Resolve in: {worktree}")
            return 2
        git(worktree, "add", "-A")
        if not git(worktree, "diff", "--cached", "--name-only"):
            print(f"  epilogue {patch.name} already applied, skipping")
            record_epilogue(results, patch)
            write_state(worktree, {"next_index": len(entries), "upstream_main": main, "mode": mode, "results": results, "conflicts": conflicts, "pre_epilogue_commit": pre_epilogue_commit})
            continue
        git(
            worktree, "-c", "user.name=Ivan Malison",
            "-c", "user.email=IvanMalison@gmail.com",
            "commit", "-q", "-m", f"assembly: {label}",
        )
        print(f"  epilogue {patch.name} applied")
        record_epilogue(results, patch)
        write_state(worktree, {"next_index": len(entries), "upstream_main": main, "mode": mode, "results": results, "conflicts": conflicts, "pre_epilogue_commit": pre_epilogue_commit})

    # Read the content tree BEFORE the provenance commit. `tree` has to keep
    # meaning "what the topics and upstream produced", or every rebuild would
    # look changed and "no flake bump needed" would never fire again.
    tree = git(worktree, "rev-parse", "HEAD^{tree}")

    # Only the top-level build describes itself. A group branch is merged INTO
    # that build, so a provenance file on it would be a second copy of the same
    # path arriving through a merge -- a guaranteed conflict, and the wrong
    # content besides. Group members reach the page through their group's lock.
    if MANIFEST == DEFAULT_MANIFEST:
        head = commit_provenance(
            worktree, build_provenance(repo, manifest, entries, results, main)
        )
        print(f"recorded {PROVENANCE_FILE}")
    else:
        head = git(worktree, "rev-parse", "HEAD")
    built_tree = git(worktree, "rev-parse", "HEAD^{tree}")

    previous_tree = None
    if LOCK.exists():
        previous_tree = json.loads(LOCK.read_text()).get("tree")

    lock = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "upstream_main": main,
        "integration_branch": manifest["integration_branch"],
        "commit": head,
        "tree": tree,
        # The pushed tree, which is `tree` plus the provenance record. Compare
        # `tree`, not this, when deciding whether anything actually moved.
        "built_tree": built_tree,
        "previous_tree": previous_tree,
        "tree_changed": previous_tree != tree,
        "conflicts": conflicts,
        "manifest_entries": entries,
        "pre_epilogue_commit": pre_epilogue_commit,
        "entries": results,
    }

    print(f"\ntree:   {tree}")
    print(f"commit: {head}")
    print(f"conflicts resolved this run: {conflicts}")
    if previous_tree and previous_tree == tree:
        print("tree UNCHANGED from previous lock -- no flake bump needed")
    elif previous_tree:
        print(f"tree CHANGED (was {previous_tree})")

    if args.write_lock:
        pin_source_checkout(repo, main)
        LOCK.write_text(json.dumps(lock, indent=2) + "\n")
        print(f"wrote {LOCK}")
    else:
        print("(--write-lock not given; lock not written)")

    if args.push:
        branch = manifest["integration_branch"]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        tag = f"{manifest['tag_prefix']}/{stamp}"
        git(repo, "push", "--force", "fork", f"{head}:refs/heads/{branch}", capture=False)
        git(repo, "push", "fork", f"{head}:refs/tags/{tag}", capture=False)
        print(f"pushed fork/{branch} and tag {tag}")
        print("Pin the flake input by REV, never by branch -- the branch is force-pushed.")

    clear_state(worktree)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=SOURCE_REPO,
        help="path to the T3 Code checkout (defaults to the repository submodule)",
    )
    parser.add_argument(
        "--mode",
        choices=["reproduce", "refresh", "extend"],
        default="refresh",
        help=(
            "reproduce: merge at manifest pins. refresh: rebuild from current "
            "upstream and topic heads. extend: merge an appended manifest suffix "
            "onto the locked assembly without refreshing its prefix."
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=(
            "Manifest to build. A group manifest (e.g. thread-picker.toml) "
            "produces a branch that the main manifest then pins as one entry. "
            "Lock, state, and build worktree are all derived from this name."
        ),
    )
    parser.add_argument("--continue", dest="cont", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--write-lock", action="store_true")
    args = parser.parse_args()
    set_manifest(args.manifest)

    try:
        return run(args)
    except Fail as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
