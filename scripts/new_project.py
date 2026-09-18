#!/usr/bin/env python3
"""Bootstrap a project with averagechris fleet conventions.

This is intentionally a little smarter than `nix flake init`: it can run in an
empty directory or a partially-started Rust CLI project, only writes missing
files by default, can initialize jj, and writes public project metadata for a
future site-enrollment workflow. It does not mutate the site checkout.
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field

from tracker_labels import UMBRELLA_TRACKER_URL, ensure_repo_label, repo_label


DEFAULT_AUTHOR = "Christopher Cummings"
USER_AGENT = "averagechris-fleet-pages (+https://averagechris.srht.site)"
RESERVED_OUTPUT_NAMES = {
    "ci-audit",
    "ci-clippy",
    "ci-deny",
    "ci-fmt",
    "ci-machete",
    "ci-sort",
    "ci-test",
    "default",
    "prepare-release",
    "release",
    "release-artifact",
    "release-tag",
    "static-checks",
}
RESERVED_SITE_PATHS = {
    "404",
    "assets",
    "fleet",
    "keys",
    "notes",
    "now",
    "state",
    "tools",
    "uses",
}
NIX_KEYWORDS = {
    "assert",
    "else",
    "if",
    "in",
    "inherit",
    "let",
    "or",
    "rec",
    "then",
    "with",
}
RUST_KEYWORDS = {
    "Self",
    "abstract",
    "as",
    "async",
    "await",
    "become",
    "box",
    "break",
    "const",
    "continue",
    "crate",
    "do",
    "dyn",
    "else",
    "enum",
    "extern",
    "false",
    "final",
    "fn",
    "for",
    "if",
    "impl",
    "in",
    "let",
    "loop",
    "macro",
    "match",
    "mod",
    "move",
    "mut",
    "override",
    "priv",
    "pub",
    "ref",
    "return",
    "self",
    "static",
    "struct",
    "super",
    "trait",
    "true",
    "try",
    "type",
    "typeof",
    "unsafe",
    "unsized",
    "use",
    "virtual",
    "where",
    "while",
    "yield",
}


@dataclass
class ActionLog:
    dry_run: bool = False
    wrote: list[pathlib.Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    ran: list[str] = field(default_factory=list)

    def say(self, message: str) -> None:
        print(message)

    def note_skip(self, message: str) -> None:
        self.skipped.append(message)
        print(f"skip: {message}")


def html_escape(value: str) -> str:
    return html.escape(value, quote=True)


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def nix_string(value: str) -> str:
    escaped = []
    index = 0
    while index < len(value):
        if value.startswith("${", index):
            escaped.append(r"\${")
            index += 2
            continue
        char = value[index]
        if char == "\\":
            escaped.append(r"\\")
        elif char == '"':
            escaped.append(r"\"")
        elif char == "\n":
            escaped.append(r"\n")
        elif char == "\r":
            escaped.append(r"\r")
        elif char == "\t":
            escaped.append(r"\t")
        else:
            escaped.append(char)
        index += 1
    return '"' + "".join(escaped) + '"'


def validate_project_name(name: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        raise SystemExit(
            "error: project name must start with a lowercase letter and contain only lowercase letters, digits, '-' or '_'"
        )
    return name


def validate_binary_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
        raise SystemExit("error: binary name contains characters Cargo/Nix cannot handle cleanly")
    return name


def validate_output_name(name: str, *, kind: str) -> str:
    if name in RESERVED_OUTPUT_NAMES or name in NIX_KEYWORDS:
        raise SystemExit(f"error: {kind} name {name!r} is reserved by the generated Nix flake")
    return name


def validate_rust_crate_name(name: str, *, kind: str) -> str:
    crate_ident = name.replace("-", "_")
    if crate_ident == "_" or crate_ident in RUST_KEYWORDS:
        raise SystemExit(f"error: {kind} name {name!r} is not usable as a Rust crate/target identifier")
    return name


def validate_site_path(path: str) -> str:
    if path in RESERVED_SITE_PATHS:
        raise SystemExit(f"error: pages subdirectory {path!r} is reserved by the root site")
    return path


def validate_semver(version: str) -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("error: version must be X.Y.Z")
    return version


def shell_join(argv: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


def run(argv: list[str], cwd: pathlib.Path, log: ActionLog, check: bool = True) -> subprocess.CompletedProcess[str]:
    log.ran.append(f"{cwd}: {shell_join(argv)}")
    if log.dry_run:
        print(f"dry-run: {cwd}: {shell_join(argv)}")
        return subprocess.CompletedProcess(argv, 0, "", "")
    print(f"run: {cwd}: {shell_join(argv)}")
    completed = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)
    if check and completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout, file=sys.stdout, end="")
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def write_file(path: pathlib.Path, content: str, log: ActionLog, *, force: bool = False, executable: bool = False) -> bool:
    content = content.strip() + "\n"
    if path.exists():
        existing = path.read_text() if path.is_file() else None
        if existing == content:
            log.note_skip(f"{path} is already up to date")
            return False
        if not force:
            log.note_skip(f"{path} exists; kept existing file (pass --force to replace)")
            return False
    action = "would write" if log.dry_run else "write"
    print(f"{action}: {path}")
    log.wrote.append(path)
    if not log.dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if executable:
            path.chmod(path.stat().st_mode | 0o111)
    return True


def cargo_toml(args: argparse.Namespace) -> str:
    repo = f"https://git.sr.ht/~averagechris/{args.srht_repo}"
    return f"""
    [package]
    name = {toml_string(args.name)}
    version = {toml_string(args.version)}
    edition = {toml_string(args.edition)}
    description = {toml_string(args.description)}
    authors = [{toml_string(args.author)}]
    license = {toml_string(args.license)}
    repository = {toml_string(repo)}
    homepage = {toml_string(repo)}
    readme = "README.md"
    categories = ["command-line-utilities"]

    [[bin]]
    name = {toml_string(args.binary)}
    path = "src/main.rs"

    [dependencies]
    anyhow = "1"
    clap = {{ version = "4", features = ["derive", "env"] }}
    serde = {{ version = "1", features = ["derive"] }}
    serde_json = "1"
    tokio = {{ version = "1", features = ["macros", "rt-multi-thread"] }}

    [profile.release]
    lto = "fat"
    codegen-units = 1
    strip = "symbols"
    opt-level = "s"
    panic = "abort"
    """


def flake_nix(args: argparse.Namespace) -> str:
    return f"""
    {{
      description = {nix_string(args.description)};

      nixConfig = {{
        extra-substituters = ["https://averagechris-dotfiles.cachix.org"];
        extra-trusted-public-keys = ["averagechris-dotfiles.cachix.org-1:VwJkl5dG1+xGDY5x884mH/kVwwpgwBAdBKIF3BZiia4="];
      }};

      inputs = {{
        nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
        fleet.url = "github:averagechris/fleet";
      }};

      outputs = {{
        self,
        nixpkgs,
        fleet,
      }}: let
        systems = [
          "aarch64-darwin"
          "aarch64-linux"
          "x86_64-darwin"
          "x86_64-linux"
        ];

        forAllSystems = nixpkgs.lib.genAttrs systems;
        pkgsFor = system: import nixpkgs {{inherit system;}};
        cargoToml = builtins.fromTOML (builtins.readFile ./Cargo.toml);
        package = cargoToml.package;
        fleetApps = system:
          fleet.lib.fleet.presets.rust {{
            pkgs = pkgsFor system;
            inherit self;
            pname = "{args.artifact_prefix}";
            binaries = ["{args.binary}"];
            subdir = "{args.pages_subdir}";
            srhtRepo = "{args.srht_repo}";
            versionMode = "package";
            versionFile = "Cargo.toml";
            lockPackages = ["{args.name}"];
            srhtPackage = fleet.packages.${{system}}.srht;
          }};
        mkToolApp = system: name: runtimeInputs: text: let
          pkgs = pkgsFor system;
        in
          pkgs.writeShellApplication {{
            inherit name runtimeInputs text;
          }};
        ciAudit = system:
          mkToolApp system "ci-audit" [(pkgsFor system).cargo (pkgsFor system).cargo-audit] ''
            cargo audit --deny warnings
          '';
        ciDeny = system:
          mkToolApp system "ci-deny" [(pkgsFor system).cargo (pkgsFor system).cargo-deny] ''
            cargo deny check
          '';
        ciMachete = system:
          mkToolApp system "ci-machete" [(pkgsFor system).cargo (pkgsFor system).cargo-machete] ''
            cargo machete
          '';
        ciSort = system:
          mkToolApp system "ci-sort" [(pkgsFor system).cargo (pkgsFor system).cargo-sort] ''
            cargo sort --workspace --check
          '';
        nixFormatter = system: let
          pkgs = pkgsFor system;
        in
          pkgs.writeShellApplication {{
            name = "alejandra";
            runtimeInputs = [pkgs.alejandra];
            text = ''
              if [[ $# -eq 0 ]]; then
                exec alejandra -q .
              fi

              exec alejandra -q "$@"
            '';
          }};
      in {{
        packages = forAllSystems (system: let
          pkgs = pkgsFor system;
          lib = pkgs.lib;
          app = pkgs.rustPlatform.buildRustPackage {{
            pname = "{args.binary}";
            version = package.version;
            src = lib.cleanSource ./.;
            cargoLock.lockFile = ./Cargo.lock;

            meta = {{
              description = package.description;
              license = lib.licenses.mit;
              mainProgram = "{args.binary}";
            }};
          }};
        in {{
          default = app;
          {args.binary} = app;
          ci-audit = ciAudit system;
          ci-deny = ciDeny system;
          ci-machete = ciMachete system;
          ci-sort = ciSort system;
          release-artifact = (fleetApps system).releaseArtifact system;
        }});

        apps = forAllSystems (system: {{
          default = self.apps.${{system}}.{args.binary};
          {args.binary} = {{
            type = "app";
            program = "${{self.packages.${{system}}.{args.binary}}}/bin/{args.binary}";
          }};
          ci-audit = {{type = "app"; program = "${{self.packages.${{system}}.ci-audit}}/bin/ci-audit";}};
          ci-deny = {{type = "app"; program = "${{self.packages.${{system}}.ci-deny}}/bin/ci-deny";}};
          ci-machete = {{type = "app"; program = "${{self.packages.${{system}}.ci-machete}}/bin/ci-machete";}};
          ci-sort = {{type = "app"; program = "${{self.packages.${{system}}.ci-sort}}/bin/ci-sort";}};
          inherit ((fleetApps system).apps) prepare-release release-tag release ci-fmt ci-clippy static-checks ci-test;
        }});

        checks = forAllSystems (system: {{
          inherit (self.packages.${{system}}) {args.binary} release-artifact;
        }});

        devShells = forAllSystems (system: let
          pkgs = pkgsFor system;
        in {{
          default = pkgs.mkShell {{
            packages = with pkgs; [
              alejandra
              cargo
              cargo-audit
              cargo-deny
              cargo-machete
              cargo-outdated
              cargo-sort
              clippy
              direnv
              jujutsu
              nixd
              rust-analyzer
              rustc
              rustfmt
              sccache
            ] ++ [fleet.packages.${{system}}.srht];
          }};
        }});

        formatter = forAllSystems nixFormatter;
      }};
    }}
    """


def release_manifest(args: argparse.Namespace) -> str:
    artifact = f"{args.artifact_prefix}-v{args.version}-x86_64-linux.tar.gz"
    return f"""
    image: nixos/unstable
    arch: x86_64
    oauth: git.sr.ht/OBJECTS:RW git.sr.ht/REPOSITORIES:RO git.sr.ht/PROFILE:RO builds.sr.ht/JOBS:RW builds.sr.ht/SECRETS:RO builds.sr.ht/PROFILE:RO meta.sr.ht/PROFILE:RO pages.sr.ht/PAGES:RW
    secrets:
      - 5ec60320-e0d8-4ebe-8b45-1d10cc4d2cb2 # cachix averagechris-dotfiles -> ~/.ci_secrets/cachix_token
    environment:
      GIT_CONFIG_COUNT: "1"
      GIT_CONFIG_KEY_0: http.userAgent
      GIT_CONFIG_VALUE_0: "{USER_AGENT}"
      NIX_CONFIG: |
        experimental-features = nix-command flakes
        extra-substituters = https://averagechris-dotfiles.cachix.org
        extra-trusted-public-keys = averagechris-dotfiles.cachix.org-1:VwJkl5dG1+xGDY5x884mH/kVwwpgwBAdBKIF3BZiia4=
    sources:
      - https://git.sr.ht/~averagechris/{args.srht_repo}
    tasks:
      - release-artifact: |
          set -eu
          cd {args.srht_repo}
          nix build .#release-artifact --out-link result-release-artifact
          mkdir -p ~/artifacts
          cp -p result-release-artifact/* ~/artifacts/
      - cache-warm: |
          set -eu
          cd {args.srht_repo}
          if [ ! -f ~/.ci_secrets/cachix_token ]; then echo "no cachix token available; skipping cache warm"; exit 0; fi
          [ -e result-release-artifact ] || {{ echo "release-artifact result missing; skipping cache warm"; exit 0; }}
          nix build .#default --out-link result-pkg --print-build-logs
          [ -e result-pkg ] || {{ echo "package result missing; skipping cache warm"; exit 0; }}
          set +x
          CACHIX_AUTH_TOKEN="$(cat ~/.ci_secrets/cachix_token)"; export CACHIX_AUTH_TOKEN
          set -x
          nix run --inputs-from . nixpkgs#cachix -- push averagechris-dotfiles result-release-artifact result-pkg
      - upload-and-refresh: |
          set -eu
          cd {args.srht_repo}
          srht() {{ nix run --inputs-from . fleet#srht -- "$@"; }}
          set +x
          export SRHT_TOKEN="${{SRHT_TOKEN:-${{OAUTH2_TOKEN:-$(awk '/access-token/ {{ gsub(/"/, "", $2); print $2; exit }}' ~/.config/hut/config 2>/dev/null || true)}}}}"
          set -x
          version="$(awk '/^\\[package\\]/{{s=1}} s && /^version = /{{gsub(/"/,"",$3); print $3; exit}}' Cargo.toml)"
          tag="v${{version#v}}"
          commit="$(git rev-parse HEAD)"

          set -- result-release-artifact/*.tar.gz
          [ -e "$1" ] || {{ printf '%s\\n' 'no release artifacts found' >&2; exit 1; }}
          for artifact do
            srht git artifact upload -r {args.srht_repo} --rev "$tag" "$artifact"
            srht git artifact upload -r {args.srht_repo} --rev "$tag" "$artifact.sha256"
          done

          manifest="$(mktemp)"
          cat > "$manifest" <<EOF
          image: nixos/unstable
          arch: x86_64
          oauth: pages.sr.ht/PAGES:RW
          environment:
            GIT_CONFIG_COUNT: "1"
            GIT_CONFIG_KEY_0: http.userAgent
            GIT_CONFIG_VALUE_0: "{USER_AGENT}"
            NIX_CONFIG: |
              experimental-features = nix-command flakes
              extra-substituters = https://averagechris-dotfiles.cachix.org
              extra-trusted-public-keys = averagechris-dotfiles.cachix.org-1:VwJkl5dG1+xGDY5x884mH/kVwwpgwBAdBKIF3BZiia4=
            TRIGGER_SOURCE: release
            TRIGGER_PROJECT: {args.pages_subdir}
            TRIGGER_TAG: $tag
            TRIGGER_SHA: $commit
          sources:
            - https://git.sr.ht/~averagechris/averagechris.srht.site
          tasks:
            - refresh: |
                cd averagechris.srht.site
                nix run .#refresh-pages
          EOF
          srht builds submit "$manifest" --secrets --note "site refresh: {args.artifact_prefix} $tag"
    artifacts:
      - artifacts/{artifact}
      - artifacts/{artifact}.sha256
    """


def ci_manifest(args: argparse.Namespace) -> str:
    return f"""
    image: nixos/unstable
    arch: x86_64
    # Keep this manifest lean: no image packages are needed for the generated checks.
    # If packages are later required on nixos/unstable, use channel-prefixed names
    # (for example, nixos.git); unprefixed names fail before tasks start.
    secrets:
      - 5ec60320-e0d8-4ebe-8b45-1d10cc4d2cb2 # cachix averagechris-dotfiles -> ~/.ci_secrets/cachix_token
    environment:
      NIX_CONFIG: |
        experimental-features = nix-command flakes
        extra-substituters = https://averagechris-dotfiles.cachix.org
        extra-trusted-public-keys = averagechris-dotfiles.cachix.org-1:VwJkl5dG1+xGDY5x884mH/kVwwpgwBAdBKIF3BZiia4=
    sources:
      - https://git.sr.ht/~averagechris/{args.srht_repo}
    tasks:
      - fmt: |
          cd {args.srht_repo}
          nix run .#ci-fmt
      - clippy: |
          cd {args.srht_repo}
          nix run .#ci-clippy
      - test: |
          cd {args.srht_repo}
          nix run .#ci-test
      - package: |
          cd {args.srht_repo}
          nix build .#{args.artifact_prefix} --print-build-logs
      - closure-size: |
          cd {args.srht_repo}
          nix path-info -S ./result
          nix path-info -rS ./result | sort -k2 -n | tail -n 5
      - cache-warm: |
          cd {args.srht_repo}
          if [ ! -f ~/.ci_secrets/cachix_token ]; then echo "no cachix token available; skipping cache warm"; exit 0; fi
          [ -e ./result ] || {{ echo "package result missing; skipping cache warm"; exit 0; }}
          set +x
          CACHIX_AUTH_TOKEN="$(cat ~/.ci_secrets/cachix_token)"; export CACHIX_AUTH_TOKEN
          set -x
          nix run --inputs-from . nixpkgs#cachix -- push averagechris-dotfiles ./result
    """


def readme(args: argparse.Namespace) -> str:
    label = repo_label(args.name)
    return f"""
    # {args.name}

    {args.description}

    ## Development

    ```sh
    direnv allow   # or: nix develop
    nix run . -- --help
    nix run .#ci-fmt
    nix run .#ci-clippy
    nix run .#static-checks
    nix run .#ci-test
    ```

    ## Release

    ```sh
    nix run .#release -- --version X.Y.Z --submit-linux-build
    ```

    The shared release interface comes from
    `github:averagechris/fleet#lib.fleet.presets.rust`.
    Its locked fleet input supplies the approved srht CLI; release manifests use
    `nix run --inputs-from . fleet#srht`, never a floating srht URL.

    ## Issues

    Track project work in the umbrella tracker at <{UMBRELLA_TRACKER_URL}> with
    the `{label}` label. The `new-project` bootstrap ensures that label exists
    idempotently when GitHub CLI credentials are available.
    """


def agents_md(args: argparse.Namespace) -> str:
    label = repo_label(args.name)
    return f"""
    # {args.name} project guidance

    Use `jj` for version-control actions in this repository.

    ## Development

    - Enter the toolchain with `direnv allow` or `nix develop`.
    - Nix formatting uses wrapped `alejandra -q`; run `nix fmt` or `nix fmt -- --check .`.
    - Prefer local checks: `nix run .#static-checks` (fmt + clippy), `nix run .#ci-test`, `nix run .#ci-machete`, `nix run .#ci-sort`, `nix run .#ci-deny`, `nix run .#ci-audit`.
    - `.builds/ci.yml` runs fmt, clippy, test, the package build, closure-size reporting, and a guarded Cachix cache warm on every push.

    ## sccache

    The host sets `RUSTC_WRAPPER=sccache` globally, and it must never be unset to "fix"
    build failures. If builds fail with sccache connection or compiler errors, run
    `sccache --stop-server` and retry; the supervised launchd agent restarts a healthy
    server.

    ## Release workflow

    This repo uses the standard averagechris fleet interface:

    ```sh
    nix run .#prepare-release -- --version X.Y.Z
    nix run .#release-tag
    nix build .#release-artifact
    nix run .#static-checks
    nix run .#release -- --version X.Y.Z --submit-linux-build
    ```

    `.builds/ci.yml` runs automatically on every push and warms the
    averagechris-dotfiles Cachix cache when the CI secret is available.
    `builds/release-linux-x86_64.yml` is explicit-submit only; do not move it to `.builds/`.
    The flake passes `fleet.packages.${{system}}.srht` to release tooling, and
    static manifests run `nix run --inputs-from . fleet#srht`. Keep this approved
    fleet-locked channel; never replace it with a floating srht URL.

    ## Issue tracking

    Project work belongs in the shared GitHub tracker:
    {UMBRELLA_TRACKER_URL}

    Use the `{label}` label for this repository. The label follows the fleet
    convention `repo:<fleet-name>` and is intentionally separate from artifact,
    binary, or SourceHut repository aliases.
    """


def main_rs(args: argparse.Namespace) -> str:
    cli_ident = args.binary.replace("-", "_")
    return f"""
    use clap::Parser;

    #[derive(Debug, serde::Serialize)]
    struct JsonOutput {{
        ok: bool,
        tool: &'static str,
    }}

    #[derive(Debug, Parser)]
    #[command(name = "{args.binary}", version, about)]
    struct Cli {{
        /// Print output as JSON.
        #[arg(long)]
        json: bool,
    }}

    #[tokio::main]
    async fn main() -> anyhow::Result<()> {{
        let cli = Cli::parse();
        if cli.json {{
            let output = JsonOutput {{
                ok: true,
                tool: "{cli_ident}",
            }};
            println!("{{}}", serde_json::to_string(&output)?);
        }} else {{
            println!("{args.binary}: hello from {args.name}");
        }}
        Ok(())
    }}
    """


def gitignore() -> str:
    return """
    /target
    /result
    /result-*
    /dist
    /.direnv
    """


def jj_lint() -> str:
    return """
    lints = [
      { name = "nix fmt", command = "nix fmt -- --check ." },
      { name = "ci fmt", command = "nix run .#ci-fmt" },
      { name = "ci clippy", command = "nix run .#ci-clippy" },
      { name = "ci test", command = "nix run .#ci-test" },
      { name = "cargo machete", command = "nix run .#ci-machete" },
      { name = "cargo sort", command = "nix run .#ci-sort" },
      { name = "cargo deny", command = "nix run .#ci-deny" },
      { name = "cargo audit", command = "nix run .#ci-audit" },
    ]
    """


def changelog() -> str:
    return """
    # Changelog

    ## Unreleased

    ### Added

    - Initial project scaffold.
    """


def deny_toml() -> str:
    return """
    [licenses]
    allow = [
      "Apache-2.0",
      "MIT",
      "Unicode-3.0",
      "Unlicense",
    ]
    confidence-threshold = 0.8
    """


def enrollment_manifest(args: argparse.Namespace) -> str:
    label = repo_label(args.name)
    return f"""
    # Public project metadata for future averagechris fleet enrollment.
    # This file is informational today; site registry updates are manual.
    [project]
    name = {toml_string(args.name)}
    description = {toml_string(args.description)}
    pages_subdir = {toml_string(args.pages_subdir)}
    srht_repo = {toml_string(args.srht_repo)}
    artifact_prefix = {toml_string(args.artifact_prefix)}
    binaries = [{toml_string(args.binary)}]
    version_file = "Cargo.toml"
    homepage_tier = {toml_string(args.tier)}
    downloads = true
    tracker = {toml_string(UMBRELLA_TRACKER_URL)}
    tracker_label = {toml_string(label)}

    [pages]
    docs = ["overview.html", "examples.html", "demo.html", "changelog.html"]
    """


def docs_page(args: argparse.Namespace, page: str) -> str:
    title = page.removesuffix(".html")
    project = html_escape(args.name)
    description = html_escape(args.description)
    binary = html_escape(args.binary)
    if page == "overview.html":
        label = html_escape(repo_label(args.name))
        body = f"""
        <h1>{project}</h1>
        <p class="lede">{description}</p>
        <ul>
          <li>Install released binaries from <a href="index.html">downloads</a>.</li>
          <li>Read the release notes in <a href="changelog.html">changelog</a>.</li>
          <li>See command shapes in <a href="examples.html">examples</a>.</li>
          <li>Track issues at <a href="{UMBRELLA_TRACKER_URL}">averagechris/fleet</a> with <code>{label}</code>.</li>
        </ul>
        """
    elif page == "examples.html":
        body = f"""
        <h1>{project} examples</h1>
        <pre><code>{binary} --help
{binary} --json</code></pre>
        <p>Replace this page with real workflows as the project grows.</p>
        """
    elif page == "demo.html":
        body = f"""
        <h1>{project} demo</h1>
        <p>Add a short terminal transcript, screenshot, or animated walkthrough here.</p>
        <pre><code>$ {binary} --json
{{"ok":true,"tool":"{html_escape(args.binary.replace('-', '_'))}"}}</code></pre>
        """
    elif page == "changelog.html":
        body = f"""
        <h1>{project} changelog</h1>
        <p>The downloads page renders tagged release notes from <code>CHANGELOG.md</code>.</p>
        <p><a href="index.html">Open downloads and release notes</a>.</p>
        """
    else:
        body = f"<h1>{project} {html_escape(title)}</h1>"
    return f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{project} {html_escape(title)}</title>
      <style>
        body {{ font-family: Charter, Georgia, serif; line-height: 1.65; max-width: 760px; margin: 0 auto; padding: 3rem 1.25rem; }}
        code, pre {{ font-family: ui-monospace, Menlo, monospace; }}
        pre {{ overflow-x: auto; padding: 1rem; border: 1px solid #dfdad9; border-radius: 8px; }}
        .nav {{ color: #797593; font-style: italic; }}
        .lede {{ font-size: 1.2rem; }}
      </style>
    </head>
    <body>
      <p class="nav"><a href="https://averagechris.srht.site/">~averagechris</a> / <a href="index.html">{project}</a> / {html_escape(title)}</p>
      {body}
    </body>
    </html>
    """


def scaffold(args: argparse.Namespace, log: ActionLog) -> None:
    target = args.dir.resolve()
    if not target.exists():
        if log.dry_run:
            print(f"dry-run: create directory {target}")
        else:
            target.mkdir(parents=True)

    files = {
        "Cargo.toml": cargo_toml(args),
        "flake.nix": flake_nix(args),
        "README.md": readme(args),
        "CHANGELOG.md": changelog(),
        "deny.toml": deny_toml(),
        "AGENTS.md": agents_md(args),
        ".builds/ci.yml": ci_manifest(args),
        ".gitignore": gitignore(),
        ".envrc": "use flake",
        ".jj-lint.toml": jj_lint(),
        ".averagechris-project.toml": enrollment_manifest(args),
        "src/main.rs": main_rs(args),
        "docs/pages/overview.html": docs_page(args, "overview.html"),
        "docs/pages/examples.html": docs_page(args, "examples.html"),
        "docs/pages/demo.html": docs_page(args, "demo.html"),
        "docs/pages/changelog.html": docs_page(args, "changelog.html"),
        "builds/release-linux_x86_64.yml": release_manifest(args),
    }

    # Preserve the repo-wide standard spelling used in fleet.toml/AGENTS.md.
    files["builds/release-linux-x86_64.yml"] = files.pop("builds/release-linux_x86_64.yml")

    for relative, content in files.items():
        write_file(target / relative, textwrap.dedent(content), log, force=args.force)


def refuse_site_checkout(args: argparse.Namespace) -> None:
    target = args.dir.resolve()
    start = target if target.exists() else target.parent
    for candidate in (start, *start.parents):
        site_markers = [
            candidate / "fleet.toml",
            candidate / "site-data" / "projects.toml",
            candidate / "scripts" / "new_project.py",
        ]
        if all(marker.exists() for marker in site_markers):
            raise SystemExit(
                "error: refusing to scaffold inside the averagechris.srht.site checkout; "
                "run this from the new project directory via the remote flake URL, "
                "or pass --dir /path/to/new-project outside this repo"
            )


def init_jj(args: argparse.Namespace, log: ActionLog) -> None:
    if args.no_repo_init:
        log.note_skip("repository initialization disabled by --no-repo-init")
        return
    target = args.dir.resolve()
    if not command_exists("jj"):
        log.note_skip("jj not found on PATH; repository not initialized")
        return
    if log.dry_run:
        if target.exists():
            existing = subprocess.run(["jj", "root"], cwd=target, text=True, capture_output=True, check=False)
            if existing.returncode == 0:
                log.note_skip(f"already inside jj repo at {existing.stdout.strip()}")
                return
        print(f"dry-run: would initialize colocated jj/git repo in {target}")
        return
    existing = run(["jj", "root"], target, log, check=False)
    if existing.returncode == 0:
        log.note_skip(f"already inside jj repo at {existing.stdout.strip()}")
        return
    if (target / ".git").exists():
        run(["jj", "git", "init", "--git-repo", ".git", "."], target, log)
    else:
        run(["jj", "git", "init", "--colocate", "."], target, log)


def generate_locks(args: argparse.Namespace, log: ActionLog) -> None:
    target = args.dir.resolve()
    if not args.no_cargo_lock and (target / "Cargo.toml").exists() and not (target / "Cargo.lock").exists():
        if command_exists("cargo"):
            run(["cargo", "generate-lockfile"], target, log)
        else:
            log.note_skip("cargo not found on PATH; Cargo.lock not generated")
    if not args.no_flake_lock and (target / "flake.nix").exists() and not (target / "flake.lock").exists():
        if command_exists("nix"):
            run(["nix", "--accept-flake-config", "flake", "lock"], target, log)
        else:
            log.note_skip("nix not found on PATH; flake.lock not generated")


def ensure_tracker_label(args: argparse.Namespace, log: ActionLog) -> None:
    if args.no_tracker_label:
        log.note_skip("tracker label creation disabled by --no-tracker-label")
        return
    if not command_exists(args.gh_bin):
        log.note_skip(f"{args.gh_bin} not found on PATH; tracker label {repo_label(args.name)} not ensured")
        return
    try:
        created, label = ensure_repo_label(args.name, gh_bin=args.gh_bin, dry_run=log.dry_run)
    except Exception as exc:  # noqa: BLE001 - preserve scaffold success if GitHub is unavailable.
        log.note_skip(f"could not ensure {UMBRELLA_TRACKER_URL} label {repo_label(args.name)}: {exc}")
        return
    if log.dry_run:
        print(f"dry-run: would ensure {UMBRELLA_TRACKER_URL} has label {label}")
    elif created:
        print(f"created tracker label: {label}")
    else:
        log.note_skip(f"tracker label {label} already exists")


def describe_new_change(args: argparse.Namespace, log: ActionLog) -> None:
    if args.no_describe or args.no_repo_init or not command_exists("jj"):
        return
    if log.dry_run:
        print(f"dry-run: would describe jj change as 'chore: bootstrap {args.name}' if @ is undescribed")
        return
    status = run(["jj", "status", "--no-pager", "--color=never"], args.dir.resolve(), log, check=False)
    if status.returncode != 0:
        return
    desc = run(
        ["jj", "log", "-r", "@", "--no-graph", "--color=never", "-T", "description.first_line()"],
        args.dir.resolve(),
        log,
        check=False,
    )
    if desc.returncode == 0 and not desc.stdout.strip():
        run(["jj", "describe", "-m", f"chore: bootstrap {args.name}"], args.dir.resolve(), log, check=False)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap a Rust CLI project with averagechris fleet conventions."
    )
    parser.add_argument("name", nargs="?", help="Cargo package / fleet project name (default: current directory name)")
    parser.add_argument("--name", dest="name_override", help="override inferred project name")
    parser.add_argument("--dir", type=pathlib.Path, default=pathlib.Path.cwd(), help="target directory (default: cwd)")
    parser.add_argument("--description", default=None, help="project description")
    parser.add_argument("--binary", default=None, help="binary name (default: project name)")
    parser.add_argument("--srht-repo", default=None, help="sourcehut repository name (default: project name)")
    parser.add_argument("--artifact-prefix", default=None, help="release artifact prefix (default: project name)")
    parser.add_argument("--pages-subdir", default=None, help="site subdirectory (default: project name)")
    parser.add_argument("--version", default="0.1.0", help="initial semver (default: 0.1.0)")
    parser.add_argument("--edition", default="2024", choices=["2021", "2024"], help="Rust edition")
    parser.add_argument("--author", default=DEFAULT_AUTHOR)
    parser.add_argument("--license", default="MIT", choices=["MIT"])
    parser.add_argument("--force", action="store_true", help="replace existing scaffolded files")
    parser.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    parser.add_argument("--no-repo-init", action="store_true", help="do not initialize jj/git")
    parser.add_argument("--no-cargo-lock", action="store_true", help="do not run cargo generate-lockfile")
    parser.add_argument("--no-flake-lock", action="store_true", help="do not run nix flake lock")
    parser.add_argument("--no-describe", action="store_true", help="do not describe the jj working-copy change")
    parser.add_argument("--no-tracker-label", action="store_true", help="do not ensure the shared GitHub repo:<name> label")
    parser.add_argument("--gh-bin", default="gh", help="gh executable to use for tracker label checks (default: gh)")
    parser.add_argument("--tier", choices=["featured", "more"], default="more", help="homepage tier recorded in .averagechris-project.toml (default: more)")
    parser.add_argument("--featured", action="store_true", help="shortcut for --tier featured")
    args = parser.parse_args(argv)

    args.dir = args.dir.expanduser()
    selected_name = args.name_override or args.name or args.dir.resolve().name
    args.name = validate_rust_crate_name(
        validate_output_name(validate_project_name(selected_name), kind="project"),
        kind="project",
    )
    args.binary = validate_rust_crate_name(
        validate_output_name(validate_binary_name(args.binary or args.name), kind="binary"),
        kind="binary",
    )
    args.srht_repo = validate_project_name(args.srht_repo or args.name)
    args.artifact_prefix = validate_project_name(args.artifact_prefix or args.name)
    args.pages_subdir = validate_site_path(validate_project_name(args.pages_subdir or args.name))
    args.version = validate_semver(args.version)
    args.description = args.description or f"A Rust CLI for {args.name}."
    if args.featured:
        args.tier = "featured"
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    log = ActionLog(dry_run=args.dry_run)
    refuse_site_checkout(args)
    scaffold(args, log)
    init_jj(args, log)
    describe_new_change(args, log)
    generate_locks(args, log)
    ensure_tracker_label(args, log)

    print("\nDone. Next steps:")
    print(f"  cd {shlex.quote(str(args.dir.resolve()))}")
    print("  nix run .#ci-fmt && nix run .#ci-clippy && nix run .#ci-test")
    print("  nix run .#ci-machete && nix run .#ci-sort && nix run .#ci-deny && nix run .#ci-audit")
    print(f"  # create/push the sourcehut repo when ready: https://git.sr.ht/~averagechris/{args.srht_repo}")
    print(f"  # issues: {UMBRELLA_TRACKER_URL} with label {repo_label(args.name)}")
    print("  # site enrollment is deferred; metadata is in .averagechris-project.toml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
