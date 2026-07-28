set shell := ["bash", "-euo", "pipefail", "-c"]

default:
  @just --list

record:
  bin/record-hermetic-inputs.py

reproduce:
  bin/rebuild-t3code-assembly.py --mode reproduce --write-lock

reproduce-group:
  bin/rebuild-t3code-assembly.py --manifest thread-picker.toml --mode reproduce --write-lock

refresh:
  bin/rebuild-t3code-assembly.py --mode refresh --write-lock

refresh-group:
  bin/rebuild-t3code-assembly.py --manifest thread-picker.toml --mode refresh --write-lock

audit:
  bin/audit-assembly-content.py "$(jq -r .commit assembly.lock.json)"

smoke:
  nix build "git+file://{{justfile_directory()}}/.worktrees/assembly-build?rev=$(jq -r .commit assembly.lock.json)#checks.x86_64-linux.smoke"

check: audit smoke

fmt:
  nix fmt
