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

audit:
  bin/audit-assembly-content.py "$(jq -r .build.commit manifest.lock.json)"

smoke:
  nix build "git+file://{{justfile_directory()}}/.worktrees/build?rev=$(jq -r .build.commit manifest.lock.json)#checks.x86_64-linux.smoke"

check: locked audit smoke

fmt:
  nix fmt
