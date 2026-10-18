# T3 Code assembly architecture

This repository is a fork-assembler maintenance stack: upstream T3 Code plus an
ordered set of topic branches and explicit patch entries.

## Sources of truth

1. `manifest.toml` records intent: remotes, base, topic order, and patches.
2. `manifest.lock.json` records fact: exact OIDs, build results, the generated
   commit, and the invariant content-tree hash.
3. `resolutions/rerere/<hash>/{preimage,postimage}` records Git’s normalized
   conflict-hunk resolutions. `INDEX.toml` maps pairs back to manifest entries
   for auditability; replay does not depend on the index.
4. `patches/` records assembly-emergent semantic glue and any required edits
   outside conflict hunks.
5. `t3code/` supplies the pinned upstream checkout and Git object database.

The generated `t3code/assembled` branch is compiled output. Never develop on
it, base a topic on it, or merge it back into a topic branch.

## Base

The assembly is based on `upstream:main`, and carried topics live as
`fork:t3code/main/*` branches based directly on it.

Earlier builds of this assembly wrote pre-release migration IDs into the
user's database. `t3code/main/migration-schema-repair` re-files that ledger
onto `main`'s IDs on first start; keep it while such databases may still be
opened.

Cleanly auto-merged files can carry silent semantic inversions -- `main` once
reversed a scan direction in `packages/shared/src/chatList.ts` that broke
timeline anchoring in both clients -- so run the test suites, not just
typecheck. Coherence fixups are assembly-emergent: they can reference files
that exist only because of some other carried entry, so they need re-deriving
whenever the entry set changes.

Several overlapping client, environment, discovery, and pairing topics are
consolidated into `fork:t3code/main/client-environment-suite`; the
corresponding individual PR branches are deliberately excluded rather than
replayed twice.
Maintain this stack on `main`, and never merge the generated assembly branch
back into a maintenance branch.

## Conflict model

Every build clears the source repository’s rr-cache and seeds it exclusively
from the tracked pairs. Fork Assembler enables rerere only on its merge/continue
commands, so no ambient cache can influence the result.

Git rerere keys on normalized conflict hunks. The same hunks therefore replay
even when unrelated content elsewhere in a file or tree has moved. A changed
or unknown hunk stops for manual resolution; `fork-assembler continue` harvests
the new pair into the repository.

Rerere intentionally captures only conflicted hunks. Edits outside them and
conflict types Git cannot record belong in tracked patches. A locked rebuild
must reproduce the lock’s tree hash exactly, which exposes any missing
out-of-hunk change.

Fork Assembler now offers two homes for such a patch. A **coherence fixup**
(`fixup = "..."` on a branch or pr entry) applies inside that entry’s own
step, right after its merge, so the entry boundary is never an invalid tree;
attach one with `fork-assembler fixup ENTRY FILE --capture`. A standalone **patch
entry** applies at its own position. Prefer a fixup for anything that repairs
what admitting a specific entry broke — which is what every assembly-emergent
patch here does.

## Repository layout

```text
manifest.toml                 fork-assembler intent
manifest.lock.json            pinned inputs and last build result
resolutions/rerere/           tracked preimage/postimage pairs and audit index
patches/                      coherence fixups attached to entries
audit-exceptions.toml         reviewed missing-line sets for `just audit`
bin/audit-assembly-content.py content audit run by `just audit`
t3code/                       pinned upstream submodule and object database
BUILDING.md                   authoritative maintenance and landing procedure
justfile                      common build and verification commands
```

The assembled tree contains the legacy-named `stack-build-info.json` as build
provenance; it is not the name of this repository or workflow. No carried
application code reads it since the move to `main` dropped the
`local/build-provenance` topic; the app's version string carries the build
commit through the Nix packaging instead.

## Agent skill

The Fork Assembler skill is discovered through `.agents/skills/fork-assembler/`;
`.claude/skills/` and `.codex/skills/` point to the same entry. The
checked-in skill is deliberately only a stable discovery stub. It evaluates
`lib.forkFoldAgentGuide`, which this repository's flake re-exports directly
from its pinned `fork-assembler` input.

The full operating instructions therefore change with `flake.lock`. Do not
copy their output into this repository. Repository-specific architecture and
publishing rules remain authoritative here and in `BUILDING.md`.

Read [BUILDING.md](BUILDING.md) before changing or publishing the assembly.
