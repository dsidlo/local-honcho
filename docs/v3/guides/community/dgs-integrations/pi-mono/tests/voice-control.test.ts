/**
 * voice-control extension tests.
 *
 * Covers the settings store (voice / speed / speaker / autospeak persistence)
 * and the TTS engine controls that delegate to Grok's tts-toggle (per-engine
 * volume, engine selection, device switching backed by ~/.grok/tts/config.json).
 *
 * child_process is mocked file-scoped, so this module must be imported BEFORE
 * the dynamically imported fresh extension instance in every test.
 */
import { describe, test, expect, beforeEach, afterAll } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { installChildProcessMock } from "./helpers/mock-child-process.ts";
import { createMockPi } from "./helpers/mock-pi.ts";
import { importFresh } from "./helpers/env.ts";

const cp = installChildProcessMock();
const EXTENSION_PATH = new URL(
  "../agent/extensions/voice-control.ts",
  import.meta.url,
).pathname;

// Each test gets a sandbox: its own tts config and voice-settings.json.
const WORK: string[] = [];

function sandbox(): { dir: string; ttsConfig: string; settingsFile: string } {
  const dir = mkdtempSync(join(tmpdir(), "vc-test-"));
  WORK.push(dir);
  const ttsConfig = join(dir, "tts-config.json");
  const settingsFile = join(dir, "voice-settings.json");
  writeFileSync(
    ttsConfig,
    JSON.stringify({
      enabled: true,
      engine: "kokoro",
      kokoro: { device: "gpu", volume: 10 },
      piper: { voice: "en_GB-jenny_dioco-medium", volume: 8 },
    }),
  );
  process.env.PI_VOICE_SETTINGS = settingsFile;
  process.env.GROK_TTS_CONFIG = ttsConfig;
  process.env.GROK_TTS_TOGGLE = join(dir, "tts-toggle.py"); // path only; exec is mocked
  delete process.env.GROK_TTS_SRC;
  return { dir, ttsConfig, settingsFile };
}

async function loadExtension(): Promise<ReturnType<typeof createMockPi>> {
  const mod = await importFresh(EXTENSION_PATH);
  const mock = createMockPi();
  mod.default(mock.pi);
  return mock;
}

function runToggleCommand(mock: any, args: string) {
  return mock.runCommand("vr-settings", args);
}

function lastNotification(mock: any) {
  return mock.notifications[mock.notifications.length - 1];
}

beforeEach(() => cp.reset());

afterAll(() => {
  for (const dir of WORK) rmSync(dir, { recursive: true, force: true });
  delete process.env.PI_VOICE_SETTINGS;
  delete process.env.GROK_TTS_CONFIG;
  delete process.env.GROK_TTS_TOGGLE;
});

describe("voice-control settings store", () => {
  test("registers the voice commands", async () => {
    const mock = await loadExtension();
    expect(mock.commands.has("vr-settings")).toBe(true);
    expect(mock.commands.has("vc-settings")).toBe(true);
    expect(mock.commands.has("voice-settings")).toBe(true);
  });

  test("voice settings persist to PI_VOICE_SETTINGS and survive reload", async () => {
    const { settingsFile } = sandbox();
    const mock = await loadExtension();

    await runToggleCommand(mock, "voice am_michael");
    await runToggleCommand(mock, "speed 1.7");
    await runToggleCommand(mock, "autospeak off");

    const saved = JSON.parse(readFileSync(settingsFile, "utf-8"));
    expect(saved.response.voice.active).toBe("am_michael");
    expect(saved.response.voice.speed).toBe(1.7);
    expect(saved.response.behavior.autoSpeak).toBe(false);

    // A fresh extension instance loads the persisted values.
    const reloaded = await loadExtension();
    await runToggleCommand(reloaded, "");
    const shown = lastNotification(reloaded).message;
    expect(shown).toContain("am_michael");
    expect(shown).toContain("1.7");
    expect(shown).toContain("Auto-Speak: Off");
  });

  test("invalid speed is rejected without changing settings", async () => {
    const { settingsFile } = sandbox();
    const mock = await loadExtension();
    // First load writes the defaults file (boot migration).
    const defaults = JSON.parse(readFileSync(settingsFile, "utf-8"));
    expect(defaults.response.voice.speed).toBe(1.5);

    await runToggleCommand(mock, "speed 9.9");
    const note = lastNotification(mock);
    expect(note.level).toBe("error");
    expect(note.message).toContain("0.5-3.0");
    // Nothing was saved after the failed command.
    const after = JSON.parse(readFileSync(settingsFile, "utf-8"));
    expect(after.response.voice.speed).toBe(1.5);
    expect(after.lastUpdated).toBe(defaults.lastUpdated);
  });
});

describe("vr-settings volume (per-engine, persisted)", () => {
  test("shows both engines' levels, highlighting the current engine", async () => {
    const { ttsConfig } = sandbox();
    writeFileSync(
      ttsConfig,
      JSON.stringify({
        engine: "piper",
        kokoro: { device: "gpu", volume: 10 },
        piper: { volume: 4 },
      }),
    );
    const mock = await loadExtension();
    await runToggleCommand(mock, "volume");
    const { message, level } = lastNotification(mock);
    expect(level).toBe("info");
    expect(message).toContain("current engine piper");
    expect(message).toContain("4/10");
    expect(message).toContain("kokoro: 10/10");
  });

  test("volume N delegates to tts-toggle volume N and surfaces its output", async () => {
    const mock = await loadExtension();
    cp.script(
      "tts-toggle.py volume 7",
      "kokoro volume=7 (piper volume=8)",
    );
    await runToggleCommand(mock, "volume 7");
    const { message, level } = lastNotification(mock);
    expect(level).toBe("success");
    expect(message).toBe("🔊 kokoro volume=7 (piper volume=8)");
    const call = cp.execCalls.find((c) => c.cmd.includes("tts-toggle.py volume 7"));
    expect(call).toBeDefined();
  });

  test("volume is a whole number 1-10", async () => {
    const mock = await loadExtension();
    for (const bad of ["0", "11", "7.5", "abc"]) {
      await runToggleCommand(mock, `volume ${bad}`);
      expect(lastNotification(mock).level).toBe("error");
      expect(lastNotification(mock).message).toContain("1-10");
    }
    expect(cp.execCalls.some((c) => c.cmd.includes("tts-toggle.py volume"))).toBe(false);
  });

  test("engine state stays untouched when volume is delegable but toggle is missing", async () => {
    const { ttsConfig } = sandbox();
    const mock = await loadExtension();
    // No scripted output => toggle unresolvable.
    await runToggleCommand(mock, "volume 5");
    expect(lastNotification(mock).level).toBe("error");
    expect(lastNotification(mock).message).toContain("TTS toggle unavailable");
    const cfg = JSON.parse(readFileSync(ttsConfig, "utf-8"));
    expect(cfg.kokoro.volume).toBe(10);
  });
});

describe("vr-settings engine and device delegation", () => {
  test("engine piper goes through tts-toggle engine piper", async () => {
    const mock = await loadExtension();
    cp.script("tts-toggle.py engine piper", "on engine=piper voice=af_heart");
    await runToggleCommand(mock, "engine piper");
    const { message, level } = lastNotification(mock);
    expect(level).toBe("success");
    expect(message).toBe("🔊 on engine=piper voice=af_heart");
  });

  test("device cpu goes through tts-toggle device cpu (no daemon restart in the extension)", async () => {
    const mock = await loadExtension();
    cp.script("tts-toggle.py device cpu", "restarted on cpu pid=1");
    await runToggleCommand(mock, "device cpu");
    const { message } = lastNotification(mock);
    expect(message).toBe("🔊 restarted on cpu pid=1");
    // The extension no longer manages the daemon itself for device changes.
    expect(cp.spawnCalls).toHaveLength(0);
    expect(cp.killCalls).toHaveLength(0);
  });

  test("engine argument is validated", async () => {
    const mock = await loadExtension();
    await runToggleCommand(mock, "engine espeak");
    expect(lastNotification(mock).level).toBe("error");
    await runToggleCommand(mock, "device tape");
    expect(lastNotification(mock).level).toBe("error");
  });

  test("no-arg engine/device report the running engine state", async () => {
    const { ttsConfig } = sandbox();
    writeFileSync(
      ttsConfig,
      JSON.stringify({
        engine: "kokoro",
        kokoro: { device: "cpu", volume: 9 },
        piper: { volume: 3 },
      }),
    );
    const mock = await loadExtension();
    await runToggleCommand(mock, "engine");
    expect(lastNotification(mock).message).toContain("engine=kokoro device=cpu");
    await runToggleCommand(mock, "device");
    expect(lastNotification(mock).message).toContain("device=cpu");
    await runToggleCommand(mock, "volume");
    expect(lastNotification(mock).message).toContain("kokoro: 9/10, piper: 3/10");
  });

  test("panel shows engine summary plus voice settings", async () => {
    sandbox();
    const mock = await loadExtension();
    await runToggleCommand(mock, "");
    const message = lastNotification(mock).message;
    expect(message).toContain("engine=kokoro device=gpu");
    expect(message).toContain("kokoro volume=10/10 piper volume=8/10");
    expect(message).toContain("Auto-Speak: On");
    expect(message).toContain("/vr-settings volume");
  });

  test("missing grok config falls back to defaults", async () => {
    const { ttsConfig, dir } = sandbox();
    rmSync(ttsConfig);
    const mock = await loadExtension();
    await runToggleCommand(mock, "volume");
    const message = lastNotification(mock).message;
    expect(message).toContain("current engine kokoro");
    expect(message).toContain("kokoro: 10/10, piper: 8/10");
    // toggles still work against a fresh config file if written afterwards
    writeFileSync(ttsConfig, JSON.stringify({ engine: "kokoro" }));
    expect(join(dir, "tts-config.json")).toBe(ttsConfig);
  });
});

describe("vr-settings voice and speed", () => {
  test("voice names persist; speak.sh falls back for unknown engines", async () => {
    const { settingsFile } = sandbox();
    const mock = await loadExtension();
    await runToggleCommand(mock, "voice non_voice");
    const note = lastNotification(mock);
    expect(note.level).toBe("success");
    expect(note.message).toContain("non_voice");
    const saved = JSON.parse(readFileSync(settingsFile, "utf-8"));
    expect(saved.response.voice.active).toBe("non_voice");
  });

  test("speed persists fractional values", async () => {
    const { settingsFile } = sandbox();
    const mock = await loadExtension();
    await runToggleCommand(mock, "speed 0.5");
    const saved = JSON.parse(readFileSync(settingsFile, "utf-8"));
    expect(saved.response.voice.speed).toBe(0.5);
  });
});