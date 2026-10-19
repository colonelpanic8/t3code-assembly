# Building and landing the T3 Code assembly

This is the authoritative procedure for the personal T3 Code assembly,
driven by `manifest.toml` and `manifest.lock.json`.

## Safety invariants

- Work on this repository’s `main` branch in the primary checkout.
- Preserve unrelated changes in this repository, `t3code/`, and
  `/srv/dotfiles`.
- Never develop on, base work on, or merge back the generated
  `t3code/assembled` branch.
- `fork-assembler build` consumes existing pins. Only `fork-assembler update` moves
  them.
- Builds may use only the tracked pairs under `resolutions/rerere/`; never
  enable persistent rerere or seed a build from ambient state.
- Release eagerly. This assembly serves only its owner, who prefers a fast
  release over a fully validated one and tolerates the occasional bad build.
  Once `fork-assembler build` completes, land it, then run the verification
  ladder and test suites and fix anything they find in a follow-up rebuild.

## Order of work

1. Update and build; resolve any conflicts.
2. Run the syntax gate.
3. Land the build (below).
4. Run the five-step verification ladder and the test suites.
5. If anything fails, fix it where it lives and land a fresh rebuild.

## Inspecting and changing the stack

Start with:

```sh
git status --short
git submodule status
just status
```

Append a topic with `fork-assembler add REMOTE:BRANCH` or `fork-assembler add --pr N`.
Append semantic glue with `fork-assembler add --patch FILE`. Appends build
incrementally; reordering or removal invalidates the suffix from the first
changed entry.

To refresh existing pins:

```sh
fork-assembler update                 # base and all live entries
fork-assembler update ENTRY...        # selected entries
fork-assembler build
```

Before a mutation phase, fetch the live upstream head and record it in the
session notes:

```sh
git -C t3code fetch origin main
git -C t3code rev-parse origin/main
```

If it moves before publishing, reassess the affected updates and rebuild.

## Resolving conflicts

Recognized conflict hunks resolve automatically from the tracked rerere pairs.
For an unknown conflict, fork-assembler exits with status 2 and names the build
worktree and files:

1. Resolve files in `.worktrees/build`.
2. Stage only the intended resolution with `git add`.
3. Run `fork-assembler continue`.
4. Repeat until the build completes.
5. Run `fork-assembler build --locked` from scratch.

`continue` harvests Git’s preimage/postimage pair under
`resolutions/rerere/` and updates the informational `INDEX.toml`. If the
correct result also requires edits outside the conflict hunks, add those as a
small named patch entry; rerere cannot record them.

If you cannot finish, preserve the stopped worktree by creating a named branch
at its current `HEAD`, then report the manifest entry and unresolved paths.
Do not discard the worktree or pretend the build is complete.

## Syntax gate

Before landing:

```sh
nix develop -c fork-assembler status
nix fmt -- --ci
python3 -m py_compile bin/audit-assembly-content.py
git diff --check
```

## Five-step verification ladder

Run these in order after landing; stop at the first failure and fix forward:

1. `just locked` — clean rebuild from the pins and tracked pairs; the tree
   must equal `manifest.lock.json` exactly.
2. Verify pair integrity: every directory immediately below
   `resolutions/rerere/` contains at least one `preimage*` and matching
   `postimage*`, with no `thisimage*` tracked.
3. `just audit` — prove the assembled content still carries the intended
   topic changes.
4. `just smoke` — build the assembled flake’s focused smoke check.
5. Compare `git -C .worktrees/build rev-parse HEAD^{tree}` with
   `.build.built_tree` and the pre-provenance tree with `.build.tree` in the
   lock.

## Test suites

Compare the assembled tree with its exact upstream base, not against a clean
run: on this machine several suites fail on upstream too (missing
`pkg-config`, no headless Chromium, the Electron binary not downloaded, timing
flakes). Run every package's `test` script in a worktree of each tree with
`T3CODE_*` variables unset, and supply Electron with
`ELECTRON_OVERRIDE_DIST_PATH=$(nix build --no-link --print-out-paths nixpkgs#electron)/libexec/electron`.
Only failures absent from the upstream run belong to the assembly; fix them
in the topic branch that owns the code.

## Landing a rebuild

As soon as the build and syntax gate pass:

1. Read `.build.commit` from `manifest.lock.json`.
2. Push that exact commit to `fork/t3code/assembled`.
3. Create and push a unique UTC tag
   `t3code-assembled/YYYYMMDDTHHMMSSZ` at the same commit.
4. Pin that exact revision as `t3code-integration` in
   `/srv/dotfiles/nixos/flake.nix`; never pin the generated branch.
5. From `/srv/dotfiles/nixos`, update the input and build the overlaid package:

   ```sh
   nix flake update t3code-integration
   nix build .#nixosConfigurations.ryzen-shine.pkgs.t3code
   ```

6. Commit the assembly’s manifest, lock, tracked pairs, patches, docs, and
   submodule pin together using explicit paths.
7. Commit the dotfiles flake pin and lock with explicit paths, preserving
   unrelated local changes and staged files.
8. Activate only with `just switch` from `/srv/dotfiles/nixos`. It restarts
   `t3code-headless.service`, which can end the session running it, so commit
   first.

The generated commits retain real timestamps, so commit IDs can differ between
rebuilds. Tree hashes are the reproducibility invariant.
