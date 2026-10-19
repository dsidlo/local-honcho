/**
 * Voice Control Dialog - TUI overlay for voice commands
 *
 * Fixed: Added tui.requestRender() calls for real-time updates
 */

import type { ExtensionAPI, ExtensionCommandContext, Theme } from "@mariozechner/pi-coding-agent";
import type { Focusable } from "@mariozechner/pi-tui";
import { spawn, execSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { existsSync, unlinkSync, readFileSync, writeFileSync } from "node:fs";

const SETTINGS_FILE = "~/.pi/voice-settings.json";
const WHISPER_CLIENT = "~/.pi/agent/scripts/voice-control/whisper-client.py";
const PARAKEET_CLIENT = "~/.pi/agent/scripts/voice-control/parakeet-client.py";
const PARAKEET_DAEMON = "~/.pi/agent/scripts/voice-control/parakeet-daemon.py";
const PARAKEET_SOCKET = "/tmp/pi-parakeet.sock";
const VAD_SCRIPT = "~/.pi/agent/scripts/voice-control/record_with_vad.py";
const UV_PYTHON = "~/.pi/.venv/bin/python3";
const LOG_FILE = "/tmp/voice-dialog.log";

// Simple file logger
function log(msg: string) {
  try {
    const fs = require('fs');
    const timestamp = new Date().toISOString();
    fs.appendFileSync(LOG_FILE, `[${timestamp}] ${msg}\n`);
  } catch {}
}

interface VoiceSettings {
  version?: number;
  transcription: {
    engine: string;
    model: { size: string };
    audio: { 
      micDevice: { name: string | null; index: number | null; hwId: string | null; pulseId: string | null }
    };
    vad: { mode: number; pauseSeconds: number };
  };
  response: {
    voice: { active: string; speed: number };
    audio: { 
      speakerDevice: { name: string | null; index: number | null; hwId: string | null; pulseId: string | null }
    };
    behavior: { autoSpeak: boolean };
  };
  dialog?: {
    ui?: { width?: number; renderThrottleMs?: number; transDelay?: number };
  };
}

// Visible width helper (in case pi-tui doesn't export it)
function visibleWidth(str: string): number {
  // Remove ANSI escape sequences
  const clean = str.replace(/\x1b\[[0-9;]*m/g, '');
  // Count characters (basic - assumes ASCII for border chars)
  let width = 0;
  for (const char of clean) {
    const code = char.charCodeAt(0);
    // CJK chars are double width
    if ((code >= 0x2E80 && code <= 0x9FFF) ||
        (code >= 0xAC00 && code <= 0xD7AF) ||
        (code >= 0xFF01 && code <= 0xFF60)) {
      width += 2;
    } else {
      width += 1;
    }
  }
  return width;
}

function loadSettings(): VoiceSettings {
  try {
    const home = process.env.HOME || "~";
    const path = SETTINGS_FILE.replace("~", home);
    if (existsSync(path)) {
      const data = JSON.parse(readFileSync(path, "utf-8"));
      return {
        version: data.version,
        transcription: {
          engine: data.transcription?.engine || "whisper",
          model: { size: data.transcription?.model?.size || "base" },
          audio: { 
            micDevice: data.transcription?.audio?.micDevice || { name: null, index: null, hwId: null, pulseId: null }
          },
          vad: { mode: data.transcription?.vad?.mode ?? 2, pauseSeconds: data.transcription?.vad?.pauseSeconds ?? 5 }
        },
        response: {
          voice: { active: data.response?.voice?.active ?? "af_heart", speed: data.response?.voice?.speed ?? 1.5 },
          audio: { 
            speakerDevice: data.response?.audio?.speakerDevice || { name: null, index: null, hwId: null, pulseId: null }
          },
          behavior: { autoSpeak: data.response?.behavior?.autoSpeak ?? false }
        },
        dialog: {
          ui: {
            width: data.dialog?.ui?.width ?? 76,
            renderThrottleMs: data.dialog?.ui?.renderThrottleMs ?? 100,
            transDelay: data.dialog?.ui?.transDelay ?? 5
          }
        }
      };
    }
  } catch {}
  // Default settings
  return {
    version: 1,
    transcription: {
      engine: "whisper",
      model: { size: "base" },
      audio: { micDevice: { name: null, index: null, hwId: null, pulseId: null } },
      vad: { mode: 2, pauseSeconds: 5 }
    },
    response: {
      voice: { active: "af_heart", speed: 1.5 },
      audio: { speakerDevice: { name: null, index: null, hwId: null, pulseId: null } },
      behavior: { autoSpeak: false }
    },
    dialog: {
      ui: {
        width: 76,
        renderThrottleMs: 100,
        transDelay: 5
      }
    }
  };
}

// Type for TUI requestRender
interface TUIHandle {
  requestRender(): void;
}

export default function (pi: ExtensionAPI) {
  // Opens the dialog. Enter (or x) submits the captured speech directly to
  // the agent LLM via sendUserMessage and the SAME dialog stays open for
  // more dictation; the agent's response streams behind/around the overlay.
  // Esc (or Enter on empty transcript) closes it. No editor round-trip and
  // no reopen cycle is involved.
  const openDialog = async (ctx: ExtensionCommandContext): Promise<void> => {
    // Load width from settings for overlay
    const settings = loadSettings();
    const dialogWidth = settings.dialog?.ui?.width ?? 76;

    const result = await ctx.ui.custom<string[] | undefined>(
      (tui, theme, _keybindings, done) => {
        return new VoiceDialogComponent(pi, theme, done, ctx, tui);
      },
      { overlay: true, overlayOptions: { width: dialogWidth } }
    );

    if (result && result.length > 0) {
      // Safety: the component normally submits directly and exits only via
      // Esc/empty-Enter, so a non-empty result here should not happen. Send
      // it anyway so dictation is never silently lost.
      pi.sendUserMessage(result.join(". "), { deliverAs: "followUp" });
    }
    ctx.ui.notify("🎙️ Voice dialog closed", "info");
  };

  pi.registerCommand("vd", {
    description: "Voice dictation - executes on Enter, returns to dictation (Esc exits)",
    handler: async (_args, ctx: ExtensionCommandContext) => {
      await openDialog(ctx);
    },
  });

  // Alias: /vd-dialog
  pi.registerCommand("vd-dialog", {
    description: "Alias for /vd - dictation stays open after execute",
    handler: async (_args, ctx: ExtensionCommandContext) => {
      await openDialog(ctx);
    },
  });
}

class VoiceDialogComponent implements Focusable {
  width: number;
  focused = false;

  private transcripts: string[] = [];
  private isRecording = false;
  private isProcessing = false;
  private recordingProc: any = null;
  private statusMessage = "Press SPACE to start recording";
  private deviceName = "Unknown";
  private modelSize = "base";
  private engine = "whisper";
  private ttsVoice = "af_heart";
  private ttsSpeed = 1.5;
  private transDelay = 5;
  private home = process.env.HOME || "~";
  private tui: TUIHandle;
  private renderTimer: any = null;
  private currentStatusFile: string = "";

  private expand(path: string): string {
    return path.replace("~", this.home);
  }

  constructor(
    private pi: ExtensionAPI,
    private theme: Theme,
    private done: (result: string[] | undefined) => void,
    private ctx: ExtensionCommandContext,
    tui: TUIHandle
  ) {
    this.tui = tui;
    // Load width and transDelay from settings (voice-control.ts uses dialog.ui)
    const settings = loadSettings();
    this.width = settings.dialog?.ui?.width ?? 76;
    this.transDelay = settings.dialog?.ui?.transDelay ?? 5;
    this.engine = settings.transcription.engine === "parakeet" ? "parakeet" : "whisper";
    log(`Dialog width: ${this.width}, transDelay: ${this.transDelay}`);
    log("Initializing...");
    this.loadTTSConfig();
    this.getDeviceInfo();
    log(`Dialog ready: ${this.deviceName} | ${this.modelSize} | ${this.ttsVoice} | width=${this.width}`);
  }

  private startRenderTimer() {
    const settings = loadSettings();
    const throttleMs = settings.dialog?.ui?.renderThrottleMs ?? 100;
    log(`Starting render timer at ${throttleMs}ms interval`);
    this.renderTimer = setInterval(() => {
      // Poll status file for updates
      if (this.currentStatusFile) {
        try {
          const fs = require('fs');
          if (fs.existsSync(this.currentStatusFile)) {
            const data = JSON.parse(fs.readFileSync(this.currentStatusFile, 'utf-8'));
            if (data.status === 'speaking') {
              this.statusMessage = ` 🎙️ [${data.countdown_bar}] SPEAKING`;
            } else if (data.status === 'countdown') {
              this.statusMessage = ` 🎙️ [${data.countdown_bar}] ${data.countdown}s to stop`;
            } else if (data.status === 'listening') {
              this.statusMessage = ` 🎙️ [${data.countdown_bar}] Listening...`;
            } else if (data.status === 'done') {
              this.statusMessage = " ⏳ Processing...";
            }
          }
        } catch (e) {
          // Ignore file read errors
        }
      }
      this.tui.requestRender();
    }, throttleMs);
  }

  private stopRenderTimer() {
    if (this.renderTimer) {
      clearInterval(this.renderTimer);
      this.renderTimer = null;
    }
    this.currentStatusFile = "";
  }

  // Direct render without throttling
  private throttledRender() {
    // No-op - timer handles renders
  }
  
  private deviceIndex: number | null = null;

  private getDeviceInfo() {
    try {
      const settings = loadSettings();
      const micDevice = settings.transcription.audio.micDevice;
      
      log(`Settings device: name=${micDevice?.name}, index=${micDevice?.index}, hwId=${micDevice?.hwId}`);
      this.modelSize = settings.transcription.model.size;
      this.engine = settings.transcription.engine === "parakeet" ? "parakeet" : "whisper";
      
      // Always query available devices first (indices can change!)
      const uvPython = this.expand(UV_PYTHON);
      const vadScript = this.expand(VAD_SCRIPT);
      
      log("Querying available audio devices...");
      const output = execSync(`${uvPython} ${vadScript} /tmp/test.wav --list-devices 2>&1`, {
        encoding: "utf-8",
        timeout: 3000
      });

      const lines = output.split('\n');
      const devices: Array<{index: number, name: string, cleanName: string}> = [];
      
      for (const line of lines) {
        const match = line.match(/^(\d+):\s*(.+)$/);
        if (match) {
          const idx = parseInt(match[1]);
          let name = match[2].trim();
          if (name === 'pulse' || name === 'default') continue;
          
          const cleanName = name.replace(/\s*\(hw:\d+,\d+\)\s*$/, '');
          devices.push({index: idx, name, cleanName});
        }
      }
      
      log(`Found ${devices.length} devices: ${devices.map(d => `${d.index}=${d.cleanName.substring(0, 20)}`).join(', ')}`);
      
      // If we have stored device info, try to find it by name first (more stable than index)
      if (micDevice?.name && devices.length > 0) {
        // Clean stored name for comparison
        const storedCleanName = micDevice.name.replace(/\s*\(hw:\d+,\d+\)\s*$/, '').trim();
        
        // Search for device by name match
        const matchedByName = devices.find(d => {
          const cleanD = d.cleanName.trim();
          return cleanD === storedCleanName ||
                 cleanD.includes(storedCleanName) ||
                 storedCleanName.includes(cleanD);
        });
        
        if (matchedByName) {
          this.deviceIndex = matchedByName.index;
          this.deviceName = micDevice.name.substring(0, 30);
          log(`Found device by name match: ${this.deviceName} at index ${this.deviceIndex} (stored index was ${micDevice.index})`);
          this.loadTTSConfig();
          return;
        }
        
        // If name didn't match, try the stored index as fallback
        const matchedByIndex = devices.find(d => d.index === micDevice.index);
        if (matchedByIndex) {
          this.deviceIndex = matchedByIndex.index;
          this.deviceName = matchedByIndex.cleanName.substring(0, 30);
          log(`Found device by index: ${this.deviceName} at index ${this.deviceIndex}`);
          this.loadTTSConfig();
          return;
        }
        
        log(`WARNING: Stored device "${micDevice.name}" not found by name or index`);
      }
      
      // Auto-select first real device
      if (devices.length > 0) {
        const first = devices.find(d => !d.name.includes('Dock')) || devices[0];
        this.deviceIndex = first.index;
        this.deviceName = micDevice?.name ? `${micDevice.name.substring(0, 20)}... (unavailable)` : "System Default";
        log(`Auto-selected: ${first.cleanName} (index ${first.index}), displaying as "${this.deviceName}"`);
      } else {
        log("WARNING: No hardware devices found");
        this.deviceName = "No device";
      }
    } catch (e) {
      log(`ERROR: Getting device info: ${e}`);
      this.deviceName = "Device error";
    }
    
    // Load voice-response settings
    this.loadTTSConfig();
  }

  private loadTTSConfig() {
    try {
      const configPath = "~/.pi/agent/skills/voice-response/config.json";
      const path = configPath.replace("~", this.home);
      if (existsSync(path)) {
        const data = JSON.parse(readFileSync(path, "utf-8"));
        this.ttsVoice = data.activeVoice || data.defaultVoice || "af_heart";
        this.ttsSpeed = data.activeSpeed || data.defaultSpeed || 1.5;
      }
    } catch (e) {
    }
  }

  private async startRecording() {
    if (this.isRecording || this.isProcessing) {
      return;
    }

    this.isRecording = true;
    // Use full timestamp with milliseconds for true uniqueness
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    const recordingFile = join(tmpdir(), `pi_voice_${timestamp}.wav`);
    const statusFile = join(tmpdir(), `pi_voice_${timestamp}.json`);
    this.currentStatusFile = statusFile;
    this.startRenderTimer(); // Start timer-based rendering

    const vadScript = this.expand(VAD_SCRIPT);
    const uvPython = this.expand(UV_PYTHON);
    
    // Log all recording parameters
    log("=== RECORDING PARAMETERS ===");
    log(`Python: ${uvPython}`);
    log(`VAD Script: ${vadScript}`);
    log(`Output file: ${recordingFile}`);
    log(`Device index: ${this.deviceIndex}`);
    log(`Device name: ${this.deviceName}`);
    log(`Pause timeout: ${this.transDelay}s`);
    
    const deviceArgs = this.deviceIndex !== null
      ? [vadScript, recordingFile, "--pause", this.transDelay.toFixed(1), "--device", this.deviceIndex.toString(), "--status-file", statusFile]
      : [vadScript, recordingFile, "--pause", this.transDelay.toFixed(1), "--status-file", statusFile];
    
    log(`Full command: ${uvPython} ${deviceArgs.join(' ')}`);

    // Device selected

    // Recording started

    this.recordingProc = spawn(uvPython, deviceArgs, {
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false,
      cwd: process.env.HOME || os.homedir()
    });
    
    if (!this.recordingProc) {
      log("ERROR: Failed to spawn recording process");
      this.isRecording = false;
      this.stopRenderTimer();
      this.statusMessage = "❌ Failed to start recorder";
      this.tui.requestRender();
      return;
    }
    
    log(`Spawned PID: ${this.recordingProc.pid}`);
    
    this.recordingProc.on("error", (err: Error) => {
      log(`ERROR: Spawn failed: ${err.message}`);
      if (processExited) return;
      processExited = true;
      this.isRecording = false;
      this.stopRenderTimer();
      this.statusMessage = `❌ Error - SPACE to retry`;
      this.tui.requestRender();
    });

    let processExited = false;
    let recordingSuccess = false;

    // Python writes to status file only (no stdout to prevent TUI corruption)

    this.recordingProc.on("close", async (code: number) => {
      log(`Recording ended: code=${code}, initial_success=${recordingSuccess}`);

      if (processExited) return;
      processExited = true;

      this.isRecording = false;
      this.stopRenderTimer();

      // Check status file for final result
      try {
        const fs = require('fs');
        if (fs.existsSync(statusFile)) {
          const data = JSON.parse(fs.readFileSync(statusFile, 'utf-8'));
          if (data.status === 'done' && data.has_speech) {
            recordingSuccess = true;
          }
        }
      } catch (e) {
        log(`Error reading status file: ${e}`);
      }

      // Wait a tiny bit for file system
      await new Promise(r => setTimeout(r, 100));

      log(`Recording final success: ${recordingSuccess}`);

      if (!recordingSuccess) {
        this.statusMessage = "⚠️ No speech - SPACE to retry";
        this.tui.requestRender();
        return;
      }

      if (!existsSync(recordingFile)) {
        log(`ERROR: File not found: ${recordingFile}`);
        this.statusMessage = "⚠️ No recording file - SPACE to retry";
        this.tui.requestRender();
        return;
      }

      const stats = { size: 0 };
      try {
        const fs = require('fs');
        const s = fs.statSync(recordingFile);
        stats.size = s.size;
        log(`Recorded: ${recordingFile} (${stats.size} bytes)`);
      } catch (e) {
        log(`ERROR: Could not stat file: ${e}`);
      }

      await this.transcribeFile(recordingFile);
    });

    this.recordingProc.on("error", (err: Error) => {
      log(`ERROR: Process error: ${err.message}`);
      if (processExited) return;
      processExited = true;
      this.isRecording = false;
      this.stopRenderTimer();
      this.statusMessage = `❌ Error - SPACE to retry`;
      this.tui.requestRender();
    });
  }

  private async transcribeFile(recordingFile: string) {
    this.isProcessing = true;
    this.statusMessage = "⏳ Transcribing...";
    this.tui.requestRender();
    
    const settings = loadSettings();
    const engine = settings.transcription.engine === "parakeet" ? "parakeet" : "whisper";
    const modelSize = settings.transcription.model.size;
    const uvPython = this.expand(UV_PYTHON);
    const clientPath = this.expand(engine === "parakeet" ? PARAKEET_CLIENT : WHISPER_CLIENT);
    const daemonPath = this.expand(engine === "parakeet" ? PARAKEET_DAEMON : "~/.pi/agent/scripts/voice-control/whisper-daemon.py");
    const clientTimeoutMs = engine === "parakeet" ? 120000 : 10000;
    
    log(`Transcribing with engine: ${engine}, model: ${modelSize}`);
    
    try {
      // Try client - it will fail if daemon not running
      let transcript = await this.tryTranscribe(uvPython, clientPath, recordingFile, modelSize, clientTimeoutMs);
      
      // If empty, try starting daemon and retry
      if (!transcript) {
        log(`Starting ${engine} daemon...`);
        this.statusMessage = engine === "parakeet" ? "🔄 Starting parakeet daemon (CPU)..." : "🔄 Starting whisper daemon...";
        this.tui.requestRender();
        
        spawn(uvPython, [daemonPath], {
          stdio: 'ignore',
          detached: true
        });
        
        // Wait for the daemon socket (parakeet may download 178 MB of weights on first ever run)
        const waitMs = engine === "parakeet" ? 60000 : 5000;
        const socketPath = engine === "parakeet" ? PARAKEET_SOCKET : "/tmp/pi-whisper.sock";
        await this.waitForFile(socketPath, waitMs);
        
        // Retry transcription
        transcript = await this.tryTranscribe(uvPython, clientPath, recordingFile, modelSize, clientTimeoutMs);
      }
      
      // Cleanup temp files
      try { unlinkSync(recordingFile); } catch {}
      try { unlinkSync(this.currentStatusFile); } catch {}
      
      if (transcript && transcript.length > 0) {
        this.transcripts.push(transcript);
        // Calculate max length for status message based on width
        const maxStatusLen = Math.max(20, this.width - 45);
        const short = transcript.length > maxStatusLen 
          ? transcript.substring(0, maxStatusLen - 3) + '...' 
          : transcript;
        log(`Transcript: "${short}"`);
        this.statusMessage = `✓ "${short}" - SPACE for more`;
      } else {
        log("WARN: Empty transcript");
        this.statusMessage = "⚠️ Empty transcript - SPACE to retry";
      }
    } catch (e: any) {
      log(`ERROR: Transcription error: ${e.message}`);
      this.statusMessage = `❌ Failed - SPACE to retry`;
    } finally {
      this.isProcessing = false;
      this.tui.requestRender();
    }
  }
  
  /**
   * Submit captured transcripts directly to the agent LLM and keep the
   * dialog open for more dictation (agent response streams behind the
   * overlay; Esc exits at any time).
   */
  private executeTranscripts(): void {
    if (this.transcripts.length === 0) {
      this.done(undefined);
      return;
    }
    // Strip trailing periods per take so the ". " join can't produce ".."
    const pieces = this.transcripts
      .map(t => t.trim().replace(/\.+$/, ""))
      .filter(t => t.length > 0);
    const text = pieces.length > 0 ? pieces.join(". ") + "." : "";
    if (!text) {
      this.done(undefined);
      return;
    }
    try {
      this.pi.sendUserMessage(text, { deliverAs: "followUp" });
      log(`Sent directly to agent: "${text.substring(0, 80)}"`);
      this.transcripts = [];
      this.statusMessage = "✓ Sent to agent - SPACE for more, Esc to exit";
    } catch (e) {
      log(`sendUserMessage failed: ${e}`);
      this.statusMessage = "⚠ Submit failed - see /tmp/voice-dialog.log";
    }
    this.tui.requestRender();
  }

  private async waitForFile(path: string, timeoutMs: number): Promise<boolean> {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      try {
        if (existsSync(path)) return true;
      } catch {}
      await new Promise(r => setTimeout(r, 250));
    }
    return false;
  }

  private tryTranscribe(uvPython: string, clientPath: string, recordingFile: string, modelSize: string, timeoutMs: number = 10000): Promise<string> {
    return new Promise((resolve) => {
      const proc = spawn(uvPython, [clientPath, recordingFile, modelSize], {
        stdio: ['ignore', 'pipe', 'pipe'],
        timeout: Math.max(30000, timeoutMs + 5000)
      });
      
      let text = "";
      let stderr = "";
      
      proc.stdout.on("data", (data: Buffer) => { text += data.toString(); });
      proc.stderr.on("data", (data: Buffer) => { stderr += data.toString(); });
      
      proc.on("close", (code: number) => {
        log(`Transcription client: code=${code}, text=${text.trim().substring(0, 50)}...`);
        if (code === 0 && text.trim()) {
          resolve(text.trim());
        } else {
          if (stderr) log(`Transcription stderr: ${stderr.substring(0, 100)}`);
          resolve("");
        }
      });
      
      proc.on("error", (err: Error) => {
        log(`Transcription client error: ${err.message}`);
        resolve("");
      });
      
      // Timeout fallback
      setTimeout(() => {
        try { proc.kill(); } catch {}
        resolve("");
      }, timeoutMs);
    });
  }

  private stopRecording(reason: string = "unknown") {
    log(`stopRecording called: ${reason}`);
    if (this.recordingProc) {
      try {
        this.recordingProc.kill('SIGTERM');
        log(`Sent SIGTERM to recording process`);
      } catch (e) {
        log(`Error killing process: ${e}`);
      }
      this.recordingProc = null;
    }
  }

  handleInput(data: string): void {
    // Debug: log what keys we receive
    // console.log(`[VoiceDialog] Key received: ${JSON.stringify(data)} (code: ${data.charCodeAt(0)})`);

    // Escape: Cancel (ESC is \x1b)
    if (data === '\x1b' || data.startsWith('\x1b')) {
      this.stopRecording("escape");
      this.done(undefined);
      return;
    }

    // Enter: Execute and KEEP the dialog open for more dictation.
    // Submits directly to the agent LLM (queued as follow-up if busy).
    if (data === '\r' || data === '\n') {
      this.stopRecording("enter");
      this.executeTranscripts();
      return;
    }

    // x: Execute (stays open, same as Enter)
    if (data === 'x' || data === 'X') {
      this.stopRecording("execute");
      this.executeTranscripts();
      return;
    }

    // -- : Clear all (check before single -)
    if (data === '--' || data === '\\x2d\\x2d') {
      if (!this.isRecording && !this.isProcessing) {
        this.transcripts = [];
        this.statusMessage = "Cleared - SPACE to record";
        this.tui.requestRender();
      }
      return;
    }

    // - : Remove last
    if (data === '-' || data === '\x2d') {
      if (!this.isRecording && !this.isProcessing && this.transcripts.length > 0) {
        this.transcripts.pop();
        this.statusMessage = "Removed last - SPACE for more";
        this.tui.requestRender();
      }
      return;
    }

    // Space: Start recording
    if (data === ' ' || data === '\x20') {
      if (!this.isRecording && !this.isProcessing) {
        this.startRecording();
      }
      return;
    }
  }

  /**
   * Word-wrap text to a visible width. Words that individually exceed the
   * width are hard-split so nothing is ever lost. Returns at least one line.
   */
  private wrapText(text: string, width: number): string[] {
    if (width < 4) return [text];
    const lines: string[] = [];
    let cur = "";
    const push = () => {
      if (cur !== "") {
        lines.push(cur);
        cur = "";
      }
    };
    for (const word of text.split(/\s+/).filter(w => w.length > 0)) {
      // Hard-split oversized words fitting the width
      let pieces: string[] = [word];
      let guard = 0;
      while (visibleWidth(pieces[pieces.length - 1]) > width && guard++ < 100) {
        const last = pieces[pieces.length - 1];
        let cut = Math.min(width, last.length);
        while (cut > 1 && visibleWidth(last.slice(0, cut)) > width) cut--;
        if (cut <= 1) break;
        pieces = pieces.slice(0, -1).concat(last.slice(0, cut), last.slice(cut));
      }
      for (const piece of pieces) {
        if (cur === "") {
          cur = piece;
        } else if (visibleWidth(cur) + 1 + visibleWidth(piece) <= width) {
          cur += " " + piece;
        } else {
          push();
          cur = piece;
        }
      }
    }
    push();
    return lines.length > 0 ? lines : [""];
  }

  render(_width: number): string[] {
    const w = this.width;
    const th = this.theme;
    const innerW = w - 2;
    const lines: string[] = [];

    const pad = (s: string, len: number) => {
      const vis = visibleWidth(s);
      return s + " ".repeat(Math.max(0, len - vis));
    };

    const row = (content: string) => th.fg("border", "│") + pad(content, innerW) + th.fg("border", "│");

    // Header with device name and engine/model
    const deviceDisplay = this.deviceName.substring(0, 22);
    const modelDisplay = this.engine === "parakeet" ? "parakeet·CPU" : this.modelSize.substring(0, 6);
    lines.push(th.fg("border", `╭${"─".repeat(innerW)}╮`));
    lines.push(row(` ${th.fg("accent", "🎙️ VOICE")} ${th.fg("dim", `│ ${deviceDisplay} │ ${modelDisplay}`)}`));
    lines.push(th.fg("border", `├${"─".repeat(innerW)}┤`));

    // Transcript area
    if (this.transcripts.length === 0) {
      lines.push(row(`  ${th.fg("dim", "[Press SPACE to start recording]")}`));
      lines.push(row(""));
    } else {
      // Word-wrap every transcript: fixed box width, vertical growth.
      // First line gets the "NN." number, wrapped lines align under it.
      this.transcripts.forEach((t, i) => {
        const num = (i + 1).toString().padStart(2);
        const prefixVis = 1 + (num + ".").length + 1; // leading space + "NN." + space
        const maxTextLen = Math.max(10, innerW - prefixVis - 1);
        const wrapped = this.wrapText(t, maxTextLen);
        wrapped.forEach((seg, j) => {
          if (j === 0) {
            lines.push(row(` ${th.fg("accent", num + ".")} ${seg}`));
          } else {
            lines.push(row(" ".repeat(prefixVis) + seg));
          }
        });
      });
    }

    // Status bar - show countdown when recording, settings when idle
    lines.push(th.fg("border", `├${"─".repeat(innerW)}┤`));
    if (this.isRecording || this.isProcessing) {
      const statusColor = this.isRecording ? "warning" : "dim";
      const maxStatusLen = innerW - 4; // Account for padding and margins
      lines.push(row(` ${th.fg(statusColor, this.statusMessage.substring(0, maxStatusLen))}`));
    } else {
      // Show settings summary when idle
      const settingsInfo = `${this.engine === "parakeet" ? "parakeet·CPU" : this.modelSize} │ ${this.ttsVoice}@${this.ttsSpeed}x`;
      const maxInfoLen = innerW - 4;
      lines.push(row(` ${th.fg("dim", settingsInfo.substring(0, maxInfoLen))}`));
    }

    // Controls
    lines.push(th.fg("border", `├${"─".repeat(innerW)}┤`));
    lines.push(row(` ${th.fg("dim", "SPACE")}=record  ${th.fg("dim", "-")}=remove  ${th.fg("dim", "--")}=clear  ${th.fg("dim", "Enter")}=exec→resume  ${th.fg("dim", "Esc")}=exit`));
    lines.push(th.fg("border", `╰${"─".repeat(innerW)}╯`));

    return lines;
  }

  invalidate(): void {}

  dispose(): void {
    this.stopRecording("dispose");
  }
}
