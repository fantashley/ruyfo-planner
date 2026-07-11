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
      # nixpkgs #495509: or-tools' checkPhase runs a ctest,
      # python_contrib_check_dependencies, whose example script does
      # `from pkg_resources import parse_version`. pkg_resources was removed in
      # setuptools 81, so recent nixpkgs fails that one obsolete test and never
      # caches or-tools. Extend the existing ctest exclusion to skip it too.
      orToolsFix =
        final: prev:
        let
          ot = prev.or-tools;
          # Trip-wire: warn on eval once this workaround is probably obsolete, so
          # we notice and drop it. Fires if or-tools moves off the version we
          # validated (9.15), or if nixpkgs' own checkPhase already excludes the
          # test (i.e. the upstream fix landed). When it fires: verify a plain
          # `import nixpkgs { inherit system; }` build of or-tools passes, then
          # delete orToolsFix + pkgsFor and use a bare import again.
          likelyObsolete =
            ot.version != "9.15"
            || nixpkgs.lib.hasInfix "python_contrib_check_dependencies" (toString (ot.checkPhase or ""));
        in
        {
          or-tools =
            nixpkgs.lib.warnIf likelyObsolete
              "ruyfo orToolsFix: or-tools ${ot.version} may no longer need the pkg_resources ctest workaround (nixpkgs#495509); verify a plain build and remove the overlay"
              (ot.overrideAttrs (old: {
                checkPhase = ''
                  runHook preCheck
                  ctest --output-on-failure -E "python_math_opt_.*|python_contrib_check_dependencies"
                  runHook postCheck
                '';
              }));
        };
      pkgsFor = system: import nixpkgs {
        inherit system;
        overlays = [ orToolsFix ];
      };
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
          pkgs = pkgsFor system;
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
          pkgs = pkgsFor system;
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
