{
  buildNpmPackage,
  fetchFromGitHub,
  importNpmLock,
  nodejs,
  lib,
}:

let
  productionManifest = lib.importJSON ./node-gyp-11-4-0-package.json;
  productionLock = lib.importJSON ./node-gyp-11-4-0-package-lock.json;
  node-gyp-11_4_0 = buildNpmPackage rec {
    pname = "node-gyp";
    version = "11.4.0";

    src = fetchFromGitHub {
      owner = "nodejs";
      repo = "node-gyp";
      rev = "refs/tags/v${version}";
      hash = "sha256-VtomUV+0kTp34IuS0D0dR4ZMWpk4Ptpk1CBP8rdW2a4=";
    };

    postPatch = ''
      # Build only the production graph with its reviewed security overrides.
      cp ${./node-gyp-11-4-0-package.json} package.json
      ln -s ${./node-gyp-11-4-0-package-lock.json} package-lock.json
    '';

    # As in nix/lib.nix, each artifact is bound by its lockfile integrity.
    npmDeps = importNpmLock {
      package = productionManifest;
      packageLock = productionLock;
    };
    npmConfigHook = importNpmLock.npmConfigHook;

    dontNpmBuild = true;

    makeWrapperArgs = [ "--set npm_config_nodedir ${nodejs}" ];

    meta = {
      description = "Node.js native addon build tool";
      homepage = "https://github.com/nodejs/node-gyp";
      license = lib.licenses.mit;
      mainProgram = "node-gyp";
    };
  };
in
node-gyp-11_4_0
