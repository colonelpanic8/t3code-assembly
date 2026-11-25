const assert = require("node:assert/strict");
const { mkdtemp, rm } = require("node:fs/promises");
const { createRequire } = require("node:module");
const { tmpdir } = require("node:os");
const path = require("node:path");

const requireFromSource = createRequire(path.resolve("source/scripts/package.json"));
const { _electron } = requireFromSource("playwright-core");

async function main() {
  const home = await mkdtemp(path.join(tmpdir(), "t3code-client-"));
  let app;
  try {
    app = await _electron.launch({
      executablePath: path.resolve(process.argv[2]),
      env: {
        ...process.env,
        APPDATA: path.join(home, "appdata"),
        T3CODE_HOME: path.join(home, "t3"),
        T3CODE_TELEMETRY_ENABLED: "false",
      },
      timeout: 60_000,
    });
    const window = await app.firstWindow({ timeout: 60_000 });
    await window.waitForFunction(() => window.desktopBridge?.getBackendModeState?.(), undefined, {
      timeout: 60_000,
    });
    const mode = await window.evaluate(() => window.desktopBridge.getBackendModeState());
    assert.equal(mode.effectiveMode, "client-only");
    assert.equal(mode.source, "settings");
    assert.match(window.url(), /^t3code:\/\/app\b/);
    console.log("The installed desktop opened in client-only mode.");
  } finally {
    try {
      await app?.close();
    } finally {
      await rm(home, { recursive: true, force: true, maxRetries: 5 });
    }
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
