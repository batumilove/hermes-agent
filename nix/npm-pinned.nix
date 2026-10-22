{
  stdenv,
  makeWrapper,
  fetchurl,
  nodejs_26,
  callPackage,
}:
let
  # pm/lock.json is the one pin table. Every artifact carries its resolved
  # url + sha256, so nix consumes it as pure data — no version or url
  # knowledge lives on the nix side.
  lock = builtins.fromJSON (builtins.readFile ../pm/lock.json);
  mirror = builtins.fromJSON (builtins.readFile ../pm/artifact-mirror.json);
  pin = lock.packages.npm;
  artifacts = if builtins.isList pin.artifacts.any then pin.artifacts.any else [ pin.artifacts.any ];
  fetchArtifact = artifact: fetchurl {
    urls = [ artifact.url "${mirror.origin}/${mirror.prefix}${artifact.sha256}" ];
    sha256 = artifact.sha256;
  };
  python = (callPackage ./pythonLock.nix { }).interpreter;
  stageArtifacts = builtins.concatStringsSep "\n" (map (artifact:
    "cp ${fetchArtifact artifact} \"$TMPDIR/npm-security-artifacts/${builtins.baseNameOf artifact.url}\""
  ) (builtins.tail artifacts));
in
stdenv.mkDerivation {
  pname = "npm";
  version = pin.version;

  src = fetchArtifact (builtins.head artifacts);

  nativeBuildInputs = [ makeWrapper ];
  dontBuild = true;

  installPhase = ''
    mkdir -p "$out/lib/node_modules/npm" "$TMPDIR/npm-security-artifacts"
    cp -r . "$out/lib/node_modules/npm/"
    ${stageArtifacts}
    # Reuse PM's exact layout/version validation and replacement. No npm
    # lifecycle scripts run; every source hash comes from the single PM row.
    mkdir -p "$TMPDIR/npm-security-runtime"
    ln -s ${../pm} "$TMPDIR/npm-security-runtime/pm"
    PYTHONPATH="$TMPDIR/npm-security-runtime" ${python}/bin/python3 - "$out" "${pin.version}" "$TMPDIR/npm-security-artifacts" <<'PY'
from pathlib import Path
import sys
from pm.npm_security_overlay import apply_overlay, unpack_overlay, verify_overlay
entry, version, artifacts = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
for archive in sorted(artifacts.iterdir()):
    if not unpack_overlay(archive, entry):
        raise SystemExit("unexpected npm archive in security overlay artifacts")
apply_overlay(entry, version, "posix")
problem = verify_overlay(entry, "posix")
if problem:
    raise SystemExit(problem)
PY
    mkdir -p $out/bin

    makeWrapper ${nodejs_26}/bin/node $out/bin/npm \
      --add-flags "$out/lib/node_modules/npm/bin/npm-cli.js"
    makeWrapper ${nodejs_26}/bin/node $out/bin/npx \
      --add-flags "$out/lib/node_modules/npm/bin/npx-cli.js"
  '';
}
