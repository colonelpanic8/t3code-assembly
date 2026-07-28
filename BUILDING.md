# Building the personal T3 Code stack

This branch (`t3code/stack-tooling`) owns everything needed to reproduce the
personal T3 Code build: the manifests, the rebuild tooling, the epilogue
patches, and this document. **This document is the authoritative reference for
the whole workflow.** The skills under `dotfiles/agents/skills/` only route to
it; if they ever disagree with it, this file wins.

## The model in one page

The installed T3 Code is **upstream `main` plus ~35 carried changes** upstream
has not merged: my open PRs, a few external PRs, and local-only topics. Keeping
that installable works like a small distro:

- Every change lives on its own **topic branch** on the fork
  (`fork` = `colonelpanic8/t3code`; `origin` = `pingdotgg/t3code`).
- `stack/stack.toml` is the **intent**: which topics are carried, in what
  merge order.
- `stack/bin/rebuild-t3code-stack.py` merges the topics, in order, onto
  upstream `main`, producing the integration branch **`t3code/stack`** — the
  **artifact**.
- `stack/stack.lock.json` records what the last rebuild actually produced.
- NixOS pins one commit of `t3code/stack` (the `t3code-integration` flake
  input in `/srv/dotfiles/nixos/flake.nix`) and installs the result via the
  flake's `overlays.client`.

So changing the installed app is always the same loop: change a topic branch
or the manifest → rebuild → verify → push → repin Nix → switch.

`t3code/stack` is a build artifact, regenerated and force-pushed on every
rebuild: **never commit to it, never base work on it, never merge it back.**
Dated tags (`t3code-stack/<timestamp>`) keep every published revision
fetchable for old pins.

Why merges rather than patches: this replaced a Nix `applyPatches` stack of
raw PR diffs plus hand-written compat patches. `patch(1)` matches context
fuzzily with no ancestry, and once silently landed a hunk on the wrong one of
two byte-identical class bodies; a 3-way merge cannot make that mistake. Do
not reintroduce patch stacks.

## Layout

```
stack/
  stack.toml                  main manifest (ordered topics; the intent)
  stack.lock.json             what the last rebuild actually produced
  thread-picker.toml          GROUP sub-manifest + thread-picker.lock.json
  audit-exceptions.toml       reviewed content-audit exceptions (digest-guarded)
  patches/                    epilogue patches
  bin/
    rebuild-t3code-stack.py   the builder (modes: extend / refresh / reproduce)
    audit-stack-content.py    verification step 1
    replay-resolutions.py     conflict helper
    resolve-from-baseline.py  conflict helper (dangerous; see below)
```

**Groups.** A group is a sub-manifest whose output branch is pinned as a
single entry in the main manifest — a subsystem tree, like linux-next. Use one
when several topics all edit the same files (`thread-picker.toml` holds the
CommandPalette cluster): the combination is resolved once against stable
upstream instead of re-derived against a shifting stack on every refresh.
Rebuild the group first, then pin its new head in `stack.toml`.

**Epilogues** (`stack/patches/`) are patches that are functions of the
_assembled_ tree and so cannot live on any topic branch: a migration ID that
depends on which IDs the stack already consumed, a test fixture that must
enumerate every settings field, glue between two topics with no single owner.
Keep them minimal; when glue belongs to one cluster, put it on that cluster's
group branch instead. Note: some historical compat patches carried original
work that exists on no branch — if a build fails on a missing symbol, suspect
that first.

One generated file lives outside `stack/`: `stack-build-info.json` at the repo
root **of the integration branch** — see "Build provenance".

## The three modes

```sh
stack/bin/rebuild-t3code-stack.py --mode <mode> --write-lock --push \
    [--manifest stack/thread-picker.toml]
```

- **`extend`** — the routine path for adding topics. Starts at the locked
  pre-epilogue commit, merges only entries appended to the locked manifest,
  reapplies the epilogues. Strict by design: it refuses changed, reordered, or
  removed existing entries and a published branch that differs from the lock.
  If it refuses, use `refresh`.
- **`refresh`** — full rebuild from current upstream `main` and current topic
  heads. Use it periodically to track upstream, and whenever an existing entry
  moved.
- **`reproduce`** — rebuild at the recorded pins. Deterministic; proves a
  rebuild reproduces a known tree.

Lock, state file, and build worktree all derive from `--manifest`, so a group
build and the main build can be in flight simultaneously. On an unrecognized
conflict the script stops, leaving it in the build worktree; resume with
`--continue`. It is resumable across crashes, and flags `ABSORBED` (already
upstream) and `EMPTY` (merge changed nothing) entries as drop **candidates** —
verify behavior before actually removing one.

## Adding a topic (the routine path)

1. Append a manifest entry (copy an existing one for the shape: `pr`,
   `kind` = fork/external/local, `branch`, 12-char `pin`, `summary`, optional
   `note`). **Placement matters:** put it next to the topics it overlaps so
   conflict resolution stays local. If it edits the CommandPalette trio it
   goes in the thread-picker group instead (then repin the group). If it opens
   a new overlap cluster with two or more topics, consider a new group.
2. Run `--mode extend --write-lock --push`; fall back to `refresh` if extend
   refuses.
3. Resolve conflicts (below), run the syntax gate, run the ladder.
4. Land it (last section).

Do not advance the upstream pin merely to simplify one integration.

## Resolving conflicts

Resolve **semantically**. Never `-X ours` / `-X theirs`, and never a
line-union of both sides — line-unions produce plausible-looking breakage
(orphaned `} from "..."` lines whose `import {` opener was dropped, the same
identifier imported twice from one module, duplicated JSX with orphaned
ternary closes). Every syntax error in this stack's history came from one.

**rerere resolves most conflicts now.** It is enabled repo-locally; its cache
(`.git/rr-cache`, not versioned) has learned every resolution staged in past
builds, so recurring conflicts arrive pre-resolved. `rerere.autoupdate` is
off, so replayed paths stay unmerged: review each one, then `git add`. If a
resolution turns out wrong, run `git rerere forget -- <path>` before
re-resolving — otherwise the mistake replays as the silent default in every
future rebuild.

**The manual primitive**, when rerere has nothing:

```sh
git checkout --ours -- <file> && git add <file>
git diff <upstream-main>...<branch-pin> -- <file> | git apply --3way
```

That lands the assembled state, then replays the branch's own contribution on
top, guaranteeing the branch's content is present. Whatever stays conflicted
is a genuine semantic conflict: read the branch's diff
(`git diff main...<pin> -- <file>`) to see what it is trying to add, and make
sure the result contains it.

Helpers, in order of preference:

1. `replay-resolutions.py --from-build fork/t3code/stack --label '#4257'` —
   replays that entry's resolution verbatim from a previous build (use a
   remote ref; the branch may not exist locally). Exact, but only valid while
   entry order is unchanged up to that entry; past any insertion or reorder
   the merge context differs and the replay is wrong. rerere has no such
   limitation — it keys on conflict content, not position.
2. `resolve-from-baseline.py --baseline <tree>` — copies whole files from a
   reference tree. Sound only when that tree is known-correct for the file
   AND no later entry contributes to it. For a group build, pass
   `--foreign-manifest stack/stack.toml` so files touched by non-group
   entries are refused. **`--force` overrides that check and is lossy** — it
   has silently dropped shipped features and a whole test. Use it only for a
   file whose content comes from exactly one topic. `Sidebar.tsx`,
   `SidebarV2.tsx`, `CommandPalette.tsx`, `ConnectionsSettings.tsx`, `ws.ts`,
   and the composer files all carry multi-topic content: hand-resolve those.

## Syntax gate (before any build)

A nix build takes minutes and Babel reports only the first parse error per
file, so one build cycle buys exactly one fix. esbuild parses TSX in seconds:

```sh
nix shell nixpkgs#esbuild --command bash -c '
for f in $(git diff --name-only origin/main...HEAD | grep -E "\.(ts|tsx)$"); do
  [ -f "$f" ] || continue
  out=$(esbuild "$f" --outfile=/dev/null 2>&1)
  echo "$out" | grep -q "✘" && { echo "### $f"; echo "$out" | grep -A2 "✘"; }
done; echo done'
```

On every hand-resolved file, also check for the two damage patterns that still
parse in isolation but break the build: orphaned import blocks (a
`} from "..."` with no `import {` opener; allow `import type {`) and duplicate
imported identifiers (compare member lists, not just module specifiers). Run
the gate across ALL changed files at once — errors surface one per file and
hide behind each other.

## Verify — the ladder

Two rules drive everything here, each learned the expensive way:

1. **Never treat any reference tree as ground truth.** A previous build was
   once used as the verification oracle; it was itself defective, and copying
   from it shipped a build missing two features. The topic branch is the
   authority for its own content.
2. **Verify per entry, not per tree.** Every substantive line a branch adds
   must be present in the result, or have a specific explanation.

Run all five steps, in order. Do not stop early.

1. **Content audit** — `stack/bin/audit-stack-content.py <rev>`. For every
   manifest entry, checks that the substantive lines its branch adds are
   present in the built tree. Non-zero MISSING is not automatically a bug (a
   later entry may legitimately rewrite those lines) but **every one needs a
   specific explanation before pushing**. Reviewed rewrites live in
   `stack/audit-exceptions.toml`, guarded by exact missing-line count and
   digest so a newly dropped line fails again. A large count on an entry
   resolved with `--force` means its content was dropped. This is the only
   step that proves features survived.
2. **Tree diff vs the previous lock** — every change must be explainable by
   upstream movement plus topic movement; anything else is resolution drift.
   Detects drift; does not prove completeness (that is step 1's job).
3. **Conflict count** in the lock — a rising count means the stack is
   drifting; consider a new group.
4. **Build** — from the dotfiles checkout, with the flake input pinned to the
   candidate rev:

   ```sh
   nix build --impure --expr 'let flake = builtins.getFlake "git+file:///srv/dotfiles?dir=nixos";
     pkgs = import flake.inputs.nixpkgs { system = "x86_64-linux"; config.allowUnfree = true;
       overlays = [ flake.inputs.t3code-integration.overlays.client ]; };
   in pkgs.t3code'
   ```

   **Check the real exit code** — piping nix through `tail` returns tail's
   status and has masked a failing build.

5. **Smoke check** — `nix build .#checks.<system>.smoke` in the fork. Launches
   the packaged app headlessly in client-only mode and fails if the renderer
   throws.

**A green build does not prove the app runs.** A dropped import is a runtime
ReferenceError, not a bundler error — rolldown emits a bundle with a free
variable in it, and one such build's first paint was "Something went wrong:
useEnvironmentSettings is not defined". Cheap static catch without a build:
for every changed file, collect bare `use[A-Z]\w+(` calls and subtract what is
imported or locally declared (exclude dotted calls like `vi.useFakeTimers()`;
handle `import React, { ... }`).

Headless gotcha: the app defaults to the Wayland ozone backend and exits with
"The platform failed to initialize" under Xvfb. Pass
`--ozone-platform=x11 --disable-gpu`, and `--user-data-dir` to dodge the
single-instance lock.

## Build provenance

The last commit on every integration branch writes `stack-build-info.json` to
the repo root: the upstream base with subject and date, then every entry with
kind, resolved OID, summary, note, and status, group members inlined under
their group. `apps/web/vite.config.ts` embeds it and Settings → Build renders
it; that is its only purpose (the lock already records all of it, but the lock
stays here and the app ships from there).

Three rules keep it from breaking things:

- **It is a pure function of the upstream base and resolved entry OIDs.** No
  timestamps, no conflict counts, nothing that varies run to run — otherwise
  an otherwise-identical rebuild produces a changed tree and the
  "tree UNCHANGED from previous lock — no flake bump needed" signal dies.
- **`tree` in the lock is the pre-provenance tree** (the topics' output);
  `built_tree` is what was pushed. Compare `tree` when deciding whether
  anything moved.
- **Only the top-level manifest emits it.** A group branch merges into the
  main build, so a provenance file there would arrive as a second copy of the
  same path — a guaranteed conflict carrying the wrong content.

It cannot record its own commit. The running app gets that separately: the
flake passes `T3CODE_BUILD_COMMIT`, `T3CODE_BUILD_REPO_REMOTE`, and
`T3CODE_BUILD_DATE` into the web build, which also puts the commit link next
to the version in Settings → General.

## Landing a rebuild

1. Push the integration branch and its dated tag (`--push` does both).
2. Downstream: repin `t3code-integration` **by rev, never by branch** in
   `/srv/dotfiles/nixos/flake.nix`, then
   `nix flake lock --update-input t3code-integration`. Skip the repin when the
   rebuild reported the tree unchanged.
3. Build the host package (ladder step 4), then `just switch` from
   `/srv/dotfiles/nixos`. Do not claim success from a source build alone —
   verify the installed `t3` store path.
4. Commit coupled files together so pin and lock never disagree: manifest +
   lock (+ any epilogues) here; flake pin + `flake.lock` in dotfiles. Use
   **explicit paths** — a bare `git commit` after `git add` has swept
   unrelated pre-existing staged changes into a commit before.

## If you cannot finish

The build worktree plus its state file is a complete resumable checkpoint.
Park progress on a named branch so it survives worktree removal, and report
the exact entry and conflicted files. **Never push a stack that has not passed
the content audit.**
