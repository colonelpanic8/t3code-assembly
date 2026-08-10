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

The five existing patch entries predate fixups and still run as trailing
entries. Migrating them would move each into its owning entry’s step — well
defined for the sidebar fixture (#4324), the composer stash merge (#4394, the
later of #4271/#4394), the refreshed-client overlap
(`t3code/unify-environment-selection`), and the runtime imports; genuinely
ambiguous for the migration-ID compat patch, whose collision is between the
base and a local topic rather than between two entries. Because the ~35 later
entries would then merge against already-patched content, treat that as a
deliberate rebuild with possible re-resolution, not a mechanical edit.

## Repository layout

```text
manifest.toml                 fork-assembler intent
manifest.lock.json            pinned inputs and last build result
resolutions/rerere/           tracked preimage/postimage pairs and audit index
patches/                      ordered semantic patch entries
t3code/                       pinned upstream submodule and object database
BUILDING.md                   authoritative maintenance and landing procedure
justfile                      common build and verification commands
```

The `thread-picker.toml` files and legacy Python builder remain temporarily
because fork-assembler does not yet implement nested groups. They maintain only the
thread-picker group branch. `assembly.toml`, `assembly.lock.json`, the old
resolution manifests, and `sources/topics.bundle` are retained as migration
history and must not be used for the primary assembly.

The assembled tree contains the legacy-named `stack-build-info.json` because
the carried application code consumes that filename. It is build provenance,
not the name of this repository or workflow.

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
