set shell := ["env", "__ETC_BASHRC_SOURCED=1", "bash", "-euo", "pipefail", "-c"]

default:
  @just --list

build:
  fork-assembler build

locked:
  fork-assembler build --locked

status:
  fork-assembler status

continue:
  fork-assembler continue

update *entries:
  fork-assembler update {{entries}}

group-reproduce:
  bin/rebuild-t3code-assembly.py --manifest thread-picker.toml --mode reproduce --write-lock

group-refresh:
  bin/rebuild-t3code-assembly.py --manifest thread-picker.toml --mode refresh --write-lock

group-record:
  bin/record-hermetic-inputs.py thread-picker.toml

audit:
  bin/audit-assembly-content.py "$(jq -r .build.commit manifest.lock.json)"

smoke:
  nix build "git+file://{{justfile_directory()}}/.worktrees/build?rev=$(jq -r .build.commit manifest.lock.json)#checks.x86_64-linux.smoke"

check: locked audit smoke

fmt:
  nix fmt
