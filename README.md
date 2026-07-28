# t3code-assembly

Ivan’s reproducible [fork-fold](https://github.com/colonelpanic8/fork-fold)
stack for T3 Code.

The repository pins upstream T3 Code as `t3code/`, records the ordered topics
in `manifest.toml`, pins their exact OIDs and resulting tree in
`manifest.lock.json`, and tracks conflict-hunk resolutions under
`resolutions/rerere/`. The assembled T3 Code branch is generated output; this
repository is the source of truth.

```sh
git clone --recurse-submodules https://github.com/colonelpanic8/t3code-assembly
cd t3code-assembly
direnv allow
just locked
```

`just locked` is network-free once every pinned object is present in the
submodule object database. Run `just build` first when a fresh checkout needs
to fetch pinned topic objects.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for the model and
[BUILDING.md](BUILDING.md) for the authoritative operating procedure.
