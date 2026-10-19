import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { spawn, execSync } from "node:child_process";
import { existsSync, unlinkSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * Voice Control Extension - Infrastructure for /vd
 * 
 * Use /vd for voice dialog interface
 */

/**
 * Settings store. Overridable for tests (PI_VOICE_SETTINGS); otherwise the
 * shared ~/.pi/voice-settings.json is used across sessions.
 */
const SETTINGS_FILE =
  process.env.PI_VOICE_SETTINGS || "~/.pi/voice-settings.json";
const LEGACY_SETTINGS_FILE = "~/.pi/voice-control-settings.json";
const CONTROL_LOG_FILE = "/tmp/voice-control.log";

// File logger for debugging
function controlLog(msg: string) {
  try {
    const timestamp = new Date().toISOString();
    const fs = require('node:fs');
    fs.appendFileSync(CONTROL_LOG_FILE, `[${timestamp}] ${msg}\n`);
  } catch {}
}

// --- Unified Voice Settings Interface ---

interface VoiceSettings {
  version: number;
  lastUpdated: string;
  transcription: TranscriptionSettings;
  response: ResponseSettings;
  dialog: DialogSettings;
  daemon: DaemonSettings;
}

interface TranscriptionSettings {
  engine: "whisper" | "parakeet";
  model: WhisperModelSettings;
  audio: AudioInputSettings;
  vad: VADSettings;
}

interface WhisperModelSettings {
  size: "tiny" | "base" | "small" | "medium";
  device: "cuda" | "cpu";
  computeType: "float16" | "int8";
  beamSize: number;
  bestOf: number;
  temperature: number;
  conditionOnPreviousText: boolean;
  noRepeatNgramSize: number;
}

interface AudioDeviceInfo {
  name: string | null;
  index: number | null;
  hwId: string | null;
  pulseId: string | null;
}

interface AudioInputSettings {
  micDevice: AudioDeviceInfo;
  sampleRate: number;
  channels: number;
  format: "int16";
}

interface VADSettings {
  enabled: boolean;
  mode: 0 | 1 | 2 | 3;
  pauseSeconds: number;
  frameDurationMs: number;
  timeoutSeconds: number;
}

interface ResponseSettings {
  voice: VoiceOutputSettings;
  audio: AudioOutputSettings;
  behavior: TTSBehaviorSettings;
}

interface VoiceOutputSettings {
  default: string;
  active: string;
  speed: number;
  language: string;
}

interface AudioOutputSettings {
  speakerDevice: AudioDeviceInfo;
  sampleRate: number;
  format: "wav" | "mp3";
}

interface TTSBehaviorSettings {
  autoSpeak: boolean;
  speakErrors: boolean;
  maxTextLength: number;
}

interface DialogSettings {
  ui: DialogUISettings;
  shortcuts: DialogShortcuts;
}

interface DialogUISettings {
  width: number;
  renderThrottleMs: number;
  transDelay: number;
  showAudioMeter: boolean;
  showCountdown: boolean;
  confirmBeforeExecute: boolean;
}

interface DialogShortcuts {
  record: string;
  removeLast: string;
  clearAll: string;
  execute: string;
  cancel: string;
}

interface DaemonSettings {
  whisper: DaemonConfig;
  tts: DaemonConfig;
}

interface DaemonConfig {
  autoStart: boolean;
  keepAlive: boolean;
  socketPath: string;
  pidPath: string;
}

// Runtime state (not persisted)

// --- Settings Management ---

const DEFAULT_SETTINGS: VoiceSettings = {
  version: 1,
  lastUpdated: new Date().toISOString(),
  transcription: {
    engine: "whisper",
    model: {
      size: "base",
      device: "cuda",
      computeType: "float16",
      beamSize: 5,
      bestOf: 5,
      temperature: 0.0,
      conditionOnPreviousText: true,
      noRepeatNgramSize: 0
    },
    audio: {
      micDevice: { name: null, index: null, hwId: null, pulseId: null },
      sampleRate: 16000,
      channels: 1,
      format: "int16"
    },
    vad: {
      enabled: true,
      mode: 2,
      pauseSeconds: 5.0,
      frameDurationMs: 30,
      timeoutSeconds: 120
    }
  },
  response: {
    voice: {
      default: "am_michael",
      active: "af_heart",
      speed: 1.5,
      language: "en"
    },
    audio: {
      speakerDevice: { name: null, index: null, hwId: null, pulseId: null },
      sampleRate: 24000,
      format: "wav"
    },
    behavior: {
      autoSpeak: true,
      speakErrors: false,
      maxTextLength: 500
    }
  },
  dialog: {
    ui: {
      width: 76,
      renderThrottleMs: 100,
      transDelay: 5,
      showAudioMeter: true,
      showCountdown: true,
      confirmBeforeExecute: false
    },
    shortcuts: {
      record: "SPACE",
      removeLast: "-",
      clearAll: "--",
      execute: "ENTER",
      cancel: "ESCAPE"
    }
  },
  daemon: {
    whisper: {
      autoStart: true,
      keepAlive: true,
      socketPath: "/tmp/pi-whisper.sock",
      pidPath: "/tmp/pi-whisper.pid"
    },
    tts: {
      autoStart: true,
      keepAlive: true,
      socketPath: "/tmp/kokoro-tts-daemon.sock",
      pidPath: "/tmp/kokoro-tts-daemon.pid"
    }
  }
};

function loadSettings(): VoiceSettings {
  const home = process.env.HOME || "~";
  
  // Try new unified settings first
  try {
    const path = SETTINGS_FILE.replace("~", home);
    if (existsSync(path)) {
      const data = JSON.parse(readFileSync(path, "utf-8"));
      // Validate version and merge with defaults
      if (data.version >= 1) {
        return mergeSettings(DEFAULT_SETTINGS, data);
      }
    }
  } catch {}
  
  // Try legacy migration
  try {
    const legacyPath = LEGACY_SETTINGS_FILE.replace("~", home);
    if (existsSync(legacyPath)) {
      const legacy = JSON.parse(readFileSync(legacyPath, "utf-8"));
      const migrated = migrateFromLegacy(legacy);
      saveSettings(migrated);
      return migrated;
    }
  } catch {}
  
  return DEFAULT_SETTINGS;
}

function migrateFromLegacy(legacy: any): VoiceSettings {
  const settings = JSON.parse(JSON.stringify(DEFAULT_SETTINGS));
  if (legacy.modelSize) {
    settings.transcription.model.size = legacy.modelSize;
  }
  if (legacy.selectedDeviceName !== undefined) {
    settings.transcription.audio.micDevice = legacy.selectedDeviceName;
  }
  settings.lastUpdated = new Date().toISOString();
  return settings;
}

function mergeSettings(defaults: VoiceSettings, user: any): VoiceSettings {
  return {
    ...defaults,
    ...user,
    transcription: {
      engine: user.transcription?.engine ?? defaults.transcription.engine,
      model: { ...defaults.transcription.model, ...user.transcription?.model },
      audio: { ...defaults.transcription.audio, ...user.transcription?.audio },
      vad: { ...defaults.transcription.vad, ...user.transcription?.vad }
    },
    response: {
      voice: { ...defaults.response.voice, ...user.response?.voice },
      audio: { ...defaults.response.audio, ...user.response?.audio },
      behavior: { ...defaults.response.behavior, ...user.response?.behavior }
    },
    dialog: {
      ui: { ...defaults.dialog.ui, ...user.dialog?.ui },
      shortcuts: { ...defaults.dialog.shortcuts, ...user.dialog?.shortcuts }
    },
    daemon: {
      whisper: { ...defaults.daemon.whisper, ...user.daemon?.whisper },
      tts: { ...defaults.daemon.tts, ...user.daemon?.tts }
    }
  };
}

function saveSettings(settings: VoiceSettings) {
  try {
    const home = process.env.HOME || "~";
    const path = SETTINGS_FILE.replace("~", home);
    const toSave = {
      ...settings,
      lastUpdated: new Date().toISOString()
    };
    writeFileSync(path, JSON.stringify(toSave, null, 2));
    console.log(`[voice-control] Saved settings to ${path}, micDevice: ${JSON.stringify(toSave.transcription.audio.micDevice)}`);
    controlLog(`Saved settings - micDevice: ${JSON.stringify(toSave.transcription.audio.micDevice)}`);
    // Log stack trace to find who's calling save with nulls
    const stack = new Error().stack?.split('\n').slice(2, 5).join(' | ');
    controlLog(`Save called from: ${stack}`);
  } catch (e) {
    console.error(`[voice-control] Failed to save settings: ${e}`);
    controlLog(`ERROR saving settings: ${e}`);
  }
}

function updateSpeakerDevice(current: any, deviceInfo: { name: string | null; index: number | null; hwId: string | null; pulseId: string | null }): any {
  const updated = JSON.parse(JSON.stringify(current));
  updated.response.audio.speakerDevice = deviceInfo;
  return updated;
}

// Helper to get TTS settings for voice-response skill
export function getTTSConfig(): ResponseSettings {
  const settings = loadSettings();
  return settings.response;
}

export default function (pi: ExtensionAPI) {

  // TEST COMMAND - diagnose if commands work at all
  pi.registerCommand("vtest", {
    description: "Test command for voice control",
    handler: async (_args, ctx) => {
      console.log("[VTEST] Handler called!");
      ctx.ui.notify("VTEST WORKS - handler is being called", "success");
    }
  });

  const TRANSCRIBE_SCRIPT = "~/.pi/agent/scripts/voice-control/whisper-fast.sh";
  const WHISPER_DAEMON = "~/.pi/agent/scripts/voice-control/whisper-daemon.py";
  const WHISPER_CLIENT = "~/.pi/agent/scripts/voice-control/whisper-client.py";
  const PARAKEET_DAEMON = "~/.pi/agent/scripts/voice-control/parakeet-daemon.py";
  const PARAKEET_CLIENT = "~/.pi/agent/scripts/voice-control/parakeet-client.py";
  const PARAKEET_PID_FILE = "/tmp/pi-parakeet.pid";
  const PARAKEET_SOCKET_FILE = "/tmp/pi-parakeet.sock";
  const VAD_SCRIPT = "~/.pi/agent/scripts/voice-control/record_with_vad.py";
  const UV_PYTHON = "~/.pi/.venv/bin/python3";
  const RECORDING_TIMEOUT = 120000; // 120 seconds max
  
  // Load persisted settings
  const settings = loadSettings();
  
  let isRecording = false;
  let modelSize = settings.transcription.model.size;
  let micDevice = settings.transcription.audio.micDevice;
  let speakerDevice = settings.response.audio.speakerDevice;

  function checkDependencies(): { ok: boolean; missing: string[] } {
    const missing: string[] = [];
    const home = process.env.HOME || "~";
    
    const vadPath = VAD_SCRIPT.replace("~", home);
    if (!existsSync(vadPath)) {
      missing.push("record_with_vad.py");
    }
    
    // Check for uv Python environment
    const uvPython = UV_PYTHON.replace("~", home);
    if (!existsSync(uvPython)) {
      missing.push("uv environment at ~/.pi/.venv");
    }
    
    // Engine-specific checks
    const engine = settings.transcription.engine || "whisper";
    if (engine === "whisper") {
      const whisperDaemon = WHISPER_DAEMON.replace("~", home);
      if (!existsSync(whisperDaemon)) {
        missing.push("whisper-daemon.py");
      }
      
      // Check for faster-whisper in uv environment
      try {
        execSync(`${uvPython} -c 'from faster_whisper import WhisperModel'`, { stdio: "ignore" });
      } catch {
        missing.push("faster-whisper (in uv env)");
      }
    } else {
      // Parakeet (CPU) engine checks
      const parakeetDaemon = PARAKEET_DAEMON.replace("~", home);
      if (!existsSync(parakeetDaemon)) {
        missing.push("parakeet-daemon.py");
      }
      const parakeetClient = PARAKEET_CLIENT.replace("~", home);
      if (!existsSync(parakeetClient)) {
        missing.push("parakeet-client.py");
      }
      
      // Check for moondream (Photon runtime for parakeet-redux) in uv environment
      try {
        execSync(`${uvPython} -c 'import moondream'`, { stdio: "ignore" });
      } catch {
        missing.push("moondream (in uv env) - run: uv pip install moondream");
      }
    }
    
    // Check for pyaudio and webrtcvad in uv environment
    try {
      execSync(`${uvPython} -c 'import pyaudio, webrtcvad'`, { stdio: "ignore" });
    } catch {
      missing.push("pyaudio and webrtcvad-wheels (in uv env)");
    }
    
    return { ok: missing.length === 0, missing };
  }

  /**
   * Check if whisper daemon is running
   */
  function isWhisperDaemonRunning(): boolean {
    const home = process.env.HOME || "~";
    const pidFile = "/tmp/pi-whisper.pid";
    const socketFile = "/tmp/pi-whisper.sock";
    
    if (!existsSync(pidFile) || !existsSync(socketFile)) {
      return false;
    }
    
    try {
      const pid = parseInt(readFileSync(pidFile, "utf-8").trim());
      // Check if process exists
      execSync(`kill -0 ${pid}`, { stdio: "ignore" });
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Start whisper daemon
   */
  function startWhisperDaemon(): boolean {
    const home = process.env.HOME || "~";
    const uvPython = UV_PYTHON.replace("~", home);
    const daemonPath = WHISPER_DAEMON.replace("~", home);
    const pidFile = "/tmp/pi-whisper.pid";
    const socketFile = "/tmp/pi-whisper.sock";
    
    // Check if already running
    if (isWhisperDaemonRunning()) {
      return true;
    }
    
    // Clean up stale files
    try {
      if (existsSync(pidFile)) unlinkSync(pidFile);
      if (existsSync(socketFile)) unlinkSync(socketFile);
    } catch {}
    
    // Start daemon in background
    try {
      const proc = spawn(uvPython, [daemonPath], {
        detached: true,
        stdio: "ignore"
      });
      proc.unref();
      
      // Wait for daemon to be ready (up to 30 seconds)
      for (let i = 0; i < 30; i++) {
        if (existsSync(socketFile)) {
          return true;
        }
        execSync("sleep 1");
      }
      return false;
    } catch {
      return false;
    }
  }

  /**
   * Stop whisper daemon
   */
  function stopWhisperDaemon(): void {
    const pidFile = "/tmp/pi-whisper.pid";
    try {
      if (existsSync(pidFile)) {
        const pid = parseInt(readFileSync(pidFile, "utf-8").trim());
        try {
          execSync(`kill -0 ${pid}`, { stdio: "ignore" });
          execSync(`kill ${pid}`, { stdio: "ignore" });
        } catch {}
      }
    } catch {}
    // Clean up files
    try {
      if (existsSync(pidFile)) unlinkSync(pidFile);
      if (existsSync("/tmp/pi-whisper.sock")) unlinkSync("/tmp/pi-whisper.sock");
    } catch {}
  }

  /**
   * Check if parakeet daemon is running
   */
  function isParakeetDaemonRunning(): boolean {
    if (!existsSync(PARAKEET_PID_FILE) || !existsSync(PARAKEET_SOCKET_FILE)) {
      return false;
    }
    
    try {
      const pid = parseInt(readFileSync(PARAKEET_PID_FILE, "utf-8").trim());
      // Check if process exists
      execSync(`kill -0 ${pid}`, { stdio: "ignore" });
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Start parakeet daemon (CPU - no GPU memory needed)
   */
  function startParakeetDaemon(): boolean {
    const home = process.env.HOME || "~";
    const uvPython = UV_PYTHON.replace("~", home);
    const daemonPath = PARAKEET_DAEMON.replace("~", home);
    
    // Check if already running
    if (isParakeetDaemonRunning()) {
      return true;
    }
    
    // Clean up stale files
    try {
      if (existsSync(PARAKEET_PID_FILE)) unlinkSync(PARAKEET_PID_FILE);
      if (existsSync(PARAKEET_SOCKET_FILE)) unlinkSync(PARAKEET_SOCKET_FILE);
    } catch {}
    
    // Start daemon in background (wait longer - may download 178 MB of weights on first ever run)
    try {
      const proc = spawn(uvPython, [daemonPath], {
        detached: true,
        stdio: "ignore"
      });
      proc.unref();
      
      // Wait for daemon to be ready (up to 60 seconds)
      for (let i = 0; i < 60; i++) {
        if (existsSync(PARAKEET_SOCKET_FILE)) {
          return true;
        }
        execSync("sleep 1");
      }
      return false;
    } catch {
      return false;
    }
  }

  /**
   * Stop parakeet daemon
   */
  function stopParakeetDaemon(): void {
    try {
      if (existsSync(PARAKEET_PID_FILE)) {
        const pid = parseInt(readFileSync(PARAKEET_PID_FILE, "utf-8").trim());
        try {
          execSync(`kill -0 ${pid}`, { stdio: "ignore" });
          execSync(`kill ${pid}`, { stdio: "ignore" });
        } catch {}
      }
    } catch {}
    // Clean up files
    try {
      if (existsSync(PARAKEET_PID_FILE)) unlinkSync(PARAKEET_PID_FILE);
      if (existsSync(PARAKEET_SOCKET_FILE)) unlinkSync(PARAKEET_SOCKET_FILE);
    } catch {}
  }

  /**
   * Check if TTS daemon is running
   */
  function isTTSDaemonRunning(): boolean {
    const pidFile = "/tmp/kokoro-tts-daemon.pid";
    const socketFile = "/tmp/kokoro-tts-daemon.sock";
    
    if (!existsSync(pidFile) || !existsSync(socketFile)) {
      return false;
    }
    
    try {
      const pid = parseInt(readFileSync(pidFile, "utf-8").trim());
      execSync(`kill -0 ${pid}`, { stdio: "ignore" });
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Start TTS daemon
   */
  function startTTSDaemon(): boolean {
    const home = process.env.HOME || "~";
    const uvPython = UV_PYTHON.replace("~", home);
    const daemonPath = "~/.pi/agent/skills/voice-response/scripts/tts-daemon.py".replace("~", home);
    const pidFile = "/tmp/kokoro-tts-daemon.pid";
    const socketFile = "/tmp/kokoro-tts-daemon.sock";
    
    if (isTTSDaemonRunning()) {
      return true;
    }
    
    try {
      if (existsSync(pidFile)) unlinkSync(pidFile);
      if (existsSync(socketFile)) unlinkSync(socketFile);
    } catch {}
    
    try {
      const proc = spawn(uvPython, [daemonPath], {
        detached: true,
        stdio: "ignore"
      });
      proc.unref();
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Stop TTS daemon
   */
  function stopTTSDaemon(): void {
    const pidFile = "/tmp/kokoro-tts-daemon.pid";
    try {
      if (existsSync(pidFile)) {
        const pid = parseInt(readFileSync(pidFile, "utf-8").trim());
        try {
          execSync(`kill -0 ${pid}`, { stdio: "ignore" });
          execSync(`kill ${pid}`, { stdio: "ignore" });
        } catch {}
      }
    } catch {}
    try {
      if (existsSync(pidFile)) unlinkSync(pidFile);
      if (existsSync("/tmp/kokoro-tts-daemon.sock")) unlinkSync("/tmp/kokoro-tts-daemon.sock");
    } catch {}
  }

  /**
   * Engine state (engine, device, per-engine volume) lives in Grok TTS's
   * config (~/.grok/tts/config.json), the same store the Kokoro daemon and
   * Piper CLI apply at synthesis time. The extension reads it for display
   * and delegates changes to the `tts-toggle.py` wrapper so there is a
   * single source of truth.
   */
  function grokConfig(): Record<string, any> | null {
    const home = process.env.HOME || "~";
    const path = process.env.GROK_TTS_CONFIG
      ? process.env.GROK_TTS_CONFIG
      : `${home}/.grok/tts/config.json`;
    try {
      return JSON.parse(readFileSync(path, "utf-8"));
    } catch {
      return null;
    }
  }
  
  function engineSummary(): string {
    const data = grokConfig();
    const engine = (data && data.engine) || "kokoro";
    const device = ((data && data.kokoro && data.kokoro.device) || "gpu") as string;
    const kokoroVol = ((data && data.kokoro && data.kokoro.volume) || 10) as number;
    const piperVol = ((data && data.piper && data.piper.volume) || 8) as number;
    return `engine=${engine} device=${device} kokoro volume=${kokoroVol}/10 piper volume=${piperVol}/10`;
  }
  
  function runToggle(args: string): string | null {
    const home = process.env.HOME || "~";
    const uvPython = UV_PYTHON.replace("~", home);
    const toggle = process.env.GROK_TTS_TOGGLE
      ? process.env.GROK_TTS_TOGGLE
      : "~/.pi/agent/skills/voice-response/scripts/tts-toggle.py".replace("~", home);
    try {
      return execSync(`${uvPython} ${toggle} ${args}`, {
        encoding: "utf-8",
        timeout: 90000
      }).trim();
    } catch (e: any) {
      return (e?.stdout ? String(e.stdout).trim() : null);
    }
  }
  
  // Command: /vc-device - List and select devices
  pi.registerCommand("vc-device", {
    description: "List or select audio input device (use: /vc-device [index])",
    handler: async (argInput, ctx) => {
      // Handle args as string or array
      const argStr = typeof argInput === 'string' ? argInput : argInput.join(' ');
      const args = argStr ? argStr.split(' ') : [];
      
      const home = process.env.HOME || "~";
      const uvPython = UV_PYTHON.replace("~", home);
      const vadPath = VAD_SCRIPT.replace("~", home);
      
      // Get devices first
      let devices: { index: number; name: string }[] = [];
      try {
        const deviceOutput = execSync(`${uvPython} ${vadPath} --list-devices 2>&1`, {
          encoding: "utf-8",
          timeout: 5000
        });
        
        const lines = deviceOutput.split('\n');
        for (const line of lines) {
          const match = line.match(/^(\d+):\s*(.+)$/);
          if (match) {
            const idx = parseInt(match[1]);
            let fullName = match[2].trim();
            if (fullName === 'pulse' || fullName === 'default') continue;
            
            // Extract hw: info for better identification
            const hwMatch = fullName.match(/\s*\((hw:\d+,\d+)\)\s*$/);
            const hwInfo = hwMatch ? ` [${hwMatch[1]}]` : '';
            
            // Clean name but keep descriptive
            let name = fullName.replace(/\s*\(hw:\d+,\d+\)\s*$/, '');
            name = name.replace(/:\s*-\s*$/, ''); // Remove trailing " -"
            
            devices.push({ index: idx, name: name + hwInfo });
          }
        }
      } catch {
        ctx.ui.notify("Could not list devices", "error");
        return;
      }
      
      if (devices.length === 0) {
        ctx.ui.notify("No microphones found", "warning");
        return;
      }
      
      // If device index provided, select it directly
      if (args[0]) {
        const idx = parseInt(args[0]);
        if (isNaN(idx) && args[0] !== "default") {
          ctx.ui.notify("Usage: /vc-device [index] or /vc-device default", "warning");
          return;
        }
        if (args[0] === "default") {
          // Auto-detect and save the default device
          const defaultDevice = devices.find(d => {
            const cleanD = d.name.replace(/:\s*-\s*$/, '').trim();
            return !cleanD.includes('Dock') && 
                   !cleanD.includes('HDA Intel') &&
                   (cleanD.includes('USB') || cleanD.includes('Generic'));
          }) || devices.find(d => !d.name.includes('Dock')) || devices[0];
          
          settings.transcription.audio.micDevice = { 
            name: defaultDevice.name.replace(/\s*\[hw:\d+,\d+\]\s*$/, ''),
            index: defaultDevice.index,
            hwId: null,
            pulseId: null
          };
          micDevice = settings.transcription.audio.micDevice;
          saveSettings(settings);
          ctx.ui.notify(`🎙️ Using system default: ${defaultDevice.name}`, "success");
        } else {
          if (!device) {
            ctx.ui.notify(`Device ${idx} not found`, "error");
            return;
          }
          // Extract hwId from device name if present
          const hwMatch = device.name.match(/\(hw:(\d+,\d+)\)/);
          settings.transcription.audio.micDevice = { 
            name: device.name.replace(/\s*\(hw:\d+,\d+\)\s*$/, ''),
            index: device.index,
            hwId: hwMatch ? `hw:${hwMatch[1]}` : null,
            pulseId: null
          };
          micDevice = settings.transcription.audio.micDevice;
          saveSettings(settings);
          ctx.ui.notify(`🎙️ Selected microphone: ${device.name}`, "success");
        }
        return;
      }
      
      // Find the system default device (prefer USB audio over built-in)
      let systemDefaultName = "Unknown";
      // First try to find a USB audio device (not Dock, not Intel HDA)
      for (const device of devices) {
        if (!device.name.includes('Dock') && 
            !device.name.includes('HDA Intel') &&
            (device.name.includes('USB') || device.name.includes('Generic'))) {
          systemDefaultName = device.name;
          break;
        }
      }
      // Fallback to first non-Dock device
      if (systemDefaultName === "Unknown") {
        for (const device of devices) {
          if (!device.name.includes('Dock')) {
            systemDefaultName = device.name;
            break;
          }
        }
      }
      if (systemDefaultName === "Unknown" && devices.length > 0) {
        systemDefaultName = devices[0].name;
      }
      
      // Show chooser (no timeout - user can take their time)
      // Mark the currently selected device
      const deviceOptions = devices.map(d => {
        const isSelected = d.name === micDevice?.name || 
                          (micDevice?.name && d.name.includes(micDevice.name.replace(/\s*\(hw:\d+,\d+\)\s*$/, '')));
        return `${d.index}: ${d.name}${isSelected ? ' [SELECTED]' : ''}`;
      });
      
      const defaultOption = `System Default (${systemDefaultName})`;
      const options = [defaultOption, ...deviceOptions];
      const selected = await ctx.ui.select("🎙️ Choose Microphone", options);
      
      if (!selected) {
        return; // User cancelled
      }
      
      if (selected === defaultOption) {
        // Auto-detect and save the default device instead of nulls
        const defaultDevice = devices.find(d => {
          const cleanD = d.name.replace(/:\s*-\s*$/, '').replace(/\s*\[hw:\d+,\d+\]\s*$/, '').trim();
          return !cleanD.includes('Dock') && 
                 !cleanD.includes('HDA Intel') &&
                 (cleanD.includes('USB') || cleanD.includes('Generic'));
        }) || devices.find(d => !d.name.includes('Dock')) || devices[0];
        
        if (defaultDevice) {
          settings.transcription.audio.micDevice = { 
            name: defaultDevice.name.replace(/\s*\[hw:\d+,\d+\]\s*$/, ''),
            index: defaultDevice.index,
            hwId: null,
            pulseId: null
          };
          micDevice = settings.transcription.audio.micDevice;
          saveSettings(settings);
          ctx.ui.notify(`🎙️ Using system default: ${defaultDevice.name}`, "success");
        }
      } else {
        const idx = parseInt(selected.split(':')[0]);
        const device = devices.find(d => d.index === idx);
        if (device) {
          const hwMatch = device.name.match(/\(hw:(\d+,\d+)\)/);
          settings.transcription.audio.micDevice = { 
            name: device.name.replace(/\s*\(hw:\d+,\d+\)\s*$/, ''),
            index: device.index,
            hwId: hwMatch ? `hw:${hwMatch[1]}` : null,
            pulseId: null
          };
          micDevice = settings.transcription.audio.micDevice;
          saveSettings(settings);
          ctx.ui.notify(`🎙️ Selected microphone: ${device.name}`, "success");
        }
      }
    }
  });

  // Command: /voice-settings - Show current settings
  pi.registerCommand("voice-settings", {
    description: "Show current model and input device settings",
    handler: async (_args, ctx) => {
      // Get current device name
      let deviceName = "Unknown";
      try {
        const home = process.env.HOME || "~";
        const uvPython = UV_PYTHON.replace("~", home);
        const vadPath = VAD_SCRIPT.replace("~", home);
        const deviceOutput = execSync(`${uvPython} ${vadPath} --list-devices 2>&1`, {
          encoding: "utf-8",
          timeout: 5000
        });
        
        if (micDevice?.name !== null) {
          // Use the stored device name directly
          deviceName = micDevice.name;
        } else {
          // System default - find first real device
          const lines = deviceOutput.split('\n');
          for (const line of lines) {
            const match = line.match(/^(\d+):\s*(.+)$/);
            if (match) {
              let name = match[2].trim();
              if (name === 'pulse' || name === 'default') continue;
              if (name.includes('Dock')) continue;
              deviceName = name.replace(/\s*\(hw:\d+,\d+\)\s*$/, '');
              break;
            }
          }
          if (deviceName === "Unknown") {
            // Fallback to any device
            for (const line of lines) {
              const match = line.match(/^(\d+):\s*(.+)$/);
              if (match) {
                let name = match[2].trim();
                if (name === 'pulse' || name === 'default') continue;
                deviceName = name.replace(/\s*\(hw:\d+,\d+\)\s*$/, '');
                break;
              }
            }
          }
          deviceName = `${deviceName} (system default)`;
        }
      } catch {}
      
      const info = `
STT Engine: ${settings.transcription.engine || "whisper"}
Model: ${modelSize}${settings.transcription.engine === "parakeet" ? " (whisper model - engine set to parakeet)" : ""}
Input Device: ${deviceName}

Change model:
  /vc-model tiny    - Fastest (~1GB VRAM)
  /vc-model base    - Good speed/accuracy (~1GB VRAM)
  /vc-model small   - Better accuracy (~2GB VRAM)  
  /vc-model medium  - Best accuracy (~5GB VRAM)

Switch engine:
  /vc-engine whisper    - faster-whisper on CUDA GPU
  /vc-engine parakeet   - Parakeet Redux 1.58-bit on CPU (178 MB)

Choose Mic with:
  /vc-device        - List available microphones
      `.trim();
      ctx.ui.notify(info, "info");
    }
  });

  // Command: /vc-model - Set specific model
  pi.registerCommand("vc-model", {
    description: "Set Whisper model (tiny/base/small/medium)",
    handler: async (argInput, ctx) => {
      // Handle args as string or array
      const argStr = typeof argInput === 'string' ? argInput : argInput.join(' ');
      const args = argStr ? argStr.split(' ') : [];
      
      // Show chooser if no model specified
      if (!args[0]) {
        const options = [
          "tiny  - Fastest, ~1GB VRAM",
          "base  - Good speed/accuracy, ~1GB VRAM",
          "small - Better accuracy, ~2GB VRAM", 
          "medium - Best accuracy, ~5GB VRAM"
        ];
        const selected = await ctx.ui.select("🎙️ Choose Whisper Model", options);
        
        if (!selected) {
          return; // User cancelled
        }
        
        const model = selected.split(' ')[0];
        modelSize = model;
        settings.transcription.model.size = model;
        saveSettings(settings);
        ctx.ui.notify(`🎙️ Model set to: ${modelSize}${settings.transcription.engine === "parakeet" ? " (idle - parakeet CPU engine active for /vd)" : ""}`, "success");
        return;
      }
      
      // Direct selection
      const model = args[0]?.toLowerCase();
      if (!model || !["tiny", "base", "small", "medium"].includes(model)) {
        ctx.ui.notify("Usage: /vc-model <tiny|base|small|medium> or just /vc-model to choose", "warning");
        return;
      }
      modelSize = model;
      settings.transcription.model.size = model;
      saveSettings(settings);
      ctx.ui.notify(`🎙️ Model set to: ${modelSize}${settings.transcription.engine === "parakeet" ? " (idle - parakeet CPU engine active for /vd)" : ""}`, "success");
    }
  });

  // Command: /vc-settings - Edit transcription settings
  pi.registerCommand("vc-settings", {
    description: "Show/edit voice transcription settings",
    handler: async (args, ctx) => {
      // Get current values
      const currentMic = settings.transcription.audio.micDevice?.name ?? "System Default";
      const currentEngine = settings.transcription.engine || "whisper";
      const currentModel = settings.transcription.model.size;
      const currentVad = settings.transcription.vad.mode;
      const currentPause = settings.transcription.vad.pauseSeconds;
      const currentWidth = settings.dialog?.ui?.width ?? 76;
      const currentTransDelay = settings.dialog?.ui?.transDelay ?? 5;
      
      if (!args[0]) {
        const help = `
🎙️ Voice Command Settings

Current:
  STT Engine: ${currentEngine}${currentEngine === "parakeet" ? " (Parakeet Redux 1.58-bit, pure CPU)" : " (faster-whisper)"}
  Mic Device: ${currentMic.replace(/\s*\(hw:\d+,\d+\)\s*$/, '')}
  Model: ${currentModel}${currentEngine === "whisper" ? "" : " (idle - parakeet engine active)"}
  VAD Mode: ${currentVad} (0-3, higher = more aggressive)
  Pause Timeout: ${currentPause}s (silence before stopping)
  Dialog Width: ${currentWidth} (affects /vd command)
  Trans Delay: ${currentTransDelay}s (transcription start delay)

Commands:
  /vc-engine whisper|parakeet   - Set STT engine (parakeet = CPU, no GPU)
  /vc-settings mic              - Change microphone
  /vc-settings model            - Change Whisper model
  /vc-settings vad <0-3>      - Set VAD aggressiveness
  /vc-settings pause <seconds>  - Set silence timeout
  /vc-settings throttle <ms>    - Set render throttle (50-1000)
  /vc-settings ui-width <76-132> - Set dialog width
  /vc-settings ui-trans-delay <1-30> - Set transcription delay
        `.trim();
        ctx.ui.notify(help, "info");
        return;
      }
      
      // Handle args as string or array
      const argStr = typeof args === 'string' ? args : args.join(' ');
      const subcmd = argStr.split(' ')[0].toLowerCase();
      
      if (subcmd === "mic") {
        console.log("[vc-settings] MIC HANDLER STARTING");
        controlLog("MIC HANDLER STARTING");
        // Device chooser
        const home = process.env.HOME || "~";
        const uvPython = UV_PYTHON.replace("~", home);
        const vadPath = VAD_SCRIPT.replace("~", home);
        
        console.log(`[vc-settings] uvPython=${uvPython}, vadPath=${vadPath}`);
        controlLog(`uvPython=${uvPython}, vadPath=${vadPath}`);
        
        let devices: { index: number; name: string }[] = [];
        try {
          const output = execSync(`${uvPython} ${vadPath} --list-devices 2>&1`, {
            encoding: "utf-8", timeout: 5000
          });
          
          for (const line of output.split('\n')) {
            const match = line.match(/^(\d+):\s*(.+)$/);
            if (match) {
              const idx = parseInt(match[1]);
              let fullName = match[2].trim();
              if (fullName === 'pulse' || fullName === 'default') continue;
              // Extract hw info and keep it for identification
              const hwMatch = fullName.match(/\s*\((hw:\d+,\d+)\)\s*$/);
              const hwInfo = hwMatch ? ` [${hwMatch[1]}]` : '';
              let name = fullName.replace(/\s*\(hw:\d+,\d+\)\s*$/, '');
              name = name.replace(/:\s*-\s*$/, '');
              devices.push({ index: idx, name: name + hwInfo });
            }
          }
        } catch {
          ctx.ui.notify("Could not list devices", "error");
          return;
        }
        
        // Get the actual device name from PulseAudio
        let pactlName: string | null = null;
        try {
          const pactlOutput = execSync('pactl get-default-source 2>&1', { encoding: 'utf-8' });
          // Parse: alsa_input.usb-Native_Instruments_Komplete_Audio_2_000066BE-00.iec958-stereo
          // Capture the full vendor_product_name (everything between .usb- and -00.)
          const match = pactlOutput.match(/\.usb-([^.]+)-\d+\./);
          if (match) {
            // match[1] = "Native_Instruments_Komplete_Audio_2_000066BE"
            pactlName = match[1].replace(/_/g, ' ');
          }
        } catch {}
        
        // Find which device is currently selected (if any)
        let currentDeviceName = "System Default";
        let currentDeviceIndex = -1;
        let matchedDevice: typeof devices[0] | undefined;
        
        if (micDevice?.name) {
          // Clean micDevice name (remove hw suffix and trailing colon-dash)
          const cleanMicName = micDevice.name
            ?.replace(/\s*[[(]hw:\d+,\d+[[)\]]\s*$/, '')
            ?.replace(/:\s*-\s*$/, '')
            ?.trim() || '';
          
          matchedDevice = devices.find(d => {
            const cleanD = d.name.replace(/\s*[[(]hw:\d+,\d+[[)\]]\s*$/, '').trim();
            return d.name === micDevice?.name || 
                   cleanD === cleanMicName || 
                   cleanMicName?.includes(cleanD) || 
                   cleanD?.includes(cleanMicName);
          });
          
          if (matchedDevice) {
            // Found match - prefer pactl name for USB/Generic devices
            const isUsbGeneric = matchedDevice.name.includes('USB') || matchedDevice.name.includes('Generic');
            currentDeviceName = (isUsbGeneric && pactlName) ? pactlName : cleanMicName;
            currentDeviceIndex = matchedDevice.index;
          } else {
            // micDevice not found - use stored name for any USB/Generic device
            const usbDevice = devices.find(d => {
              const cleanD = d.name.replace(/\s*\[hw:\d+,\d+\]\s*$/, '').trim();
              return (cleanD.includes('USB') || cleanD.includes('Generic')) && !cleanD.includes('Dock');
            });
            
            if (usbDevice) {
              // Prefer pactl name if we can get it for the default source
              if (pactlName) {
                currentDeviceName = pactlName;
              } else {
                currentDeviceName = cleanMicName;
              }
              currentDeviceIndex = usbDevice.index;
            } else {
              currentDeviceName = pactlName || cleanMicName;
              devices.push({ index: -2, name: micDevice?.name || 'Unknown Device' });
            }
          }
        } else {
          // No micDevice set, find the system default
          let defaultDevice = devices.find(d => {
            const cleanD = d.name.replace(/:\s*-\s*$/, '').replace(/\s*\[hw:\d+,\d+\]\s*$/, '').trim();
            return !cleanD.includes('Dock') && 
                   !cleanD.includes('HDA Intel') &&
                   (cleanD.includes('USB') || cleanD.includes('Generic'));
          });
          if (!defaultDevice) {
            defaultDevice = devices.find(d => {
              const cleanD = d.name.replace(/:\s*-\s*$/, '').replace(/\s*\[hw:\d+,\d+\]\s*$/, '').trim();
              return !cleanD.includes('Dock');
            });
          }
          if (defaultDevice) {
            // Use pactl name if available for USB/Generic devices
            if (pactlName && (defaultDevice.name.includes('USB') || defaultDevice.name.includes('Generic'))) {
              currentDeviceName = pactlName;
            } else {
              currentDeviceName = defaultDevice.name
                .replace(/:\s*-\s*$/, '')
                .replace(/\s*\[hw:\d+,\d+\]\s*$/, '')
                .trim();
            }
            currentDeviceIndex = defaultDevice.index;
          }
        }
        
        // Find the best system default device (non-Dock USB/Generic preferred)
        let systemDefaultName = "Unknown";
        let systemDefaultIndex = -1;
        
        // First try: USB/Generic audio device (not Dock)
        for (const device of devices) {
          const cleanName = device.name.replace(/:\s*-\s*$/, '').trim();
          if (!cleanName.includes('Dock') && 
              (cleanName.includes('USB') || cleanName.includes('Generic'))) {
            systemDefaultName = cleanName;
            systemDefaultIndex = device.index;
            break;
          }
        }
        
        // Second try: any non-Dock device
        if (systemDefaultName === "Unknown") {
          for (const device of devices) {
            if (!device.name.includes('Dock')) {
              systemDefaultName = device.name.replace(/:\s*-\s*$/, '');
              systemDefaultIndex = device.index;
              break;
            }
          }
        }
        
        // Fallback: first available device
        if (systemDefaultName === "Unknown" && devices.length > 0) {
          systemDefaultName = devices[0].name.replace(/:\s*-\s*$/, '');
          systemDefaultIndex = devices[0].index;
        }
        
        // Build display options - names only, no indices
        type MicOption = { display: string; value: string | null; index: number };
        const options: MicOption[] = [];
        
        // If user has a device selected, show it as the first option to preserve it
        if (micDevice?.name && micDevice?.index !== null) {
          options.push({ 
            display: `★ Current: ${micDevice.name}`, 
            value: micDevice.name, 
            index: micDevice.index 
          });
        }
        
        // System Default option (auto-detect best device)
        options.push({ 
          display: `⚙️ System Default (${systemDefaultName})`, 
          value: systemDefaultName, 
          index: systemDefaultIndex 
        });
        
        // Add all devices
        devices.forEach(d => {
          // Skip if this is the current device (already shown above)
          if (micDevice?.index === d.index) return;
          
          const cleanD = d.name.replace(/\s*\[hw:\d+,\d+\]\s*$/, '');
          options.push({ 
            display: d.name, 
            value: cleanD, 
            index: d.index 
          });
        });
        
        const displayOptions = options.map(o => o.display);
        console.log(`[vc-settings] Showing ${displayOptions.length} options`);
        controlLog(`Showing ${displayOptions.length} options`);
        
        const selectedDisplay = await ctx.ui.select("🎙️ Choose Microphone", displayOptions);
        console.log(`[vc-settings] Selected: ${selectedDisplay}`);
        controlLog(`Selected: ${selectedDisplay}`);
        
        if (!selectedDisplay) {
          console.log("[vc-settings] No selection - returning");
          controlLog("No selection - returning");
          return;
        }
        
        // Find the selected option by display string
        const selected = options.find(o => o.display === selectedDisplay);
        if (!selected) return;
        
        if (selected.index === -1) {
          // Save the auto-detected default device using systemDefaultName (stored in selected.value)
          const defaultDevice = devices.find(d => {
            const cleanD = d.name.replace(/:\s*-\s*$/, '').trim();
            return cleanD === selected.value;
          });
          
          if (defaultDevice) {
            settings.transcription.audio.micDevice = { 
              name: selected.value,
              index: defaultDevice.index,
              hwId: null,
              pulseId: null
            };
            micDevice = settings.transcription.audio.micDevice;
            saveSettings(settings);
            ctx.ui.notify(`🎙️ Mic: System Default (${selected.value})`, "success");
          }
          return;
        }
        
        // Set to selected device - try to extract hw info from original device name
        const selectedDevice = devices.find(d => d.index === selected.index);
        const hwMatch = selectedDevice?.name.match(/\(hw:(\d+,\d+)\)/);
        console.log(`[vc-settings] Saving device: ${selected.value} (index ${selected.index})`);
        controlLog(`Saving device: ${selected.value} (index ${selected.index})`);
        
        // FIX: Update settings directly instead of using local variable
        settings.transcription.audio.micDevice = { 
          name: selected.value,
          index: selected.index,
          hwId: hwMatch ? `hw:${hwMatch[1]}` : null,
          pulseId: null
        };
        
        // Also update local reference
        micDevice = settings.transcription.audio.micDevice;
        
        console.log(`[vc-settings] Updated settings.micDevice: ${JSON.stringify(settings.transcription.audio.micDevice)}`);
        controlLog(`Updated settings.micDevice: ${JSON.stringify(settings.transcription.audio.micDevice)}`);
        console.log(`[vc-settings] About to save settings...`);
        controlLog(`About to save settings...`);
        saveSettings(settings);
        console.log(`[vc-settings] Save completed!`);
        controlLog(`Save completed!`);
        ctx.ui.notify(`🎙️ Mic: ${micDevice.name}`, "success");
        console.log(`[vc-settings] Notification sent, handler done`);
        controlLog(`Notification sent, handler done`);
        return;
      }
      
      if (subcmd === "model") {
        ctx.ui.notify("Use /vc-model to change Whisper model", "info");
        return;
      }
      
      // Parse remaining args for subcommands that need them
      const remainingArgs = argStr.split(' ').slice(1);
      
      if (subcmd === "vad" && remainingArgs[0]) {
        const mode = parseInt(remainingArgs[0]);
        if (isNaN(mode) || mode < 0 || mode > 3) {
          ctx.ui.notify("VAD mode must be 0-3", "error");
          return;
        }
        settings.transcription.vad.mode = mode as 0 | 1 | 2 | 3;
        saveSettings(settings);
        ctx.ui.notify(`🎙️ VAD mode: ${mode}`, "success");
        return;
      }
      
      if (subcmd === "pause" && remainingArgs[0]) {
        const secs = parseFloat(remainingArgs[0]);
        if (isNaN(secs) || secs < 1 || secs > 10) {
          ctx.ui.notify("Pause must be 1-10 seconds", "error");
          return;
        }
        settings.transcription.vad.pauseSeconds = secs;
        saveSettings(settings);
        ctx.ui.notify(`🎙️ Pause timeout: ${secs}s`, "success");
        return;
      }
      
      // Handle: /vc-settings throttle <ms>
      if (subcmd === "throttle") {
        const ms = remainingArgs[0] ? parseInt(remainingArgs[0]) : null;
        const currentThrottle = settings.dialog?.ui?.renderThrottleMs ?? 100;
        if (ms === null) {
          ctx.ui.notify(`Current render throttle: ${currentThrottle}ms (lower = smoother, higher = less CPU)`, "info");
          return;
        }
        if (isNaN(ms) || ms < 50 || ms > 1000) {
          ctx.ui.notify("Throttle must be 50-1000 ms", "error");
          return;
        }
        settings.dialog.ui.renderThrottleMs = ms;
        saveSettings(settings);
        ctx.ui.notify(`🎙️ Render throttle: ${ms}ms (${ms <= 100 ? 'smooth' : ms <= 250 ? 'balanced' : 'slow terminal'})`, "success");
        return;
      }
      
      // Handle: /vc-settings ui-width <76-132>
      if (subcmd === "ui-width") {
        const width = remainingArgs[0] ? parseInt(remainingArgs[0]) : null;
        const currentWidth = settings.dialog?.ui?.width ?? 76;
        if (width === null) {
          ctx.ui.notify(`Current dialog width: ${currentWidth} (affects /vd command)`, "info");
          return;
        }
        if (isNaN(width) || width < 76 || width > 132) {
          ctx.ui.notify("UI width must be 76-132", "error");
          return;
        }
        settings.dialog.ui.width = width;
        saveSettings(settings);
        ctx.ui.notify(`🎙️ Dialog width: ${width} (effective after /reload)`, "success");
        return;
      }
      
      // Handle: /vc-settings ui-trans-delay <1-30>
      if (subcmd === "ui-trans-delay") {
        const delay = remainingArgs[0] ? parseInt(remainingArgs[0]) : null;
        const currentDelay = settings.dialog?.ui?.transDelay ?? 5;
        if (delay === null) {
          ctx.ui.notify(`Current transcription delay: ${currentDelay}s (delay before transcription starts)`, "info");
          return;
        }
        if (isNaN(delay) || delay < 1 || delay > 30) {
          ctx.ui.notify("Transcription delay must be 1-30 seconds", "error");
          return;
        }
        settings.dialog.ui.transDelay = delay;
        saveSettings(settings);
        ctx.ui.notify(`🎙️ Transcription delay: ${delay}s (effective after /reload)`, "success");
        return;
      }
      
      ctx.ui.notify("Unknown setting. Use /vc-settings for help.", "warning");
    }
  });

  // Command: /vr-device - List and select output/speaker devices
  pi.registerCommand("vr-device", {
    description: "List or select audio output/speaker device",
    handler: async (args, ctx) => {
      const home = process.env.HOME || "~";
      const uvPython = UV_PYTHON.replace("~", home);
      
      // Get output devices
      let devices: { index: number; name: string }[] = [];
      try {
        const output = execSync(`${uvPython} -c "
import pyaudio
p = pyaudio.PyAudio()
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    if info['maxOutputChannels'] > 0:
        print(f'{i}: {info['name']}')
p.terminate()
" 2>&1`, {
          encoding: "utf-8",
          timeout: 5000
        });
        
        const lines = output.split('\n');
        for (const line of lines) {
          const match = line.match(/^(\d+):\s*(.+)$/);
          if (match) {
            const idx = parseInt(match[1]);
            let name = match[2].trim();
            if (name === 'pulse' || name === 'default') continue;
            devices.push({ index: idx, name });
          }
        }
      } catch {
        ctx.ui.notify("Could not list output devices", "error");
        return;
      }
      
      if (devices.length === 0) {
        ctx.ui.notify("No output devices found", "warning");
        return;
      }
      
      // Show current selection
      const currentSpeaker = settings.response.audio.speakerDevice;
      const deviceOptions = devices.map(d => {
        const isSelected = d.name === currentSpeaker?.name;
        return `${d.index}: ${d.name}${isSelected ? ' [SELECTED]' : ''}`;
      });
      
      // Show chooser
      const defaultOption = "System Default";
      const options = [defaultOption, ...deviceOptions];
      const selected = await ctx.ui.select("🔊 Choose Speaker Device", options);
      
      if (!selected) return;
      
      if (selected === defaultOption) {
        const updated = updateSpeakerDevice(settings, null);
        Object.assign(settings, updated);
        saveSettings(settings);
        ctx.ui.notify("🔊 Using system default speaker", "success");
      } else {
        const idx = parseInt(selected.split(':')[0]);
        const device = devices.find(d => d.index === idx);
        if (device) {
          const updated = updateSpeakerDevice(settings, { name: device.name, index: device.index, hwId: null, pulseId: null });
          Object.assign(settings, updated);
          saveSettings(settings);
          ctx.ui.notify(`🔊 Selected speaker: ${device.name}`, "success");
        }
      }
    }
  });

  // Command: /vr-settings - Edit response settings
  pi.registerCommand("vr-settings", {
    description: "Show/edit voice response (TTS) settings",
    handler: async (args, ctx) => {
      // Get current values
      const currentVoice = settings.response.voice.active;
      const currentSpeed = settings.response.voice.speed;
      const spk = settings.response.audio.speakerDevice;
      const currentSpeaker =
        typeof spk === "string" ? spk : spk?.name || "System Default";
      const autoSpeak = settings.response.behavior.autoSpeak ? "On" : "Off";
      
      // Handle args as string or array (same as /vc-settings)
      const argStr = typeof args === 'string' ? args : args.join(' ');
      const argsArr = argStr ? argStr.split(' ') : [];
      
      if (!argsArr[0]) {
        // Display current settings
        const help = `
🔊 Voice Response (TTS) Settings

Current:
  ${engineSummary()}
  Voice: ${currentVoice}
  Speed: ${currentSpeed}x
  Speaker: ${currentSpeaker}
  Auto-Speak: ${autoSpeak}

Commands:
  /vr-settings volume <1-10>   - Volume for the CURRENT engine; each engine persists its own level
  /vr-settings engine <kokoro|piper> - Set TTS engine
  /vr-settings device <gpu|cpu>      - Set compute device (restarts TTS engine)
  /vr-settings voice <name>       - Set voice (af_heart, am_michael)
  /vr-settings speed <0.5-3.0>   - Set speech speed
  /vr-settings speaker            - Choose speaker device
  /vr-settings autospeak <on/off> - Toggle auto-speak
        `.trim();
        ctx.ui.notify(help, "info");
        return;
      }
      
      const subcmd = argsArr[0].toLowerCase();
      
      if (subcmd === "volume") {
        const value = (argsArr[1] || "").trim();
        if (!value) {
          const data = grokConfig();
          const engine = (data && data.engine) || "kokoro";
          const kokoroVol = ((data && data.kokoro && data.kokoro.volume) || 10) as number;
          const piperVol = ((data && data.piper && data.piper.volume) || 8) as number;
          const current = engine === "piper" ? piperVol : kokoroVol;
          ctx.ui.notify(
            `🔊 Volume (current engine ${engine}): ${current}/10. kokoro: ${kokoroVol}/10, piper: ${piperVol}/10.\n` +
            `Scaling: gain = level/10, applied by the running engine (kokoro in-daemon, piper via --volume).\n` +
            `Use: /vr-settings volume <1-10> - sets the current engine only; each engine remembers its own level.`,
            "info"
          );
          return;
        }
        const level = parseInt(value, 10);
        if (`${level}` !== value || isNaN(level) || level < 1 || level > 10) {
          ctx.ui.notify("Volume must be a whole number 1-10", "error");
          return;
        }
        // Delegated: toggle writes the level for the engine currently
        // running (data[engine]["volume"]) and keeps every engine's level.
        const out = runToggle(`volume ${level}`);
        if (out !== null) {
          ctx.ui.notify(`🔊 ${out}`, "success");
        } else {
          ctx.ui.notify("TTS toggle unavailable - could not set volume", "error");
        }
        return;
      }
      
      if (subcmd === "engine") {
        const engine = (argsArr[1] || "").toLowerCase();
        if (!engine) {
          ctx.ui.notify(`🔊 TTS: ${engineSummary()}. Use: /vr-settings engine kokoro|piper`, "info");
          return;
        }
        if (engine !== "kokoro" && engine !== "piper") {
          ctx.ui.notify("Use: /vr-settings engine kokoro|piper", "error");
          return;
        }
        const out = runToggle(`engine ${engine}`);
        if (out !== null) {
          ctx.ui.notify(`🔊 ${out}`, "success");
        } else {
          ctx.ui.notify("TTS toggle unavailable - could not set engine", "error");
        }
        return;
      }
      
      if (subcmd === "device") {
        const device = (argsArr[1] || "").toLowerCase();
        if (!device) {
          ctx.ui.notify(`🔊 TTS: ${engineSummary()}. Use: /vr-settings device gpu|cpu`, "info");
          return;
        }
        if (device !== "gpu" && device !== "cpu") {
          ctx.ui.notify("Use: /vr-settings device gpu|cpu", "error");
          return;
        }
        // Delegated: toggle saves kokoro.device and restarts the daemon on
        // the new device (with rollback if the new device fails to boot).
        const out = runToggle(`device ${device}`);
        if (out !== null) {
          ctx.ui.notify(`🔊 ${out}`, "success");
        } else {
          ctx.ui.notify("TTS toggle unavailable - could not set device", "error");
        }
        return;
      }
      
      if (subcmd === "voice" && argsArr[1]) {
        const voice = argsArr[1];
        settings.response.voice.active = voice;
        saveSettings(settings);
        ctx.ui.notify(`🔊 Voice set to: ${voice}`, "success");
        return;
      }
      
      if (subcmd === "speed" && argsArr[1]) {
        const speed = parseFloat(argsArr[1]);
        if (isNaN(speed) || speed < 0.5 || speed > 3.0) {
          ctx.ui.notify("Speed must be 0.5-3.0", "error");
          return;
        }
        settings.response.voice.speed = speed;
        saveSettings(settings);
        ctx.ui.notify(`🔊 Speed set to: ${speed}x`, "success");
        return;
      }
      
      if (subcmd === "speaker") {
        // Show speaker device chooser - same logic as mic chooser
        const home = process.env.HOME || "~";
        const uvPython = UV_PYTHON.replace("~", home);
        
        let devices: { index: number; name: string }[] = [];
        try {
          const output = execSync(`${uvPython} -c "
import pyaudio
p = pyaudio.PyAudio()
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    if info['maxOutputChannels'] > 0:
        print(f'{i}: {info['name']}')
p.terminate()
" 2>&1`, {
            encoding: "utf-8",
            timeout: 5000
          });
          
          const lines = output.split('\n');
          for (const line of lines) {
            const match = line.match(/^(\d+):\s*(.+)$/);
            if (match) {
              const idx = parseInt(match[1]);
              let name = match[2].trim();
              if (name === 'pulse' || name === 'default') continue;
              devices.push({ index: idx, name });
            }
          }
        } catch {
          ctx.ui.notify("Could not list output devices", "error");
          return;
        }
        
        if (devices.length === 0) {
          ctx.ui.notify("No output devices found", "warning");
          return;
        }
        
        const currentSpk = settings.response.audio.speakerDevice;
        
        // Find which device is currently selected (same logic as mic chooser)
        let currentDeviceName = "System Default";
        let currentDeviceIndex = -1;
        let matchedDevice: typeof devices[0] | undefined;
        
        // Get the actual device name from PulseAudio for output
        let pactlSinkName: string | null = null;
        try {
          const pactlOutput = execSync('pactl get-default-sink 2>&1', { encoding: 'utf-8' });
          // Parse: alsa_output.usb-Native_Instruments_Komplete_Audio_2_000066BE-00.iec958-stereo
          // Capture the full vendor_product_name (everything between .usb- and -00.)
          const match = pactlOutput.match(/\.usb-([^.]+)-\d+\./);
          if (match) {
            pactlSinkName = match[1].replace(/_/g, ' ');
          }
        } catch {}
        
        if (currentSpk) {
          // Clean speaker name (remove hw suffix and trailing colon-dash)
          const cleanSpkName = currentSpk.name
            ?.replace(/\s*[[(]hw:\d+,\d+[[)\]]\s*$/, '')
            ?.replace(/:\s*-\s*$/, '')
            ?.trim() || '';
          
          matchedDevice = devices.find(d => {
            const cleanD = d.name.replace(/\s*[[(]hw:\d+,\d+[[)\]]\s*$/, '').trim();
            return d.name === currentSpk.name || 
                   cleanD === cleanSpkName || 
                   cleanSpkName?.includes(cleanD) || 
                   cleanD?.includes(cleanSpkName);
          });
          
          if (matchedDevice) {
            // Found match - prefer pactl name for USB/Generic devices
            const isUsbGeneric = matchedDevice.name.includes('USB') || matchedDevice.name.includes('Generic');
            currentDeviceName = (isUsbGeneric && pactlSinkName) ? pactlSinkName : cleanSpkName;
            currentDeviceIndex = matchedDevice.index;
          } else {
            // currentSpk not found - assume it's a USB device presenting as "Generic USB"
            const usbDevice = devices.find(d => {
              const cleanD = d.name.replace(/\s*\[hw:\d+,\d+\]\s*$/, '').trim();
              return (cleanD.includes('USB') || cleanD.includes('Generic')) && !cleanD.includes('Dock');
            });
            
            if (usbDevice) {
              // Prefer pactl name if available for the default sink
              if (pactlSinkName) {
                currentDeviceName = pactlSinkName;
              } else {
                currentDeviceName = cleanSpkName;
              }
              currentDeviceIndex = usbDevice.index;
            } else {
              // No USB device found, add as custom entry
              currentDeviceName = pactlSinkName || cleanSpkName;
              devices.push({ index: -2, name: currentSpk.name! });
            }
          }
        } else {
          // No currentSpk set, find system default
          let defaultDevice = devices.find(d => {
            const cleanD = d.name.replace(/:\s*-\s*$/, '').replace(/\s*\[hw:\d+,\d+\]\s*$/, '').trim();
            return !cleanD.includes('Dock') && 
                   !cleanD.includes('HDA Intel') &&
                   (cleanD.includes('USB') || cleanD.includes('Generic'));
          });
          if (!defaultDevice) {
            defaultDevice = devices.find(d => {
              const cleanD = d.name.replace(/:\s*-\s*$/, '').replace(/\s*\[hw:\d+,\d+\]\s*$/, '').trim();
              return !cleanD.includes('Dock');
            });
          }
          if (defaultDevice) {
            // Use pactl name if available for USB/Generic devices
            if (pactlSinkName && (defaultDevice.name.includes('USB') || defaultDevice.name.includes('Generic'))) {
              currentDeviceName = pactlSinkName;
            } else {
              currentDeviceName = defaultDevice.name
                .replace(/:\s*-\s*$/, '')
                .replace(/\s*\[hw:\d+,\d+\]\s*$/, '')
                .trim();
            }
            currentDeviceIndex = defaultDevice.index;
          }
        }
        
        // Build options - show stored/current device as first "Default Speaker" option
        type SpkOption = { display: string; value: { name: string; index: number } | null; index: number };
        const options: SpkOption[] = [];
        
        const isDefault = !currentSpk?.name;
        options.push({ 
          display: `Default Speaker (${currentDeviceName})${isDefault ? '' : ' [CURRENT]'}`, 
          value: null, 
          index: -1 
        });
        
        // Add all devices EXCEPT the one already shown as Default
        devices.forEach(d => {
          if (d.index === currentDeviceIndex) return; // Skip, it's already shown above
          options.push({ 
            display: d.name, 
            value: { name: d.name, index: d.index }, 
            index: d.index 
          });
        });
        
        const displayOptions = options.map(o => o.display);
        const selected = await ctx.ui.select("🔊 Choose Speaker Device", displayOptions);
        
        if (!selected) return;
        
        // Find the selected option by display string
        const selectedOpt = options.find(o => o.display === selected);
        if (!selectedOpt) return;
        
        if (selectedOpt.index === -1) {
          // Default selected - clear speakerDevice
          const updated = updateSpeakerDevice(settings, null);
          Object.assign(settings, updated);
          saveSettings(settings);
          ctx.ui.notify("🔊 Using system default speaker", "success");
        } else {
          // Specific device selected
          const updated = updateSpeakerDevice(settings, { name: selectedOpt.value!.name, index: selectedOpt.value!.index, hwId: null, pulseId: null });
          Object.assign(settings, updated);
          saveSettings(settings);
          ctx.ui.notify(`🔊 Selected speaker: ${selectedOpt.value!.name}`, "success");
        }
        return;
      }
      
      if (subcmd === "autospeak" && argsArr[1]) {
        const val = argsArr[1].toLowerCase();
        if (val !== "on" && val !== "off") {
          ctx.ui.notify("Use: /vr-settings autospeak on|off", "error");
          return;
        }
        settings.response.behavior.autoSpeak = val === "on";
        saveSettings(settings);
        ctx.ui.notify(`🔊 Auto-speak: ${val.toUpperCase()}`, "success");
        return;
      }
      
      ctx.ui.notify("Unknown setting. Use /vr-settings for help.", "warning");
    }
  });

  // Command: /voice-whisper - Manage whisper daemon
  // Command: /vc-engine - Set STT engine for /vd
  pi.registerCommand("vc-engine", {
    description: "Set STT engine (whisper GPU | parakeet CPU)",
    handler: async (argInput, ctx) => {
      // Handle args as string or array
      const argStr = typeof argInput === 'string' ? argInput : argInput.join(' ');
      const args = argStr ? argStr.split(' ') : [];
      
      // Show chooser if no engine specified
      if (!args[0]) {
        const options = [
          `whisper  - faster-whisper, CUDA GPU (currently active: ${settings.transcription.engine === "whisper" ? "YES" : "no"})`,
          `parakeet - Parakeet Redux 1.58-bit, pure CPU 178 MB (currently active: ${settings.transcription.engine === "parakeet" ? "YES" : "no"})`
        ];
        const selected = await ctx.ui.select("🎙️ Choose STT Engine", options);
        
        if (!selected) {
          return; // User cancelled
        }
        
        const engine = selected.split(' ')[0] === "parakeet" ? "parakeet" : "whisper";
        settings.transcription.engine = engine;
        saveSettings(settings);
        ctx.ui.notify(`🎙️ STT engine set to: ${engine} (/vd will use it on next transcription)`, "success");
        return;
      }
      
      // Direct selection
      const engine = args[0]?.toLowerCase();
      if (!engine || !["whisper", "parakeet"].includes(engine)) {
        ctx.ui.notify("Usage: /vc-engine <whisper|parakeet> or just /vc-engine to choose", "warning");
        return;
      }
      settings.transcription.engine = engine as "whisper" | "parakeet";
      saveSettings(settings);
      ctx.ui.notify(`🎙️ STT engine set to: ${engine}`, "success");
    }
  });

  pi.registerCommand("voice-whisper", {
    description: "Manage whisper daemon (start/stop/status)",
    handler: async (args, ctx) => {
      const cmd = args[0]?.toLowerCase() || "status";
      const home = process.env.HOME || "~";
      const uvPython = UV_PYTHON.replace("~", home);
      const daemonPath = WHISPER_DAEMON.replace("~", home);
      
      if (cmd === "status") {
        if (isWhisperDaemonRunning()) {
          ctx.ui.notify("🎙️ Whisper daemon: Running", "success");
        } else {
          ctx.ui.notify("🎙️ Whisper daemon: Not running", "info");
        }
        return;
      }
      
      if (cmd === "start") {
        if (isWhisperDaemonRunning()) {
          ctx.ui.notify("🎙️ Whisper daemon already running", "info");
          return;
        }
        ctx.ui.setStatus("voice-control", "🔄 Starting whisper daemon...");
        if (startWhisperDaemon()) {
          ctx.ui.setStatus("voice-control", undefined);
          ctx.ui.notify("🎙️ Whisper daemon started", "success");
        } else {
          ctx.ui.setStatus("voice-control", undefined);
          ctx.ui.notify("❌ Failed to start whisper daemon", "error");
        }
        return;
      }
      
      if (cmd === "stop") {
        try {
          execSync(`${uvPython} ${daemonPath} stop`, { stdio: "ignore" });
          ctx.ui.notify("🎙️ Whisper daemon stopped", "success");
        } catch {
          ctx.ui.notify("🎙️ Whisper daemon not running", "info");
        }
        return;
      }
      
      ctx.ui.notify("Usage: /voice-whisper [start|stop|status]", "warning");
    }
  });

  // Command: /voice-parakeet - Manage parakeet daemon
  pi.registerCommand("voice-parakeet", {
    description: "Manage parakeet CPU daemon (start/stop/status)",
    handler: async (argInput, ctx) => {
      const argStr = typeof argInput === 'string' ? argInput : argInput.join(' ');
      const cmd = argStr ? argStr.split(' ')[0]?.toLowerCase() || "status" : "status";
      
      if (cmd === "status") {
        if (isParakeetDaemonRunning()) {
          ctx.ui.notify("🦜 Parakeet daemon: Running (CPU)", "success");
        } else {
          ctx.ui.notify("🦜 Parakeet daemon: Not running", "info");
        }
        return;
      }
      
      if (cmd === "start") {
        if (isParakeetDaemonRunning()) {
          ctx.ui.notify("🦜 Parakeet daemon already running", "info");
          return;
        }
        ctx.ui.setStatus("voice-control", "🔄 Starting parakeet daemon (CPU)...");
        if (startParakeetDaemon()) {
          ctx.ui.setStatus("voice-control", undefined);
          ctx.ui.notify("🦜 Parakeet daemon started (CPU)", "success");
        } else {
          ctx.ui.setStatus("voice-control", undefined);
          ctx.ui.notify("❌ Failed to start parakeet daemon", "error");
        }
        return;
      }
      
      if (cmd === "stop") {
        if (!isParakeetDaemonRunning()) {
          ctx.ui.notify("🦜 Parakeet daemon not running", "info");
          return;
        }
        stopParakeetDaemon();
        ctx.ui.notify("🦜 Parakeet daemon stopped", "success");
        return;
      }
      
      ctx.ui.notify("Usage: /voice-parakeet [start|stop|status]", "warning");
    }
  });

  // Command: /voice-unload - Stop all voice daemons
  pi.registerCommand("voice-unload", {
    description: "Unload whisper, parakeet and TTS daemons to free memory",
    handler: async (_args, ctx) => {
      const whisperRunning = isWhisperDaemonRunning();
      const parakeetRunning = isParakeetDaemonRunning();
      const ttsRunning = isTTSDaemonRunning();
      
      if (!whisperRunning && !parakeetRunning && !ttsRunning) {
        ctx.ui.notify("🎙️ Voice daemons already unloaded", "info");
        return;
      }
      
      ctx.ui.setStatus("voice-control", "🔄 Unloading daemons...");
      
      if (whisperRunning) {
        stopWhisperDaemon();
      }
      
      if (parakeetRunning) {
        stopParakeetDaemon();
      }
      
      if (ttsRunning) {
        stopTTSDaemon();
      }
      
      ctx.ui.setStatus("voice-control", undefined);
      
      const stopped: string[] = [];
      if (whisperRunning) stopped.push("  • Whisper STT daemon");
      if (parakeetRunning) stopped.push("  • Parakeet Redux STT daemon (CPU)");
      if (ttsRunning) stopped.push("  • Kokoro TTS daemon");
      
      const msg = `
🎙️ Voice daemons UNLOADED

The following have been STOPPED:
${stopped.join("\n")}

Memory freed (GPU and CPU).
/vd will auto-restart the STT daemon on next use.`.trim();
      
      ctx.ui.notify(msg, "success");
    }
  });

  // Session start
  pi.registerCommand("voice-help", {
    description: "Show voice control help",
    handler: async (_args, ctx) => {
      const whisperStatus = isWhisperDaemonRunning() ? "🟢" : "⚪";
      const parakeetStatus = isParakeetDaemonRunning() ? "🟢" : "⚪";
      const ttsStatus = isTTSDaemonRunning() ? "🟢" : "⚪";
      
      const help = `
🎙️ Voice Control Commands:
  /vd          - Voice dictation (Enter executes, then returns to dictation)
  /vc-device   - Choose microphone
  /vc-model    - Choose Whisper model
  /vc-engine   - Choose STT engine (whisper | parakeet)
  /voice-settings - Show current settings
  /voice-whisper  [start|stop|status] - Manage whisper (GPU) daemon
  /voice-parakeet [start|stop|status] - Manage parakeet (CPU) daemon
  /voice-unload - Stop all daemons (free GPU/CPU memory)
  /vr-settings  - TTS engine/device/voice/speed (/vr-settings engine kokoro|piper, device gpu|cpu)

Daemon Status:
  STT Engine: ${settings.transcription.engine || "whisper"}  (Parakeet Redux = 1.58-bit CPU model via moondream)
  Whisper STT: ${whisperStatus}  Parakeet STT: ${parakeetStatus}  TTS: ${ttsStatus}

Flow: /vd → SPACE → speak → pause → Enter → executes → back in dialog (Esc exits)
      `.trim();
      ctx.ui.notify(help, "info");
    }
  });

  pi.on("session_start", async (_event, ctx) => {
    const deps = checkDependencies();
    if (!deps.ok) {
      ctx.ui.notify(`⚠️ Voice needs: ${deps.missing.join(", ")}`, "warning");
      return;
    }
    
    const engine = settings.transcription.engine || "whisper";
    const whisper = isWhisperDaemonRunning() ? "🟢" : "⚪";
    const parakeet = isParakeetDaemonRunning() ? "🟢" : "⚪";
    const tts = isTTSDaemonRunning() ? "🟢" : "⚪";
    ctx.ui.notify(`🎙️ Voice ready (/voice-help). STT engine: ${engine}. Daemons: whisper ${whisper} parakeet ${parakeet} tts ${tts}`, "info");
  });
}
