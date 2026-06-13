{
  description = "Development environment for the RUYFO logistics planner";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { nixpkgs, ... }:
    let
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-darwin"
        "x86_64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      devShells = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          python = pkgs.python313;
          zipcodes = pkgs.python313Packages.buildPythonPackage rec {
            pname = "zipcodes";
            version = "1.3.0";
            pyproject = true;

            src = pkgs.fetchPypi {
              inherit pname version;
              hash = "sha256-ao1mP4R3tUgGsK8BvZC/orqI6Oeb6CSkTjdpfGnQZO8=";
            };

            build-system = [ pkgs.python313Packages.setuptools ];
          };
          pythonEnv = python.withPackages (
            ps: with ps; [
              fastapi
              jinja2
              ortools
              pytest
              python-multipart
              sqlmodel
              uvicorn
              zipcodes
            ]
          );
        in
        {
          default = pkgs.mkShell {
            packages = [
              pythonEnv
              pkgs.gh
              pkgs.jujutsu
              pkgs.sqlite
            ];

            shellHook = ''
              echo "RUYFO planner dev shell"
              echo "  python -m pytest"
              echo "  python -m scripts.fixture plan example"
              echo "  python -m uvicorn app.main:app --reload"
            '';
          };
        }
      );
    };
}
