# t3code-assembly

Reproducible assembly of Ivan's T3 Code build.

This repository pins upstream T3 Code as the `t3code/` submodule and keeps the
ordered topic manifests, locks, epilogue patches, conflict helpers, and build
procedure alongside it. The assembled T3 Code branch is a generated artifact;
this repository is the source of truth.

Clone it with the submodule:

```sh
git clone --recurse-submodules https://github.com/colonelpanic8/t3code-assembly
cd t3code-assembly
bin/rebuild-t3code-assembly.py --mode reproduce
```

Read [BUILDING.md](BUILDING.md) before changing or publishing an assembly.
