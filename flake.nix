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
      # or-tools 9.15's checkPhase runs a ctest, python_contrib_check_dependencies,
      # whose example script does `from pkg_resources import parse_version`.
      # nixpkgs bumped setuptools 80.10.1 -> 82.0.1, which removed pkg_resources,
      # and that reached nixos-unstable between 2026-07-05 and 2026-07-08. Since
      # then that one obsolete test fails, so or-tools builds nowhere -- including
      # on Hydra, which is why it also isn't in any binary cache. or-tools itself
      # is unchanged (last touched 2026-05-21); only its build env moved.
      #
      # No upstream issue covered this as of 2026-07-12. (nixpkgs#495509 looks
      # related by title but is a different, already-fixed bug: pip fetching
      # setuptools during installPhase, fixed by nixpkgs#483150 in January.)
      #
      # IMPORTANT: apply this ONLY when the build would actually fail. Overriding
      # or-tools changes its derivation hash, so the patched build is in no binary
      # cache and must be compiled from source (~30 min). The *stock* or-tools is
      # in cache.nixos.org wherever it builds, so on any nixpkgs with setuptools
      # < 82 -- or once nixpkgs carries its own fix -- we want the plain, cached
      # derivation and no override at all.
      orToolsFix =
        final: prev:
        let
          ot = prev.or-tools;

          # Has nixpkgs already dealt with it? Either it moved off the affected
          # version, or its own postPatch/checkPhase already handles the test
          # (whether by deleting the script or excluding the ctest).
          upstreamFixed =
            ot.version != "9.15"
            || nixpkgs.lib.hasInfix "check_dependencies" (
              toString (ot.postPatch or "") + toString (ot.checkPhase or "")
            );

          # pkg_resources is gone from setuptools >= 82. Below that, the stock
          # build passes and is cached -- do not touch it.
          setuptoolsDropsPkgResources = nixpkgs.lib.versionAtLeast prev.python313Packages.setuptools.version "82";

          needsFix = !upstreamFixed && setuptoolsDropsPkgResources;
        in
        # NB: the overlay's attribute *names* must not depend on evaluating any
        # package, or nixpkgs' fixpoint hits infinite recursion. So always define
        # `or-tools`, and make only its *value* conditional -- when no fix is
        # needed it is `prev.or-tools` unchanged, i.e. the stock cached derivation.
        {
          or-tools =
            # Trip-wire: once nixpkgs handles this itself, the overlay is dead
            # weight. Say so on eval, then delete orToolsFix + pkgsFor and use a
            # bare `import nixpkgs { inherit system; }` again.
            nixpkgs.lib.warnIf upstreamFixed
              "ruyfo orToolsFix: nixpkgs now handles the or-tools pkg_resources ctest itself (or-tools ${ot.version}); this overlay is obsolete -- delete orToolsFix and pkgsFor"
              (
                if !needsFix then
                  ot
                else
                  ot.overrideAttrs (old: {
                    checkPhase = ''
                      runHook preCheck
                      ctest --output-on-failure -E "python_math_opt_.*|python_contrib_check_dependencies"
                      runHook postCheck
                    '';
                  })
              );
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
