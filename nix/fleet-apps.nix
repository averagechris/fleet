{lib}: let
  q = lib.escapeShellArg;
  app = drv: {
    type = "app";
    program = lib.getExe drv;
  };

  core = rec {
    # Core release building blocks. Presets wire language-specific version and
    # validation behavior while keeping the fleet command names stable.
    #
    # Linux release manifests (builds/release-linux-x86_64.yml) run on the
    # nixos/unstable image has NO system python3 or srht on PATH. Static
    # manifests use `nix run --inputs-from . fleet#srht --`; release apps should
    # receive the approved `fleet.packages.${system}.srht` as srhtPackage.
    # Required oauth grants for the job (repo lookup needs git PROFILE:RO +
    # REPOSITORIES:RO; artifact upload needs OBJECTS:RW; submitting the
    # site-refresh manifest needs JOBS:RW + SECRETS:RO + builds PROFILE:RO,
    # and the refresh publish needs pages PAGES:RW):
    #   git.sr.ht/OBJECTS:RW git.sr.ht/REPOSITORIES:RO git.sr.ht/PROFILE:RO
    #   builds.sr.ht/JOBS:RW builds.sr.ht/SECRETS:RO builds.sr.ht/PROFILE:RO
    #   meta.sr.ht/PROFILE:RO pages.sr.ht/PAGES:RW
    # srht reads SRHT_TOKEN directly. sr.ht CI oauth grants export OAUTH2_TOKEN
    # and pre-provision ~/.config/hut/config (HCL: `access-token "..."`), so
    # generated manifests export SRHT_TOKEN from those before invoking srht.
    # IMPORTANT: sr.ht only provisions a manifest's `oauth:` bearer token when
    # the job is submitted with secrets ENABLED. srht defaults to secrets
    # disabled (agent safety), so every submit of an oauth-grant manifest here
    # must pass --secrets or the job's token (and hut config) never appears.
    mkPrepareRelease = {
      pkgs,
      pname,
      changelog ? "CHANGELOG.md",
      manifestPath ? "builds/release-linux-x86_64.yml",
      artifactSuffix ? "x86_64-linux",
      requireManifestReplacement ? false,
      setVersion,
      verify ? null,
      runtimeInputs ? [],
      ...
    }:
      pkgs.writeShellApplication {
        name = "prepare-release";
        runtimeInputs = (with pkgs; [python3]) ++ runtimeInputs;
        text = ''
          set -euo pipefail
          usage() { printf 'usage: prepare-release [--version X.Y.Z] [--allow-downgrade]\n' >&2; }
          version=""; allow_downgrade=0
          while [ "$#" -gt 0 ]; do
            case "$1" in
              --version) version="''${2:-}"; shift 2 ;;
              --allow-downgrade) allow_downgrade=1; shift ;;
              -h|--help) usage; exit 0 ;;
              *) usage; exit 2 ;;
            esac
          done
          export ALLOW_DOWNGRADE="$allow_downgrade"
          ${setVersion}
          VERSION="$version" PNAME=${q pname} CHANGELOG=${q changelog} MANIFEST_PATH=${q manifestPath} ARTIFACT_SUFFIX=${q artifactSuffix} STRICT_MANIFEST=${
            if requireManifestReplacement
            then "1"
            else "0"
          } python3 - <<'PY'
          from pathlib import Path
          import datetime, os, re
          version = os.environ["VERSION"]
          if not isinstance(version, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+", version):
              raise SystemExit(f"invalid semver version: {version}")
          version = version.removeprefix("v")
          manifest = Path(os.environ["MANIFEST_PATH"])
          if manifest.exists():
              p = re.escape(os.environ["PNAME"])
              suffix = re.escape(os.environ["ARTIFACT_SUFFIX"])
              updated, count = re.subn(rf"{p}-v\d+\.\d+\.\d+-{suffix}\.tar\.gz", f"{os.environ['PNAME']}-v{version}-{os.environ['ARTIFACT_SUFFIX']}.tar.gz", manifest.read_text())
              if os.environ["STRICT_MANIFEST"] == "1" and count < 1: raise SystemExit(f"expected a release artifact in {manifest}, found none")
              manifest.write_text(updated)
          changelog = Path(os.environ["CHANGELOG"])
          content = changelog.read_text() if changelog.exists() else "# Changelog\n\n## Unreleased\n"
          if not content.startswith("# Changelog"): content = "# Changelog\n\n" + content
          if not re.search(r"(?m)^## Unreleased\s*$", content): content = content.rstrip() + "\n\n## Unreleased\n"
          tag = f"v{version}"
          if not re.search(rf"(?m)^## {re.escape(tag)}(?:\s+-\s+.*)?$", content):
              m = re.search(r"(?m)^## Unreleased\s*$", content); start = m.end()
              nxt = re.search(r"(?m)^## ", content[start:]); end = start + nxt.start() if nxt else len(content)
              body = content[start:end].strip() or "### Changed\n\n- Maintenance release."
              content = content[:start] + f"\n\n## {tag} - {datetime.date.today().isoformat()}\n\n{body}\n" + content[end:].lstrip("\n")
          changelog.write_text(content.rstrip() + "\n")
          PY
          ${lib.optionalString (verify != null) verify}
        '';
      };

    mkReleaseTag = {
      pkgs,
      pname,
      versionFile ? "Cargo.toml",
      versionExpr ? null,
      versionCommand ? null,
    }: let
      readVersion =
        if versionCommand != null
        then versionCommand
        else ''VERSION_FILE=${q versionFile} python3 -c 'import os,tomllib; data=tomllib.load(open(os.environ["VERSION_FILE"],"rb")); v=${versionExpr}; assert isinstance(v,str) and v; print(v)' '';
    in
      pkgs.writeShellApplication {
        name = "release-tag";
        runtimeInputs = with pkgs; [git jujutsu python3];
        text = ''
          set -euo pipefail
          revision="@"; while [[ $# -gt 0 ]]; do case "$1" in --revision) revision="$2"; shift 2;; -h|--help) printf 'usage: release-tag [--revision REV]\n'; exit 0;; *) printf 'unknown argument: %s\n' "$1" >&2; exit 1;; esac; done
          repo_root="$(git rev-parse --show-toplevel 2>/dev/null || jj root)"; cd "$repo_root"
          version="$(${readVersion})"
          [[ "$version" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]] || { printf 'version must be semver\n' >&2; exit 1; }
          tag="v''${version#v}"
          # An empty jj working copy is never the release target; tag its
          # parent instead (same semantics as `jj ship`).
          if [[ -d .jj && "$revision" == "@" && "$(jj log -r @ --no-graph --color=never -T 'if(empty, "1", "0")')" == "1" ]]; then revision="@-"; fi
          if [[ -d .jj ]]; then commit="$(jj log -r "$revision" --no-graph --color=never -T 'commit_id')"; else commit="$(git rev-parse "$revision")"; fi

          remote_tag_ref="$(git ls-remote --tags origin "refs/tags/$tag" 2>/dev/null || true)"
          if [[ -n "$remote_tag_ref" ]]; then
            remote_tag_sha="''${remote_tag_ref%%$'\t'*}"
            remote_peeled_ref="$(git ls-remote origin "refs/tags/$tag^{}" 2>/dev/null || true)"
            if [[ -n "$remote_peeled_ref" ]]; then
              remote_commit="''${remote_peeled_ref%%$'\t'*}"
            else
              remote_commit="$remote_tag_sha"
            fi
            if [[ "$remote_commit" == "$commit" ]]; then
              printf 'tag %s already exists at %s, skipping tag creation\n' "$tag" "$commit" >&2
              exit 0
            fi
            printf 'remote tag %s exists at %s, but release target is %s\n' "$tag" "$remote_commit" "$commit" >&2
            exit 1
          fi

          git -c tag.gpgSign=false tag -fa "$tag" -m "${pname} $tag" "$commit"
          git push origin "refs/tags/$tag"
        '';
      };

    mkRefreshTriggerManifest = {
      pname,
      subdir ? pname,
    }: ''
      tmp="$release_tmpdir/refresh.yml"
      cat > "$tmp" <<EOF
      image: nixos/unstable
      arch: x86_64
      oauth: pages.sr.ht/PAGES:RW
      environment:
        GIT_CONFIG_COUNT: "1"
        GIT_CONFIG_KEY_0: http.userAgent
        GIT_CONFIG_VALUE_0: "averagechris-fleet-pages (+https://averagechris.srht.site)"
        NIX_CONFIG: |
          experimental-features = nix-command flakes
          extra-substituters = https://averagechris-dotfiles.cachix.org
          extra-trusted-public-keys = averagechris-dotfiles.cachix.org-1:VwJkl5dG1+xGDY5x884mH/kVwwpgwBAdBKIF3BZiia4=
        TRIGGER_SOURCE: release
        TRIGGER_PROJECT: ${subdir}
        TRIGGER_TAG: $tag
        TRIGGER_SHA: $commit
      sources:
        - https://git.sr.ht/~averagechris/averagechris.srht.site
      tasks:
        - refresh: |
            cd averagechris.srht.site
            nix run .#refresh-pages
      EOF
      submit_build_once "${pname}/$tag/refresh" "$tmp" "site refresh: ${pname} $tag"
    '';

    mkRelease = {
      pkgs,
      pname,
      srhtRepo ? pname,
      versionFile ? "Cargo.toml",
      versionExpr ? null,
      versionCommand ? null,
      refreshTrigger,
      prepareRelease,
      releaseTag,
      ciApps ? [],
      artifactPackage,
      linuxManifest ? "builds/release-linux-x86_64.yml",
      allowLinuxBuild ? true,
      # Supplying this puts the caller's pinned derivation on PATH. null keeps
      # old consumers evaluable, but their release app fails with migration
      # guidance instead of silently fetching an unapproved floating CLI.
      srhtPackage ? null,
      runtimeInputs ? [],
    }: let
      readVersion =
        if versionCommand != null
        then versionCommand
        else ''VERSION_FILE=${q versionFile} python3 -c 'import os,tomllib; data=tomllib.load(open(os.environ["VERSION_FILE"],"rb")); print(${versionExpr})' '';
      validateScript =
        if ciApps == []
        then "true"
        else lib.concatMapStringsSep "\n" (name: "nix run .#${name}") ciApps;
    in
      pkgs.writeShellApplication {
        name = "release";
        runtimeInputs = (with pkgs; [coreutils git jujutsu nix python3]) ++ lib.optional (srhtPackage != null) srhtPackage ++ runtimeInputs;
        text = ''
          set -euo pipefail
          export TERM=dumb
          if [[ -n "''${FLEET_RELEASE_NIX:-}" ]]; then nix() { "$FLEET_RELEASE_NIX" "$@"; }; fi
          if [[ -n "''${FLEET_RELEASE_SRHT:-}" ]]; then srht() { "$FLEET_RELEASE_SRHT" "$@"; }; fi
          ${lib.optionalString (srhtPackage == null) ''
            if [[ -z "''${FLEET_RELEASE_SRHT:-}" ]]; then
              srht() {
                printf '%s\n' 'fleet release requires srhtPackage; pass fleet.packages.<system>.srht to the preset' >&2
                return 2
              }
            fi
          ''}
          usage() {
            cat <<'EOF'
          usage: release --version X.Y.Z [--check] [--allow-downgrade] [--submit-linux-build]

          --check               verify release readiness without editing files or publishing refs
          --version X.Y.Z       required release version
          --allow-downgrade     permit a lower version; the target tag must still be new
          --submit-linux-build  submit the Linux release build after publication
          EOF
          }
          version=""; check_only=0; submit_linux_build=0; allow_downgrade=0; linux_manifest=${q linuxManifest}
          while [[ $# -gt 0 ]]; do case "$1" in --version) version="''${2:-}"; shift 2;; --check) check_only=1; shift;; --allow-downgrade) allow_downgrade=1; shift;; --submit-linux-build) submit_linux_build=1; shift;; -h|--help) usage; exit 0;; *) printf 'unknown argument: %s\n' "$1" >&2; usage >&2; exit 2;; esac; done
          [[ -n "$version" ]] || { printf '%s\n' '--version X.Y.Z is required' >&2; exit 2; }
          ${lib.optionalString (!allowLinuxBuild) ''
            if [[ $submit_linux_build -eq 1 ]]; then printf '%s\n' '--submit-linux-build is not valid for a platform-neutral web-game release' >&2; exit 2; fi
          ''}
          [[ "$version" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]] || { printf 'invalid semver version: %s\n' "$version" >&2; exit 2; }
          version="''${version#v}"; tag="v$version"
          repo_root="$(jj root 2>/dev/null)" || { printf '%s\n' 'release requires a jj repository' >&2; exit 1; }
          cd "$repo_root"
          git_dir="$(jj git root 2>/dev/null)" || { printf '%s\n' 'release requires a jj Git-backed repository' >&2; exit 1; }
          [[ -d "$git_dir" ]] || { printf 'jj Git backing directory does not exist: %s\n' "$git_dir" >&2; exit 1; }
          git --git-dir="$git_dir" remote get-url origin >/dev/null || { printf '%s\n' 'origin remote is not configured' >&2; exit 1; }
          [[ "$(jj log -r @ --no-graph --color=never -T 'if(empty, "1", "0")')" == 1 ]] || { printf '%s\n' 'working-copy commit @ is not empty; finish it before release' >&2; exit 1; }
          base="$(jj log -r '@-' --no-graph --color=never -T commit_id 2>/dev/null)" || { printf '%s\n' 'cannot resolve @-' >&2; exit 1; }
          local_main="$(jj log -r main --no-graph --color=never -T commit_id 2>/dev/null)" || { printf '%s\n' 'cannot resolve local main' >&2; exit 1; }
          remote_main_line="$(git --git-dir="$git_dir" ls-remote --heads origin refs/heads/main)"
          remote_main="''${remote_main_line%%$'\t'*}"
          [[ -n "$remote_main" ]] || { printf '%s\n' 'origin main does not resolve' >&2; exit 1; }
          [[ "$base" == "$local_main" ]] || { printf 'stale/diverged checkout: @-=%s local main=%s\n' "$base" "$local_main" >&2; exit 1; }
          current="$(${readVersion})"
          ALLOW_DOWNGRADE="$allow_downgrade" CURRENT="$current" REQUESTED="$version" python3 - <<'PY'
          import os, re
          def semver(name):
              value = os.environ[name].removeprefix("v")
              if not re.fullmatch(r"\d+\.\d+\.\d+", value): raise SystemExit(f"invalid {name.lower()} semver: {value}")
              return tuple(map(int, value.split(".")))
          if semver("REQUESTED") < semver("CURRENT") and os.environ["ALLOW_DOWNGRADE"] != "1":
              raise SystemExit("refusing version downgrade (pass --allow-downgrade to override)")
          PY
          remote_tags="$(git --git-dir="$git_dir" ls-remote --tags origin)"
          remote_tag_object="$(printf '%s\n' "$remote_tags" | python3 -c 'import sys; ref=f"refs/tags/{sys.argv[1]}"; print(next((line.split()[0] for line in sys.stdin if line.split()[1:]==[ref]),""))' "$tag")"
          remote_tag_commit="$(printf '%s\n' "$remote_tags" | python3 -c 'import sys; ref=f"refs/tags/{sys.argv[1]}^{{}}"; print(next((line.split()[0] for line in sys.stdin if line.split()[1:]==[ref]),""))' "$tag")"
          resume=0
          if [[ -n "$remote_tag_object" ]]; then
            if [[ -n "$remote_tag_commit" && "$remote_tag_commit" == "$remote_main" && "$remote_main" == "$local_main" && "$local_main" == "$base" && "''${current#v}" == "$version" ]]; then
              resume=1
            else
              printf 'existing tag %s is not an exact resumable release (peeled=%s remote-main=%s local-main=%s base=%s current-version=%s)\n' "$tag" "''${remote_tag_commit:-unpeeled}" "$remote_main" "$local_main" "$base" "$current" >&2
              exit 1
            fi
          elif [[ "$base" != "$remote_main" ]]; then
            printf 'stale/diverged checkout: @-=%s origin main=%s\n' "$base" "$remote_main" >&2; exit 1
          fi
          if [[ $resume -eq 0 ]] && git --git-dir="$git_dir" show-ref --verify --quiet "refs/tags/$tag"; then printf 'local tag exists without matching remote release: %s\n' "$tag" >&2; exit 1; fi
          srht auth status >/dev/null
          [[ $submit_linux_build -eq 0 || -f "$linux_manifest" ]] || { printf 'Linux manifest not found: %s\n' "$linux_manifest" >&2; exit 1; }
          mode=release; [[ $resume -eq 1 ]] && mode=resume
          printf 'Release plan (%s):\n  repo: %s\n  base/local/remote main: %s\n  version/tag: %s / %s\n  validation apps: %s\n  artifact: .#release-artifact (tarball + checksum)\n  remote actions: %sartifact upload, refresh%s\n' "$mode" "$repo_root" "$base" "$version" "$tag" ${q (lib.concatStringsSep ", " ciApps)} "$([[ $resume -eq 0 ]] && printf 'atomic main + annotated tag, ' || true)" "$([[ $submit_linux_build -eq 1 ]] && printf ', Linux build' || true)"
          [[ $check_only -eq 0 ]] || exit 0
          mutated=0; tag_created=0; published=$resume; release_tmpdir="$(mktemp -d)"
          recovery_note() { printf '%s\n' 'release aborted after version stamping — inspect with "jj diff"; discard the prep commit with "jj abandon @" if you do not want it' >&2; }
          on_exit() { status=$?; rm -rf "$release_tmpdir"; if [[ $status -ne 0 && $tag_created -eq 1 && $published -eq 0 ]]; then git --git-dir="$git_dir" tag -d "$tag" >/dev/null 2>&1 || true; jj git import >/dev/null 2>&1 || true; fi; if [[ $status -ne 0 && $mutated -eq 1 && $published -eq 0 ]]; then recovery_note; fi; exit "$status"; }
          trap on_exit EXIT
          if [[ $resume -eq 0 ]]; then
            mutated=1
            args=(--version "$version"); [[ $allow_downgrade -eq 1 ]] && args+=(--allow-downgrade); nix run .#prepare-release -- "''${args[@]}"
            (${validateScript}) < /dev/null
            version="$(${readVersion})"; tag="v''${version#v}"
          fi
          artifact_dir="$(nix build .#release-artifact --no-link --print-out-paths)"
          artifacts=("$artifact_dir"/*.tar.gz); [[ ''${#artifacts[@]} -eq 1 && -f "''${artifacts[0]}.sha256" ]] || { printf '%s\n' 'expected exactly one tarball and checksum' >&2; exit 1; }
          (cd "$artifact_dir" && sha256sum -c "$(basename "''${artifacts[0]}").sha256")
          if [[ $resume -eq 0 ]]; then
            jj describe -m "chore: release $tag"
            commit="$(jj log -r @ --no-graph --color=never -T commit_id)"
            git --git-dir="$git_dir" -c tag.gpgSign=false tag -a "$tag" -m "${pname} $tag" "$commit"
            tag_created=1
            git --git-dir="$git_dir" push --atomic --force-with-lease="refs/heads/main:$remote_main" origin "$commit:refs/heads/main" "refs/tags/$tag:refs/tags/$tag"
            published=1
            jj git import
            jj bookmark set main --revision "$commit"
            jj new "$commit"
          else
            commit="$remote_tag_commit"
          fi
          existing="$(srht --json git artifact list -r ${q srhtRepo} --rev "$tag")"
          for f in "''${artifacts[0]}" "''${artifacts[0]}.sha256"; do
            if ARTIFACTS="$existing" NAME="$(basename "$f")" python3 -c 'import json,os,sys; data=json.loads(os.environ["ARTIFACTS"]); names=[item["filename"] for item in data["items"]]; sys.exit(0 if os.environ["NAME"] in names else 1)'; then printf 'artifact already uploaded: %s\n' "$(basename "$f")"; else srht git artifact upload -r ${q srhtRepo} --rev "$tag" "$f"; fi
          done
          submit_build_once() { build_tag="$1"; manifest="$2"; note="$3"; jobs="$(srht --json builds list --all --tag "$build_tag")"; if JOBS="$jobs" python3 -c 'import json,os,sys; failed={"failed","cancelled"}; statuses=[str(x["status"]).lower() for x in json.loads(os.environ["JOBS"])["items"]]; sys.exit(0 if any(s not in failed for s in statuses) else 1)'; then printf 'build already active or successful: %s\n' "$build_tag"; else srht builds submit "$manifest" --secrets --note "$note" --tag "$build_tag"; fi; }
          ${refreshTrigger}
          if [[ $submit_linux_build -eq 1 ]]; then submit_build_once "${pname}/$tag/linux" "$linux_manifest" "${pname} $tag linux release"; fi
          trap - EXIT; rm -rf "$release_tmpdir"
        '';
      };

    mkReleaseTarball = {
      pkgs,
      pname,
      version,
      contents,
    }: system: let
      platform =
        if pkgs.stdenv.hostPlatform.isDarwin && pkgs.stdenv.hostPlatform.isAarch64
        then "aarch64-darwin"
        else if pkgs.stdenv.hostPlatform.isDarwin && pkgs.stdenv.hostPlatform.isx86_64
        then "x86_64-darwin"
        else if pkgs.stdenv.hostPlatform.isLinux && pkgs.stdenv.hostPlatform.isAarch64
        then "aarch64-linux"
        else if pkgs.stdenv.hostPlatform.isLinux && pkgs.stdenv.hostPlatform.isx86_64
        then "x86_64-linux"
        else null;
      artifactName = "${pname}-v${version}-${platform}.tar.gz";
    in
      if platform == null
      then null
      else
        pkgs.runCommand "${pname}-release-artifact-${version}-${platform}" {nativeBuildInputs = with pkgs; [coreutils gnutar gzip];} ''
          stage="$TMPDIR/stage/${lib.removeSuffix ".tar.gz" artifactName}"; mkdir -p "$out" "$stage"
          ${contents system}
          tar --sort=name --format=ustar --mtime='@1' --owner=0 --group=0 --numeric-owner -C "$TMPDIR/stage" -cf - "${lib.removeSuffix ".tar.gz" artifactName}" | gzip -n > "$out/${artifactName}"
          (cd "$out" && sha256sum "${artifactName}" > "${artifactName}.sha256")
        '';

    mkWebReleaseTarball = {
      pkgs,
      pname,
      version,
      webPackage,
      entrypoint ? "index.html",
    }: let
      artifactName = "${pname}-v${version}-web.tar.gz";
      stem = lib.removeSuffix ".tar.gz" artifactName;
    in
      pkgs.runCommand "${pname}-web-release-artifact-${version}" {nativeBuildInputs = with pkgs; [coreutils findutils gnutar gzip];} ''
        stage="$TMPDIR/stage/${stem}"
        mkdir -p "$out" "$stage"
        cp -R --no-preserve=mode,ownership,timestamps ${webPackage}/. "$stage/"
        [ -f "$stage/${entrypoint}" ] || { printf 'web bundle entrypoint missing: %s\n' ${q entrypoint} >&2; exit 1; }
        if [[ -n "$(find "$stage" -type l -print -quit)" ]]; then
          printf 'web bundle must not contain symlinks\n' >&2
          exit 1
        fi
        find "$stage" -type d -exec chmod 0755 {} +
        find "$stage" -type f -exec chmod 0644 {} +
        tar --sort=name --format=posix --pax-option=delete=atime,delete=ctime --mtime='@1' --owner=0 --group=0 --numeric-owner -C "$TMPDIR/stage" -cf - ${q stem} | gzip -n > "$out/${artifactName}"
        (cd "$out" && sha256sum "${artifactName}" > "${artifactName}.sha256")
      '';
  };

  presets.rust = args @ {
    pkgs,
    self,
    pname,
    binaries ? [pname],
    subdir ? pname,
    srhtRepo ? pname,
    versionMode ? "package",
    versionFile ? "Cargo.toml",
    lockPackages ? [pname],
    workspaceDepPins ? [],
    changelog ? "CHANGELOG.md",
    # Extra packages on PATH for the ci-* apps, e.g. interpreters that tests
    # spawn as subprocesses (CI images have no system python3 etc.).
    ciExtraInputs ? [],
    ciFmt ? null,
    ciClippy ? null,
    # Additional flake app names run after release preparation, after the
    # standard Rust fmt/clippy/test gates (for example, ["ci-docs"]).
    releaseValidationApps ? [],
    # Extra cheap static gates to compose into the ecosystem-agnostic
    # static-checks app (e.g. deny, machete, statix). These are derivations,
    # not flake app names, so running static-checks does not re-evaluate Nix.
    extraStaticChecks ? [],
    srhtPackage ? null,
    ...
  }: let
    cargoVersionExpr =
      if versionMode == "workspace"
      then ''data.get("workspace", {}).get("package", {}).get("version")''
      else ''data.get("package", {}).get("version")'';
    versionToml = builtins.fromTOML (builtins.readFile (self + "/${versionFile}"));
    version =
      if versionMode == "workspace"
      then versionToml.workspace.package.version
      else versionToml.package.version;
    rustToolchain = with pkgs; [cargo clippy rustc rustfmt stdenv.cc] ++ lib.optionals stdenv.isDarwin [libiconv];
    ciToolchain = rustToolchain ++ ciExtraInputs;
    darwinLinkEnv = lib.optionalString pkgs.stdenv.isDarwin ''
      export LIBRARY_PATH="${pkgs.libiconv}/lib''${LIBRARY_PATH:+:$LIBRARY_PATH}"
    '';
    setCargoVersion = ''
      VERSION="$version" VERSION_MODE=${q versionMode} VERSION_FILE=${q versionFile} LOCK_PACKAGES=${q (lib.concatStringsSep "," lockPackages)} WORKSPACE_DEP_PINS=${q (lib.concatStringsSep "," workspaceDepPins)} python3 - <<'PY'
      from pathlib import Path
      import os, re, sys, tomllib
      version = os.environ["VERSION"]
      version_file = Path(os.environ["VERSION_FILE"])
      mode = os.environ["VERSION_MODE"]
      def parse_semver(value):
          if not isinstance(value, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
              raise SystemExit(f"invalid semver version: {value}")
          return tuple(int(part) for part in value.removeprefix("v").split("."))
      data = tomllib.loads(version_file.read_text())
      current = (data.get("workspace", {}).get("package", {}) if mode == "workspace" else data.get("package", {})).get("version")
      current_tuple = parse_semver(current)
      if not version:
          version = current
      requested_tuple = parse_semver(version)
      if requested_tuple < current_tuple and os.environ.get("ALLOW_DOWNGRADE") != "1":
          raise SystemExit(f"refusing version downgrade: requested {version.removeprefix('v')} is lower than current {current.removeprefix('v')} (pass --allow-downgrade to override)")
      if requested_tuple == current_tuple:
          print(f"prepare-release: requested version {version.removeprefix('v')} matches current version", file=sys.stderr)
      version = version.removeprefix("v")
      text = version_file.read_text()
      if mode == "workspace":
          text, count = re.subn(r'(?ms)(\[workspace\.package\].*?^version = ")[^"]+?("\s*)', rf'\g<1>{version}\2', text, count=1)
          if count != 1: raise SystemExit("could not update [workspace.package] version")
      else:
          text, count = re.subn(r'(?ms)(\[package\].*?^version = ")[^"]+?("\s*)', rf'\g<1>{version}\2', text, count=1)
          if count != 1: raise SystemExit("could not update [package] version")
      for dep in filter(None, os.environ["WORKSPACE_DEP_PINS"].split(",")):
          pat = rf'(?m)^({re.escape(dep)} = \{{[^\n]*?version = ")[^"]+?("[^\n]*?\}})$'
          text, count = re.subn(pat, rf'\g<1>{version}\2', text, count=1)
          if count != 1: raise SystemExit(f"could not update [workspace.dependencies] {dep} pin")
      version_file.write_text(text)
      lock = Path("Cargo.lock")
      if lock.exists():
          lock_text = lock.read_text()
          for name in filter(None, os.environ["LOCK_PACKAGES"].split(",")):
              lock_text, count = re.subn(rf'(\[\[package\]\]\nname = "{re.escape(name)}"\nversion = ")[^"]+(")', rf'\g<1>{version}\2', lock_text, count=1)
              if count != 1: raise SystemExit(f"could not update Cargo.lock package {name}")
          lock.write_text(lock_text)
      Path(".fleet-release-version").write_text(version)
      PY
      version="$(cat .fleet-release-version)"
      rm .fleet-release-version
    '';
    prepareRelease = core.mkPrepareRelease {
      inherit pkgs pname changelog;
      setVersion = setCargoVersion;
      verify = "cargo check --locked --workspace";
      runtimeInputs = rustToolchain;
    };
    releaseTag = core.mkReleaseTag {
      inherit pkgs pname versionFile;
      versionExpr = cargoVersionExpr;
    };
    refreshTrigger = core.mkRefreshTriggerManifest {inherit pname subdir;};
    release = core.mkRelease {
      inherit pkgs pname srhtRepo versionFile refreshTrigger prepareRelease releaseTag srhtPackage;
      versionExpr = cargoVersionExpr;
      runtimeInputs = rustToolchain;
      ciApps = ["ci-fmt" "ci-clippy" "ci-test"] ++ releaseValidationApps;
      artifactPackage = releaseArtifact;
    };
    defaultCiFmt = pkgs.writeShellApplication {
      name = "ci-fmt";
      runtimeInputs = ciToolchain;
      text = darwinLinkEnv + "\ncargo fmt --all -- --check\n";
    };
    defaultCiClippy = pkgs.writeShellApplication {
      name = "ci-clippy";
      runtimeInputs = ciToolchain;
      text = darwinLinkEnv + "\ncargo clippy --locked --workspace --all-targets -- -D warnings\n";
    };
    ciFmtDrv =
      if ciFmt == null
      then defaultCiFmt
      else ciFmt;
    ciClippyDrv =
      if ciClippy == null
      then defaultCiClippy
      else ciClippy;
    staticChecks = pkgs.writeShellApplication {
      name = "static-checks";
      text = lib.concatMapStringsSep "\n" (drv: "${lib.getExe drv}") ([ciFmtDrv ciClippyDrv] ++ extraStaticChecks);
    };
    ciTest = pkgs.writeShellApplication {
      name = "ci-test";
      runtimeInputs = ciToolchain;
      text = darwinLinkEnv + "\nTERM=dumb cargo test --workspace < /dev/null\n";
    };
    releaseArtifact = core.mkReleaseTarball {
      inherit pkgs pname version;
      contents = system: ''
        ${lib.concatMapStringsSep "\n" (b: ''cp -p ${self.packages.${system}.${b}}/bin/${b} "$stage/${b}"; chmod 0555 "$stage/${b}"'') binaries}
        for f in README.md CHANGELOG.md LICENSE LICENSE-APACHE LICENSE-MIT; do [ -e ${self}/$f ] && cp -p ${self}/$f "$stage/$f" || true; done
      '';
    };
  in {
    packages.release-artifact = releaseArtifact;
    apps = {
      prepare-release = app prepareRelease;
      release-tag = app releaseTag;
      release = app release;
      ci-fmt = app ciFmtDrv;
      ci-clippy = app ciClippyDrv;
      static-checks = app staticChecks;
      ci-test = app ciTest;
    };
    inherit releaseArtifact;
  };

  # Ecosystem-neutral browser-game preset. The caller owns its package manager,
  # build, and CI derivations; this preset owns JSON semver release mechanics.
  presets.webGame = {
    pkgs,
    self,
    pname,
    webPackage,
    subdir ? pname,
    srhtRepo ? pname,
    versionFile ? "package.json",
    changelog ? "CHANGELOG.md",
    manifestPath ? "builds/release-web.yml",
    entrypoint ? "index.html",
    ciFmt ? null,
    ciTest ? null,
    ciCheck ? null,
    prepareVerify ? null,
    extraStaticChecks ? [],
    srhtPackage ? null,
    ...
  }: let
    packageJson = builtins.fromJSON (builtins.readFile (self + "/${versionFile}"));
    version = packageJson.version or (throw "${versionFile} must contain a version string");
    checkedVersion =
      if builtins.isString version && builtins.match "[0-9]+\\.[0-9]+\\.[0-9]+" version != null
      then version
      else throw "${versionFile} version must be X.Y.Z semver";
    versionCommand = ''VERSION_FILE=${q versionFile} python3 -c 'import json,os; value=json.load(open(os.environ["VERSION_FILE"])).get("version"); assert isinstance(value,str) and value; print(value)' '';
    setJsonVersion = ''
      VERSION="$version" VERSION_FILE=${q versionFile} python3 - <<'PY'
      from pathlib import Path
      import json, os, re, sys
      path = Path(os.environ["VERSION_FILE"])
      data = json.loads(path.read_text())
      current = data.get("version")
      requested = os.environ["VERSION"] or current
      def semver(value):
          if not isinstance(value, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
              raise SystemExit(f"invalid semver version: {value}")
          return tuple(int(part) for part in value.removeprefix("v").split("."))
      current_tuple, requested_tuple = semver(current), semver(requested)
      if requested_tuple < current_tuple and os.environ.get("ALLOW_DOWNGRADE") != "1":
          raise SystemExit(f"refusing version downgrade: requested {requested.removeprefix('v')} is lower than current {current.removeprefix('v')} (pass --allow-downgrade to override)")
      if requested_tuple == current_tuple:
          print(f"prepare-release: requested version {requested.removeprefix('v')} matches current version", file=sys.stderr)
      data["version"] = requested.removeprefix("v")
      path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
      print(data["version"])
      PY
      version="$(${versionCommand})"
    '';
    releaseArtifact = core.mkWebReleaseTarball {
      inherit pkgs pname webPackage entrypoint;
      version = checkedVersion;
    };
    ciWeb = pkgs.writeShellApplication {
      name = "ci-web";
      runtimeInputs = with pkgs; [coreutils];
      text = ''
        set -euo pipefail
        [ -f "${webPackage}/${entrypoint}" ] || { printf 'web bundle entrypoint missing: %s\n' ${q entrypoint} >&2; exit 1; }
        artifacts=(${releaseArtifact}/*-web.tar.gz)
        [[ ''${#artifacts[@]} -eq 1 && -f "''${artifacts[0]}" && -f "''${artifacts[0]}.sha256" ]] || { printf 'expected exactly one web release artifact and checksum\n' >&2; exit 1; }
        (cd ${releaseArtifact} && sha256sum -c "$(basename "''${artifacts[0]}").sha256")
      '';
    };
    prepareRelease = core.mkPrepareRelease {
      inherit pkgs pname changelog manifestPath;
      artifactSuffix = "web";
      requireManifestReplacement = true;
      setVersion = setJsonVersion;
      verify = prepareVerify;
      runtimeInputs = lib.optionals (prepareVerify != null) [pkgs.nix];
    };
    releaseTag = core.mkReleaseTag {inherit pkgs pname versionFile versionCommand;};
    refreshTrigger = core.mkRefreshTriggerManifest {inherit pname subdir;};
    namedCi = lib.filterAttrs (_: drv: drv != null) {
      ci-fmt = ciFmt;
      ci-test = ciTest;
      ci-check = ciCheck;
    };
    release = core.mkRelease {
      inherit pkgs pname srhtRepo versionFile versionCommand refreshTrigger prepareRelease releaseTag srhtPackage;
      artifactPackage = releaseArtifact;
      allowLinuxBuild = false;
      ciApps = builtins.attrNames namedCi ++ ["ci-web"];
    };
    staticChecks = pkgs.writeShellApplication {
      name = "static-checks";
      text = let
        checks = lib.filter (drv: drv != null) [ciFmt ciCheck] ++ extraStaticChecks;
      in
        if checks == []
        then "true"
        else lib.concatMapStringsSep "\n" (drv: lib.getExe drv) checks;
    };
  in {
    packages.release-artifact = releaseArtifact;
    apps =
      lib.mapAttrs (_: drv: app drv) namedCi
      // {
        prepare-release = app prepareRelease;
        release-tag = app releaseTag;
        release = app release;
        ci-web = app ciWeb;
        static-checks = app staticChecks;
      };
    inherit releaseArtifact;
  };
in {
  fleet = {inherit core presets;};
  mkFleetApps = presets.rust;
}
