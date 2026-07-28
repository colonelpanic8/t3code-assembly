# T3 Code assembly architecture

This repository completely describes Ivan's T3 Code build. A fresh clone with
its submodule can reproduce the same source tree without GitHub access or any
machine-local conflict cache.

## Model

The build is upstream T3 Code plus an ordered set of pinned topics:

1. `t3code/` supplies the pinned upstream base and its history.
2. `sources/topics.bundle` supplies every pinned PR and local-topic Git object.
3. `assembly.toml` specifies the base, topic OIDs, and merge order.
4. `resolutions/assembly.toml` and `resolutions/assembly/*.patch` specify the
   exact result of each conflicted merge.
5. `patches/` supplies small changes that only make sense after all topics have
   been combined.
6. `bin/rebuild-t3code-assembly.py` creates the generated
   `t3code/assembled` branch and `assembly.lock.json`.

The source-tree hash is the reproducibility invariant. Generated commits retain
their real timestamps, so their commit IDs may differ across rebuilds while
their trees remain identical.

`t3code/assembled` is disposable output. Never develop on it or merge it back
into a topic branch.

## Repository layout

```text
assembly.toml                 main manifest
assembly.lock.json            last build result
thread-picker.toml            nested manifest for the picker overlap group
thread-picker.lock.json       last group build result
sources/topics.bundle         vendored topic Git objects
resolutions/                  explicit conflict-resolution records
patches/                      post-merge epilogues
bin/                          rebuild, record, and audit tools
t3code/                       pinned upstream submodule
flake.nix                     development toolchain
justfile                      common commands
```

`AGENTS.md` points to this file. `CLAUDE.md` points to `AGENTS.md`.

## Reproduce

```sh
just reproduce
just check
```

Reproduce performs no network fetch. It imports the committed topic bundle,
checks out the manifest's base, and merges each pinned topic in order.

When Git reports a conflict, the rebuild looks up that entry's checked-in
resolution record. A record contains:

- the expected first-parent tree;
- the exact topic OID;
- the expected resolved tree; and
- a binary-safe full diff from the first-parent tree to the resolved merge.

The rebuild applies a record only when both inputs match exactly, then verifies
the resulting tree. If no matching record exists, it stops in the build
worktree for a new resolution. Git `rerere` is explicitly disabled.

`just check` runs the manifest-content audit and the assembled flake's smoke
check.

## Refresh

Refreshing is the only networked operation:

```sh
just refresh
# resolve any new conflict, then:
bin/rebuild-t3code-assembly.py --continue --write-lock
just record
just reproduce
just check
```

`just refresh` rebuilds from current upstream and topic branch heads.
`just record` then:

- copies the resolved base and topic OIDs from the lock into the manifest;
- rewrites the explicit resolution records from the completed merge commits;
- rewrites `sources/topics.bundle`.

The final `just reproduce` proves that the newly recorded repository state is
sufficient by itself.

For the thread-picker group, use `just refresh-group` and
`just reproduce-group`. Rebuild the group first, update its pin in the main
manifest through a main refresh, then record both manifests together.

## Resolution mechanics

Each conflicted merge is itself the resolution record's source. The recorder
finds its `assembly: merge <entry>` commit and stores the full diff between the
merge's first parent and resolved tree. This includes clean paths as well as
conflicted paths, so replay does not depend on conflict-marker placement or
fuzzy patch context.

If replay stops:

1. Resolve the files in `.worktrees/assembly-build`.
2. Stage them with `git add`.
3. Continue with `bin/rebuild-t3code-assembly.py --continue --write-lock`.
4. Run `just record`, then reproduce again.

Do not add a broad compatibility patch when the change is a resolution for one
topic. Resolution records belong under `resolutions/`; `patches/` is only for
behavior that emerges after multiple topics are present.

## Groups and epilogues

A group is a nested manifest whose generated branch is one topic in the main
manifest. Use one when several topics repeatedly conflict in the same
subsystem. `thread-picker.toml` is the current example.

An epilogue is a post-merge patch for behavior with no single topic owner:
cross-topic glue, a migration-number collision, or a fixture that must enumerate
the final combined schema. Keep epilogues small.

The assembled tree contains a legacy-named `stack-build-info.json` because the
carried application code still consumes that filename. It is build provenance,
not the name of this repository or workflow.

## Publish and install

After `just check`:

1. Push the lock's commit to `t3code/assembled` and to a dated
   `t3code-assembled/<timestamp>` tag.
2. Pin that exact commit as `t3code-integration` in
   `/srv/dotfiles/nixos/flake.nix`.
3. Update the flake lock and build the overlaid `pkgs.t3code`.
4. Run `just switch` from `/srv/dotfiles/nixos`.
5. Commit the manifest, lock, bundle, resolutions, patches, and submodule pin
   together.

Use explicit paths when committing in either repository; unrelated local
changes may already exist.
