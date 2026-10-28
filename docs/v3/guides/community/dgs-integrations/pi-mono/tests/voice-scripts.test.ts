/**
 * Voice-response script tests: per-engine volume plumbing.
 *
 * - tts-toggle.py: engine/device/volume control backed by a GROK_TTS_CONFIG
 *   temp file (the real grok-build toggle, so persistence semantics are
 *   exercised end to end).
 * - tts-gain.py: level-to-gain mapping including legacy float levels.
 * - speak.sh: reads engine from the grok config, passes the piper gain via
 *   --volume, and keeps engine branches separated.
 *
 * These tests spawn real processes with sandboxed env; they never touch the
 * live ~/.grok/tts/config.json. speak.sh runs with GROK_TTS_PYTHON pointing
 * at a fake python (records argv), daemon/direct paths pointed away so no
 * daemon is spawned, GROK_TTS_PIPER_LOG capturing the session piper log,
 * and VOICE_SKILL_CONFIG pointing at a nonexistent file so the skill's
 * shipped config.json (this repo's live voice state) cannot bleed in.
 */
import { describe, test, expect, beforeEach, afterAll } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const REPO = import.meta.dir + "/..";
const SCRIPTS = join(REPO, "agent/voice-response/scripts");
const GROK_SRC = join(REPO, "../grok-build/src");
const SYS_PY = process.env.PYTHON_SYS || "python3";

const WORK: string[] = [];
let dir = "";
let ttsConfig = "";
let piperLog = "";
let argvLog = "";
let fakePython = "";

function resetEnv() {
  delete process.env.GROK_TTS_CONFIG;
  delete process.env.GROK_TTS_SRC;
  delete process.env.GROK_TTS_PYTHON;
  delete process.env.GROK_TTS_PIPER_PYTHON;
  delete process.env.GROK_TTS_PIPER_SPEAK;
  delete process.env.GROK_TTS_DAEMON_SCRIPT;
  delete process.env.GROK_TTS_DIRECT_TTS;
  delete process.env.GROK_TTS_PIPER_VOICES;
  delete process.env.GROK_TTS_PIPER_LOG;
  delete process.env.PI_SESSION_ID;
  delete process.env.VOICE_SKILL_CONFIG;
  delete process.env.PIPER_LOG;
}

/** Fresh sandbox: tts config (engine=kokoro by default) + fake piper python. */
function sandbox(grokConfig: object) {
  dir = mkdtempSync(join(tmpdir(), "voice-scripts-"));
  WORK.push(dir);
  ttsConfig = join(dir, "tts-config.json");
  writeFileSync(ttsConfig, JSON.stringify(grokConfig));
  piperLog = join(dir, "piper.log");
  argvLog = join(dir, "argv.log");
  fakePython = join(dir, "piper-python.sh");
  // Fake Piper interpreter: record every argv to ARGV_LOG (speak.sh exports
  // PIPER_LOG itself for the per-session scrub log, so argv can't share it),
  // then exit cleanly.
  writeFileSync(
    fakePython,
    [
      "#!/bin/bash",
      `{ echo "ARGV:" "$@" >> "$ARGV_LOG"; } 2>/dev/null`,
      "exit 0",
    ].join("\n"),
    { mode: 0o755 },
  );
  // speak.sh requires an existing piper script before executing it.
  writeFileSync(join(dir, "piper.py"), "# fake piper speaker\n");
  return { dir, ttsConfig, fakePython };}

function baseEnv(extra: Record<string, string> = {}) {
  return {
    ...process.env,
    GROK_TTS_CONFIG: ttsConfig,
    GROK_TTS_SRC: GROK_SRC,
    // speak.sh exports PIPER_LOG (session scrub log); argv goes to ARGV_LOG.
    ARGV_LOG: argvLog,
    GROK_TTS_PIPER_LOG: piperLog,
    // Nonexistent file: no activeVoice bleed from the shipped config.json.
    VOICE_SKILL_CONFIG: join(dir, "skill-config.json"),
    // Nonexistent per-repo voice map: neutralize resolve-voice.sh per-repo
    // resolution so tests control engine/voice via GROK_TTS_CONFIG + env.
    PI_VOICE_REPO_MAP: join(dir, "no-repo-map.json"),
    ...extra,
  };
}

const piperArgs = () =>
  existsSync(argvLog)
    ? readFileSync(argvLog, "utf-8").split(/\r?\n/).filter(Boolean)
    : [];

function spawnProc(cmd: string[], env: Record<string, string>) {
  return Bun.spawnSync(cmd, { env, stdout: "pipe", stderr: "pipe" });
}

function ttsGain(engine: string, env = baseEnv()) {
  return spawnProc([SYS_PY, join(SCRIPTS, "tts-gain.py"), engine], {
    ...env,
    PIPER_LOG: piperLog,
  });
}

beforeEach(resetEnv);

afterAll(() => {
  for (const d of WORK) rmSync(d, { recursive: true, force: true });
});

describe("tts-toggle.py (grok-build toggle through the wrapper)", () => {
  test("volume without args reports both engines' persisted levels", () => {
    sandbox({ engine: "kokoro", kokoro: { volume: 7 }, piper: { volume: 8 } });
    const r = spawnProc([SYS_PY, join(SCRIPTS, "tts-toggle.py"), "volume"], baseEnv());
    expect(r.exitCode).toBe(0);
    expect(r.stdout.toString().trim()).toBe("kokoro volume=7 (piper volume=8)");
  });

  test("volume N writes the CURRENT engine's level, keeping the other engine", () => {
    sandbox({ engine: "piper", kokoro: { volume: 7 }, piper: { volume: 4 } });
    const r = spawnProc([SYS_PY, join(SCRIPTS, "tts-toggle.py"), "volume", "3"], baseEnv());
    expect(r.exitCode).toBe(0);
    expect(r.stdout.toString().trim()).toBe("piper volume=3 (kokoro volume=7)");
    const saved = JSON.parse(readFileSync(ttsConfig, "utf-8"));
    expect(saved.piper.volume).toBe(3); // current engine changed...
    expect(saved.kokoro.volume).toBe(7); // ...the other engine kept its level
  });

  test("levels persist across separate invocations (real file round-trip)", () => {
    sandbox({ engine: "kokoro", kokoro: { volume: 10 }, piper: { volume: 8 } });
    const env = baseEnv();
    spawnProc([SYS_PY, join(SCRIPTS, "tts-toggle.py"), "volume", "5"], env);
    const r = spawnProc([SYS_PY, join(SCRIPTS, "tts-toggle.py"), "volume"], env);
    expect(r.stdout.toString().trim()).toBe("kokoro volume=5 (piper volume=8)");
  });

  test("engine switch persists and volume follows the new engine", () => {
    sandbox({ engine: "kokoro", kokoro: { volume: 10 }, piper: { volume: 8 } });
    const env = baseEnv();
    spawnProc([SYS_PY, join(SCRIPTS, "tts-toggle.py"), "engine", "piper"], env);
    spawnProc([SYS_PY, join(SCRIPTS, "tts-toggle.py"), "volume", "9"], env);
    const saved = JSON.parse(readFileSync(ttsConfig, "utf-8"));
    expect(saved.engine).toBe("piper");
    expect(saved.piper.volume).toBe(9);
    expect(saved.kokoro.volume).toBe(10);
  });

  test("out-of-range volumes exit with usage", () => {
    sandbox({ engine: "kokoro", kokoro: { volume: 10 }, piper: { volume: 8 } });
    const r = spawnProc([SYS_PY, join(SCRIPTS, "tts-toggle.py"), "volume", "15"], baseEnv());
    expect(r.exitCode).toBe(2);
    const saved = JSON.parse(readFileSync(ttsConfig, "utf-8"));
    expect(saved.kokoro.volume).toBe(10);
  });
});

describe("tts-gain.py (level to gain, daemon-identical rules)", () => {
  test("integer levels divide by ten for each engine", () => {
    sandbox({ engine: "kokoro", kokoro: { volume: 7 }, piper: { volume: 4 } });
    expect(ttsGain("kokoro").stdout.toString().trim()).toBe("0.70");
    expect(ttsGain("piper").stdout.toString().trim()).toBe("0.40");
  });

  test("legacy float levels map to the nearest level first", () => {
    sandbox({ engine: "kokoro", piper: { volume: 0.8 } });
    expect(ttsGain("piper").stdout.toString().trim()).toBe("0.80");
  });

  test("missing config falls back to engine defaults", () => {
    dir = mkdtempSync(join(tmpdir(), "voice-scripts-"));
    WORK.push(dir);
    ttsConfig = join(dir, "absent.json");
    expect(ttsGain("piper").stdout.toString().trim()).toBe("0.80");
    expect(ttsGain("kokoro").stdout.toString().trim()).toBe("1.00");
  });

  test("'current' resolves through the configured engine", () => {
    sandbox({ engine: "piper", kokoro: { volume: 10 }, piper: { volume: 6 } });
    expect(ttsGain("current").stdout.toString().trim()).toBe("0.60");
  });
});

describe("speak.sh engine and volume routing", () => {
  test("piper engine passes the configured gain via --volume", () => {
    const { fakePython } = sandbox({
      engine: "piper",
      piper: { voice: "en_GB-jenny_dioco-medium", volume: 4 },
    });
    const r = spawnProc(
      ["bash", join(SCRIPTS, "speak.sh"), "hello world", "", ""],
      baseEnv({
        GROK_TTS_ENGINE: "piper",
        GROK_TTS_PIPER_PYTHON: fakePython,
        GROK_TTS_PIPER_SPEAK: join(dir, "piper.py"), // never read; argv only
        PIPER_LOG: piperLog,
      }),
    );
    expect(r.exitCode).toBe(0);
    const lines = piperArgs();
    expect(lines).toHaveLength(1);
    const argv = lines[0].split(/\s+/);
    expect(argv).toContain("--volume");
    expect(argv[argv.indexOf("--volume") + 1]).toBe("0.40");
    expect(argv).toContain("--voice");
    expect(argv[argv.indexOf("--voice") + 1]).toBe("en_GB-jenny_dioco-medium");
    expect(lines[0]).not.toContain("tts-daemon");
  });

  test("legacy stored volume 0.8 maps to --volume 0.80", () => {
    const { fakePython } = sandbox({
      engine: "piper",
      piper: { volume: 0.8 },
    });
    spawnProc(
      ["bash", join(SCRIPTS, "speak.sh"), "hi", "", ""],
      baseEnv({
        GROK_TTS_ENGINE: "piper",
        GROK_TTS_PIPER_PYTHON: fakePython,
        GROK_TTS_PIPER_SPEAK: join(dir, "piper.py"),
        PIPER_LOG: piperLog,
      }),
    );
    const argv = piperArgs()[0].split(/\s+/);
    expect(argv[argv.indexOf("--volume") + 1]).toBe("0.80");
  });

  test("kokoro engine does not invoke the piper CLI", () => {
    const { fakePython } = sandbox({
      engine: "kokoro",
      kokoro: { device: "gpu", volume: 10 },
    });
    const r = spawnProc(
      ["bash", join(SCRIPTS, "speak.sh"), "hi", "", ""],
      baseEnv({
        // Point the daemon and direct fallback at missing files so the run
        // ends quickly with "No TTS available" instead of spawning models.
        GROK_TTS_DAEMON_SCRIPT: join(dir, "missing-daemon.py"),
        GROK_TTS_DIRECT_TTS: join(dir, "missing-direct.py"),
        GROK_TTS_PIPER_PYTHON: fakePython,
        GROK_TTS_PIPER_SPEAK: join(dir, "piper.py"),
        PIPER_LOG: piperLog,
      }),
    );
    expect(r.exitCode).toBe(1);
    expect(r.stderr.toString()).toContain("No TTS available");
    expect(piperArgs()).toHaveLength(0);
  });

  test("an unknown piper voice falls back to the configured piper voice", () => {
    const { fakePython } = sandbox({
      engine: "piper",
      piper: { voice: "en_GB-jenny_dioco-medium", volume: 4 },
    });
    spawnProc(
      ["bash", join(SCRIPTS, "speak.sh"), "hi", "af_heart", ""],
      baseEnv({
        GROK_TTS_ENGINE: "piper",
        GROK_TTS_PIPER_PYTHON: fakePython,
        GROK_TTS_PIPER_SPEAK: join(dir, "piper.py"),
        GROK_TTS_PIPER_VOICES: join(dir, "voices"),
        PIPER_LOG: piperLog,
      }),
    );
    const argv = piperArgs()[0].split(/\s+/);
    expect(argv[argv.indexOf("--voice") + 1]).toBe("en_GB-jenny_dioco-medium");
  });

  describe("per-session piper log, scrubbing, and voice pairing", () => {
    /** Speak with a dedicated session log (GROK_TTS_PIPER_LOG) so its header
     * + scrubbed text stay separate from the argv stream in PIPER_LOG. */
    function speak(sessionLog: string, text: string, voice: string, extra = {}) {
      return spawnProc(
        ["bash", join(SCRIPTS, "speak.sh"), text, voice, ""],
        baseEnv({
          GROK_TTS_ENGINE: "piper",
          GROK_TTS_PIPER_PYTHON: fakePython,
          GROK_TTS_PIPER_SPEAK: join(dir, "piper.py"),
          GROK_TTS_PIPER_VOICES: join(dir, "voices"),
          GROK_TTS_PIPER_LOG: sessionLog,
          PIPER_LOG: piperLog,
          PI_SESSION_ID: "test-session",
          ...extra,
        }),
      );
    }

    test("logs a session header and the scrubbed text before exec", () => {
      sandbox({ engine: "piper", piper: { voice: "en_GB-jenny_dioco-medium", volume: 8 } });
      const sessionLog = join(dir, "pair-a.log");
      speak(sessionLog, "Run 2**8 iterations", "");
      const log = readFileSync(sessionLog, "utf-8");
      expect(log).toContain("voice=en_GB-jenny_dioco-medium");
      expect(log).toContain("volume=0.80");
      // sanitizer evaluated the power before the header was written
      expect(log).toContain("Run 256 iterations");
      expect(log).not.toContain("2**8");
      expect(piperArgs()).toHaveLength(1); // piper still exec'd after logging
    });

    test("guard rewrites reach the log (comparisons, paths, hashes)", () => {
      sandbox({ engine: "piper", piper: { voice: "en_GB-jenny_dioco-medium", volume: 8 } });
      const sessionLog = join(dir, "pair-b.log");
      // Paths: sanitize slabs to the filename (src/lib/main.py -> main.py);
      // the guard then speaks surviving slash tokens as words (24/7), and
      // drops the hex hash.
      speak(sessionLog, "code != 0, src/lib/main.py changed, uptime 24/7, commit abc123def", "");
      const log = readFileSync(sessionLog, "utf-8");
      expect(log).toContain("is not equal to");
      expect(log).toContain("main.py");
      expect(log).toContain("24 slash 7");
      expect(log).not.toContain("abc123def"); // hex-hash token dropped
      expect(piperArgs()).toHaveLength(1);
    });

    test("unpaired .onnx (no .onnx.json) falls back and logs WARN", () => {
      sandbox({ engine: "piper", piper: { voice: "en_GB-jenny_dioco-medium", volume: 4 } });
      mkdirSync(join(dir, "voices"), { recursive: true });
      writeFileSync(join(dir, "voices", "en_US-amy-medium.onnx"), "");
      const sessionLog = join(dir, "pair-c.log");
      speak(sessionLog, "hi", "en_US-amy-medium");
      const argv = piperArgs()[0].split(/\s+/);
      expect(argv[argv.indexOf("--voice") + 1]).toBe("en_GB-jenny_dioco-medium");
      const log = readFileSync(sessionLog, "utf-8");
      expect(log).toContain("WARN unpaired piper voice: en_US-amy-medium");
      expect(log).toContain("fallback en_GB-jenny_dioco-medium");
    });

    test("paired voice model is selected without warning", () => {
      sandbox({ engine: "piper", piper: { voice: "en_GB-jenny_dioco-medium", volume: 4 } });
      mkdirSync(join(dir, "voices"), { recursive: true });
      writeFileSync(join(dir, "voices", "en_US-amy-medium.onnx"), "");
      writeFileSync(join(dir, "voices", "en_US-amy-medium.onnx.json"), "{}");
      const sessionLog = join(dir, "pair-d.log");
      speak(sessionLog, "hi", "en_US-amy-medium");
      const argv = piperArgs()[0].split(/\s+/);
      expect(argv[argv.indexOf("--voice") + 1]).toBe("en_US-amy-medium");
      expect(readFileSync(sessionLog, "utf-8")).toContain("voice=en_US-amy-medium");
      expect(readFileSync(sessionLog, "utf-8")).not.toContain("WARN");
    });
  });
});

describe("set-voice.sh per-session voice selection", () => {
  test("accepts an installed piper voice so pi can differ from grok", () => {
    const { fakePython, dir: d, ttsConfig: _unused } = sandbox({
      engine: "piper",
      piper: { voice: "en_GB-jenny_dioco-medium", volume: 4 },
    });
    void _unused;
    const voices = join(d, "voices");
    mkdirSync(voices, { recursive: true });
    writeFileSync(join(voices, "en_US-amy-medium.onnx"), "");
    const skillConfig = join(d, "skill-config.json");
    // Fake piper python replaces the interpreter; speak.sh stays silent by
    // pointing the daemon/direct fallbacks at missing files.
    const r = spawnProc(
      ["bash", join(SCRIPTS, "set-voice.sh"), "en_US-amy-medium", "1.3"],
      baseEnv({
        GROK_TTS_PIPER_PYTHON: fakePython,
        GROK_TTS_PIPER_SPEAK: join(d, "piper.py"),
        GROK_TTS_PIPER_VOICES: voices,
        GROK_TTS_ENGINE: "piper",
        VOICE_SKILL_CONFIG: skillConfig,
        PIPER_LOG: piperLog,
      }),
    );
    expect(r.exitCode).toBe(0);
    expect(r.stdout.toString()).toContain("en_US-amy-medium");
    const cfg = JSON.parse(readFileSync(skillConfig, "utf-8"));
    expect(cfg.activeVoice).toBe("en_US-amy-medium");
    expect(cfg.activeSpeed).toBe(1.3);
  });

  test("still rejects unknown kokoro/piper voice names", () => {
    sandbox({ engine: "piper" });
    const r = spawnProc(
      ["bash", join(SCRIPTS, "set-voice.sh"), "not_a_voice"],
      baseEnv({ GROK_TTS_DAEMON_SCRIPT: join(dir, "x"), GROK_TTS_DIRECT_TTS: join(dir, "y") }),
    );
    expect(r.exitCode).toBe(1);
    expect(r.stdout.toString()).toContain("Invalid voice");
  });
});