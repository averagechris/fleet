{
  description = "Release conventions and operations for the averagechris project fleet";

  nixConfig = {
    extra-substituters = ["https://averagechris-dotfiles.cachix.org"];
    extra-trusted-public-keys = ["averagechris-dotfiles.cachix.org-1:VwJkl5dG1+xGDY5x884mH/kVwwpgwBAdBKIF3BZiia4="];
  };

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    # Transitional release backend: release artifacts and the site refresh still use sr.ht.
    srht.url = "git+https://git.sr.ht/~averagechris/srht?ref=refs/tags/v0.9.0";
  };

  outputs = {
    self,
    nixpkgs,
    flake-utils,
    srht,
  }:
    {
      lib = import ./nix/fleet-apps.nix {lib = nixpkgs.lib;};
      templates.rust-cli = {
        path = ./templates/rust-cli;
        description = "Minimal Rust CLI wired to averagechris fleet release conventions";
      };
    }
    // flake-utils.lib.eachDefaultSystem (system: let
      pkgs = nixpkgs.legacyPackages.${system};
      srhtPackage = srht.packages.${system}.srht;
      python = pkgs.python3;
      mkPythonApp = name: file: {
        type = "app";
        program = pkgs.lib.getExe (pkgs.writeShellApplication {
          inherit name;
          runtimeInputs = [python pkgs.gitMinimal pkgs.jujutsu pkgs.nix pkgs.gh] ++ pkgs.lib.optional (name == "new-project") pkgs.cargo;
          text = ''
            export PYTHONPATH=${./scripts}:''${PYTHONPATH:-}
            export FLEET_REGISTRY=${./fleet.toml}
            exec python3 ${file} "$@"
          '';
        });
      };
      fixtureWeb = pkgs.runCommand "web-game-fixture-web" {} ''
        mkdir -p "$out/assets"
        printf '<!doctype html><title>fixture</title>\n' > "$out/index.html"
        printf 'fixture\n' > "$out/assets/game.js"
      '';
      fixtureCi = name:
        pkgs.writeShellApplication {
          inherit name;
          text = "true";
        };
      fixture = self.lib.fleet.presets.webGame {
        inherit pkgs srhtPackage;
        self = ./tests/fixtures/web-game;
        pname = "web-game-fixture";
        webPackage = fixtureWeb;
        ciFmt = fixtureCi "fixture-fmt";
        ciTest = fixtureCi "fixture-test";
        ciCheck = fixtureCi "fixture-check";
        prepareVerify = null;
      };
      formatter = pkgs.writeShellApplication {
        name = "alejandra";
        runtimeInputs = [pkgs.alejandra];
        text = ''if [[ $# -eq 0 ]]; then exec alejandra -q .; fi; exec alejandra -q "$@"'';
      };
    in {
      apps = {
        srht = srht.apps.${system}.srht;
        fleet-status = mkPythonApp "fleet-status" ./scripts/fleet_status.py;
        fleet-tracker-audit = mkPythonApp "fleet-tracker-audit" ./scripts/fleet_tracker_audit.py;
        new-project = mkPythonApp "new-project" ./scripts/new_project.py;
        site-registry = mkPythonApp "site-registry" ./scripts/registry_projection.py;
      };
      packages.srht = srhtPackage;
      checks = {
        web-game-preset = assert builtins.all (name: builtins.hasAttr name fixture.apps) [
          "prepare-release"
          "release-tag"
          "release"
          "ci-fmt"
          "ci-test"
          "ci-check"
          "ci-web"
          "static-checks"
        ];
          pkgs.runCommand "check-web-game-preset" {nativeBuildInputs = with pkgs; [coreutils git gnugrep gnutar gzip jujutsu python3];} ''
            cp -R ${./tests/fixtures/web-game} work; chmod -R u+w work; cd work
            ${fixture.apps.release.program} --help | grep -q -- '--check'
            RELEASE_PROGRAM=${fixture.apps.release.program} python3 ${./tests/release_behavior.py}
            ${fixture.apps.prepare-release.program} --version 1.2.4
            grep -q 'web-game-fixture-v1.2.4-web.tar.gz' builds/release-web.yml
            artifact=${fixture.packages.release-artifact}
            (cd "$artifact" && sha256sum -c web-game-fixture-v1.2.3-web.tar.gz.sha256)
            touch "$out"
          '';
        registry-schema = pkgs.runCommand "check-fleet-registry-schema" {nativeBuildInputs = [python];} ''
          python3 ${./scripts/registry_projection.py} --registry ${./fleet.toml} > projection.toml
          python3 -c 'import tomllib; d=tomllib.load(open("projection.toml","rb")); assert len(d["repos"]) > 0'
          touch "$out"
        '';
      };
      devShells.default = pkgs.mkShell {packages = [python pkgs.alejandra pkgs.gh srhtPackage];};
      inherit formatter;
    });
}
