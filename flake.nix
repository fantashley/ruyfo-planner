{
  description = "RUYFO logistics planner";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { self, nixpkgs, ... }:
    let
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-darwin"
        "x86_64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      mkZipcodes =
        pkgs:
        pkgs.python313Packages.buildPythonPackage rec {
          pname = "zipcodes";
          version = "1.3.0";
          pyproject = true;

          src = pkgs.fetchPypi {
            inherit pname version;
            hash = "sha256-ao1mP4R3tUgGsK8BvZC/orqI6Oeb6CSkTjdpfGnQZO8=";
          };

          build-system = [ pkgs.python313Packages.setuptools ];
        };
      mkPythonEnv =
        pkgs:
        let
          python = pkgs.python313;
          zipcodes = mkZipcodes pkgs;
        in
        python.withPackages (
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
      packages = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          pythonEnv = mkPythonEnv pkgs;
        in
        {
          default = pkgs.writeShellApplication {
            name = "ruyfo-planner";
            runtimeInputs = [ pythonEnv ];
            text = ''
              export PYTHONDONTWRITEBYTECODE=1
              export PYTHONPATH=${self}
              exec python -m uvicorn app.main:app "$@"
            '';
          };
        }
      );

      apps = forAllSystems (
        system:
        {
          default = {
            type = "app";
            program = "${self.packages.${system}.default}/bin/ruyfo-planner";
          };
        }
      );

      nixosModules.default = import ./nixos/ruyfo-planner.nix self;

      devShells = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          pythonEnv = mkPythonEnv pkgs;
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
