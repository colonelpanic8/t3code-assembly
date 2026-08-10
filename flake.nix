{
  description = "T3 Code fork-assembler assembly tooling";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    fork-assembler.url = "github:colonelpanic8/fork-assembler";
    fork-assembler.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs =
    {
      nixpkgs,
      fork-assembler,
      ...
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
        "x86_64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = fork-assembler.lib.mkMaintenanceShell {
            inherit pkgs;
            extraPackages = with pkgs; [
              jq
              python3
            ];
          };
        }
      );

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt-tree);

      lib.forkFoldAgentGuide = fork-assembler.lib.agentGuide;
    };
}
