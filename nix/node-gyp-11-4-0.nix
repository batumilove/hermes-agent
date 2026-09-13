{
  buildNpmPackage,
  fetchFromGitHub,
  nodejs,
  lib,
}:

let
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
      # This derivation does not build or run node-gyp's development suite.
      # Remove its dev-only dependency graph so it is neither fetched nor shipped.
      substituteInPlace package.json --replace-fail \
        $'  "devDependencies": {\n    "bindings": "^1.5.0",\n    "cross-env": "^7.0.3",\n    "eslint": "^9.16.0",\n    "mocha": "^11.0.1",\n    "nan": "^2.14.2",\n    "neostandard": "^0.11.9",\n    "require-inject": "^1.4.4"\n  },\n' \
        ""
      ln -s ${./node-gyp-11-4-0-package-lock.json} package-lock.json
    '';

    npmDepsHash = "sha256-F7QTUSpxoHQ9cDnIpDjHkHIXETRmOMNJwQC60Q5jofI=";

    npmDepsFetcherVersion = 2;

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
