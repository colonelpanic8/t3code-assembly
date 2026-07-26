# Building the personal T3 Code stack

This branch (`t3code/stack-tooling`) owns everything needed to reproduce the
personal integration build: the manifests, the rebuild tooling, the epilogue
patches, and these instructions. It is a normal branch with normal history.

Downstream (NixOS dotfiles) holds exactly two things: a flake input pinned to an
integration rev, and a small overlay that adds the Electron safeStorage wrapper.
Everything else lives here.

## The model

The build is an **integration branch** — `t3code/stack` — assembled by merging
an ordered manifest of topic branches. It is a build artifact:

- never commit to it
- never base work on it
- never merge it back

`refresh` and `reproduce` regenerate it from scratch and force-push it. `extend`
adds a manifest suffix to the locked build. Every published build also gets a
dated tag (`t3code-stack/<timestamp>`) so older revs stay fetchable for old
pins.

This replaced a Nix `applyPatches` stack of raw PR diffs plus hand-written
compatibility patches. Do not reintroduce that: `patch(1)` does fuzzy context
matching with no ancestry, and it once silently landed a hunk on the wrong
symbol (two byte-identical class bodies). A 3-way merge cannot do that.

## Layout

```
stack/
  stack.toml              main manifest (ordered topics)
  stack.lock.json         what the last rebuild actually produced
  thread-picker.toml      GROUP sub-manifest, pinned as one entry in stack.toml
  thread-picker.lock.json
  patches/                epilogues -- see below
  bin/
    rebuild-t3code-stack.py
    resolve-from-baseline.py
    replay-resolutions.py
    audit-stack-content.py
```

## Refreshing, reproducing, and extending

Groups first, then the main stack. A group is a sub-manifest whose output branch
is pinned as a single entry in the main manifest — a subsystem tree.

```sh
stack/bin/rebuild-t3code-stack.py \
    --manifest stack/thread-picker.toml --mode reproduce --write-lock --push
# pin the new group head in stack.toml, then:
stack/bin/rebuild-t3code-stack.py --mode refresh --write-lock --push
```

- `--mode reproduce` merges at the pins recorded in the manifest (deterministic).
- `--mode refresh` starts at current upstream `main` and follows every current
  topic-branch head. Use it to rebase the entire assembled stack onto upstream
  and pick up movement anywhere in the manifest.
- `--mode extend` preserves the locked upstream and topic prefix, then merges
  only newly appended entries:

  ```sh
  stack/bin/rebuild-t3code-stack.py --mode extend --write-lock --push
  ```

  Extend requires the prior manifest snapshot to be an exact prefix of the
  current manifest. It refuses changed, reordered, or removed existing entries,
  a published branch that differs from the lock, and locks created before
  extend metadata existed. Because epilogues must remain last, it resumes at
  the recorded pre-epilogue commit, merges the suffix, and reapplies all
  epilogues. It is a fast path for adding PRs, not a substitute for periodic
  refreshes.

Lock, state file, and build worktree all derive from `--manifest`, so a group
build and the main build can be in flight simultaneously.

The rebuild stops on an unrecognized conflict and leaves it in the build
worktree. Resume with `--continue`. It is resumable across crashes, and flags
`ABSORBED` (already upstream) and `EMPTY` (merge changed nothing) entries as
drop candidates.

## Resolving conflicts

**The correct primitive**, and the one to reach for first:

```sh
git checkout --ours -- <file> && git add <file>
git diff <upstream-main>...<branch-pin> -- <file> | git apply --3way
```

That lands the assembled state, then replays the branch's own contribution on
top, guaranteeing the branch's content is present. What it leaves behind are
genuine semantic conflicts — read them.

**Never resolve by taking a line-union of the two sides.** It produces
syntactically broken output that a reviewer will not notice: dropped `import {`
openers leaving orphaned member lines, duplicated import identifiers, and
duplicated JSX blocks with orphaned ternary closes. Every syntax error in this
stack's history came from that.

**Never use `-X ours` / `-X theirs`.**

Helpers, in order of preference:

1. `replay-resolutions.py --from-build fork/t3code/stack --label '#4257'` —
   replays that entry's resolution verbatim from a previous build. Exact, but
   only valid while entry order is unchanged up to that point. Use a remote ref;
   the branch may not exist locally.
2. `resolve-from-baseline.py --baseline <tree>` — copies files from a reference
   tree. Sound only when that tree is known-correct for the file AND no later
   entry contributes to it. When building a group, pass
   `--foreign-manifest stack/stack.toml` so files touched by non-group
   entries are refused.

`--force` overrides that check. **It is lossy.** It has silently dropped shipped
features (sidebar host names, sidebar accent colors) and a whole test. Only use
it for a file whose content comes from exactly one topic. `Sidebar.tsx`,
`SidebarV2.tsx`, `CommandPalette.tsx`, `ConnectionsSettings.tsx`, `ws.ts` and the
composer files all have multi-topic content — hand-resolve those.

## Syntax gate (run before any build)

A nix build takes minutes, and Babel reports only the **first** parse error per
file -- so one build cycle buys exactly one fix. esbuild parses TSX in seconds
with an exact location:

```sh
nix shell nixpkgs#esbuild --command bash -c '
for f in $(git diff --name-only origin/main...HEAD | grep -E "\.(ts|tsx)$"); do
  [ -f "$f" ] || continue
  out=$(esbuild "$f" --outfile=/dev/null 2>&1)
  echo "$out" | grep -q "✘" && { echo "### $f"; echo "$out" | grep -A2 "✘"; }
done; echo done'
```

Two more checks worth running on every file you hand-resolved, because they
catch the damage a line-union leaves that still _looks_ plausible:

- **orphaned import blocks** -- a `} from "..."` whose `import {` opener was
  dropped. (Allow `import type {`.)
- **duplicate imported identifiers** -- parse the member lists, not just the
  module specifiers; the same symbol imported twice from the same module is a
  hard error and a `from "..."` comparison will not see it.

Run the gate across ALL changed files at once, not just the ones you touched
last -- errors surface one per file and hide behind each other.

## Verification ladder

Run all five, in this order. Do not stop early.

1. **Content audit** — `stack/bin/audit-stack-content.py <rev>`. For every
   manifest entry, checks that the substantive lines its branch adds are present
   in the built tree. Non-zero MISSING is not automatically a bug (a later entry
   may legitimately rewrite those lines) but **every one needs an explanation
   before pushing**. Reviewed later rewrites live in
   `stack/audit-exceptions.toml`; each is guarded by the exact missing-line
   count and digest, so a newly dropped line fails the audit. This is the only
   step that proves features survived.
2. **Tree diff vs the previous lock** — changes must be explainable by upstream
   movement plus topic movement. Detects drift; does not prove completeness.
3. **Conflict count** in the lock -- rising counts mean the stack is drifting;
   consider a new group.
4. **Build** -- see below.
5. **Smoke check** -- `nix build .#checks.<system>.smoke`. Launches the packaged
   app headlessly in client-only mode and fails if the renderer throws.

**The build does not prove the app runs.** A dropped import is a runtime
ReferenceError, not a bundler error: rolldown happily emits a bundle containing
a free variable. That shipped a build whose first paint was "Something went
wrong: useEnvironmentSettings is not defined". Two such imports were dropped by
taking only `ours` on an import conflict whose `theirs` side carried the new
module.

Cheap way to catch it without a build: for every changed file, collect bare
`use[A-Z]\w+(` calls and subtract what is imported or locally declared. Exclude
dotted calls (`vi.useFakeTimers()`), and handle `import React, { ... }`.

Headless gotcha: the app defaults to the Wayland ozone backend and exits with
"The platform failed to initialize" under Xvfb. Pass `--ozone-platform=x11
--disable-gpu`, and `--user-data-dir` to dodge the single-instance lock.

Do not verify by diffing against a previous _build_ and calling a match good.
That was done once; the reference tree was itself defective, and copying from it
propagated its omissions into a build that passed and shipped missing features.

## Building

From the NixOS dotfiles checkout, with the flake input pinned to the rev:

```sh
nix build --impure --expr 'let flake = builtins.getFlake "git+file:///srv/dotfiles?dir=nixos";
  pkgs = import flake.inputs.nixpkgs { system = "x86_64-linux"; config.allowUnfree = true;
    overlays = [ (import /srv/dotfiles/nix-shared/t3code.nix { inherit (flake) inputs; }) ]; };
in pkgs.t3code'
```

**Check the real exit code.** Piping nix through `tail` returns tail's status and
has masked a failing build.

A green build proves it compiles. It does not prove features survived — that is
the audit's job. Note also that Babel stops at the first parse error per file, so
syntax errors surface one at a time; fix them in a batch by scanning for the
known damage patterns rather than one build cycle each.

The build itself is defined by `flake.nix` at the repo root, carried as the
`t3code/local/nix-flake` topic. Anything build-related belongs there, not
downstream — when both defined the build, they drifted.

## Epilogues

`patches/` holds patches that are functions of the _assembled_ tree and so
cannot live on any topic branch:

- a migration ID that depends on which IDs the stack already consumed
- a test fixture that must enumerate every settings field
- glue between topics that has no single owner

Keep them minimal. When glue belongs to a specific cluster, prefer adding it to
that cluster's group branch instead.

Some historical compatibility patches carried **original local work that exists
in no branch**. Merging alone can never recover that. If a build fails on a
missing export or symbol, suspect this first.

## Landing a rebuild

1. Push the branch and a dated tag.
2. Downstream: repin the flake input **by rev, never by branch**, then
   `nix flake lock --update-input t3code-integration`.
3. Build, then activate.
4. Commit the manifest and lock together here, so the pin and lock never
   disagree.
