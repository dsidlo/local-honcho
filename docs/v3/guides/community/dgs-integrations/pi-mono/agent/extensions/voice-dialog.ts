/**
 * Voice Control Dialog - TUI overlay for voice commands
 *
 * Fixed: Added tui.requestRender() calls for real-time updates
 */

import type { ExtensionAPI, ExtensionCommandContext, Theme } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";
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

// ============================ WEB DIALOG (ThinkRail/browser host) ============================
// In ThinkRail, pi is hosted in-process by the app's web UI and the extension
// UI sub-protocol degrades ctx.ui.custom() to undefined - the TUI overlay
// cannot render. Detect that host via the THINKRAIL_STATIC_DIR env var the
// ThinkRail app sets for every in-process pi session, and redirect /vd to a
// small local web dialog instead:
//   1. The extension runs a localhost-only HTTP server (node:http).
//   2. The page captures mic PCM via getUserMedia, encodes a WAV blob.
//   3. POST /transcribe -> write wav to tmp -> existing parakeet/whisper
//      client + daemon autostart -> JSON { text }.
//   4. POST /execute -> pi.sendUserMessage(text, { deliverAs: "followUp" })
//      into the live session, exactly like the TUI dialog's Enter key.

const WEB_DIALOG_VERSION = "web2";
const WEB_DEFAULT_PORT = 8741;
const WEB_MAX_UPLOAD = 30 * 1024 * 1024; // 30 MB wav cap

function isThinkRailWeb(): boolean {
  // Primary: THINKRAIL_STATIC_DIR is exported by the ThinkRail host process for
  // every in-process pi session (verified in live env and in thinkrail source:
  // apps/cli/src/args.ts documents it as "Override the built web app served by
  // the host"). Fallback: any non-TTY process cannot render the TUI overlay
  // (ctx.ui.custom degrades to undefined there), so the web dialog is strictly
  // better than a silent no-op.
  return !!process.env.THINKRAIL_STATIC_DIR || process.stdout?.isTTY !== true;
}

function expandPath(path: string): string {
  return path.replace("~", process.env.HOME || "~~");
}

/**
 * Module-level transcription: spawn the configured client (whisper or
 * parakeet); if the daemon socket is missing, spawn the daemon detached,
 * wait for the socket (parakeet downloads weights on first ever run), retry.
 * Mirrors VoiceDialogComponent.transcribeFile logic for non-TUI callers.
 */
function transcribeWavWithDaemon(recordingFile: string): Promise<{ text: string; error?: string }> {
  return new Promise((resolve) => {
    const settings = loadSettings();
    const engine = settings.transcription.engine === "parakeet" ? "parakeet" : "whisper";
    const modelSize = settings.transcription.model.size;
    const uvPython = expandPath(UV_PYTHON);
    const clientPath = expandPath(engine === "parakeet" ? PARAKEET_CLIENT : WHISPER_CLIENT);
    const daemonPath = expandPath(engine === "parakeet" ? PARAKEET_DAEMON : "~/.pi/agent/scripts/voice-control/whisper-daemon.py");
    const clientTimeoutMs = engine === "parakeet" ? 120000 : 10000;
    const socketPath = engine === "parakeet" ? PARAKEET_SOCKET : "/tmp/pi-whisper.sock";
    const waitMs = engine === "parakeet" ? 60000 : 5000;

    log(`[web] transcribing: engine=${engine} model=${modelSize} file=${recordingFile}`);

    const tryClient = (): Promise<string> => new Promise((res) => {
      const proc = spawn(uvPython, [clientPath, recordingFile, modelSize], {
        stdio: ['ignore', 'pipe', 'pipe'],
        timeout: Math.max(30000, clientTimeoutMs + 5000)
      });
      let text = "";
      let stderr = "";
      proc.stdout.on("data", (d: Buffer) => { text += d.toString(); });
      proc.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });
      proc.on("close", (code: number) => {
        log(`[web] client code=${code} text=${text.trim().substring(0, 80)}`);
        if (stderr) log(`[web] client stderr: ${stderr.substring(0, 200)}`);
        res(code === 0 && text.trim() ? text.trim() : "");
      });
      proc.on("error", (err: Error) => {
        log(`[web] client error: ${err.message}`);
        res("");
      });
      setTimeout(() => { try { proc.kill(); } catch {} res(text.trim()); }, clientTimeoutMs);
    });

    const waitForFile = async (path: string, timeout: number): Promise<boolean> => {
      const start = Date.now();
      while (Date.now() - start < timeout) {
        try { if (existsSync(path)) return true; } catch {}
        await new Promise(r => setTimeout(r, 250));
      }
      return false;
    };

    (async () => {
      let transcript = await tryClient();
      if (!transcript) {
        log(`[web] starting ${engine} daemon...`);
        spawn(uvPython, [daemonPath], { stdio: 'ignore', detached: true });
        const gotSocket = await waitForFile(socketPath, waitMs);
        log(`[web] daemon socket ${gotSocket ? "ready" : "NOT ready in " + waitMs + "ms"}`);
        transcript = await tryClient();
      }
      if (transcript && transcript.length > 0) {
        resolve({ text: transcript });
      } else {
        resolve({ text: "", error: "Empty transcript (daemon may still be warming up - try again)" });
      }
    })().catch((e) => resolve({ text: "", error: String(e?.message || e) }));
  });
}

interface WebServerState {
  server: any; // import("node:http").Server
  port: number;
}

let webServerState: WebServerState | null = null;

function buildWebDialogPage(port: number): string {
  // String.raw + no ${} interpolation inside the page script: concatenation only.
  return String.raw`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice Dialog</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: ui-sans-serif, system-ui, sans-serif; background:#17111b; color:#e6ddf0;
         display:flex; align-items:center; justify-content:center; min-height:100vh; }
  .box { width: min(680px, 94vw); border:1px solid #4a3a5c; border-radius:10px; background:#221733;
         box-shadow:0 6px 40px rgba(0,0,0,.5); }
  header { padding:12px 16px; border-bottom:1px solid #4a3a5c; display:flex; gap:10px; align-items:center; }
  header h1 { font-size:14px; margin:0; color:#d9a8ff; font-weight:600; }
  header .meta { color:#8f7fa8; font-size:12px; margin-left:auto; }
  main { padding:16px; display:flex; flex-direction:column; gap:12px; }
  textarea { width:100%; min-height:140px; resize:vertical; border-radius:8px; border:1px solid #4a3a5c;
             background:#1b1226; color:#f2ecff; padding:10px; font-size:14px; line-height:1.5; }
  textarea:focus { outline-color:#d9a8ff; }
  .row { display:flex; gap:10px; align-items:center; }
  button { border:1px solid #6b4fa0; background:#33234d; color:#e6ddf0; padding:10px 18px;
           border-radius:8px; font-size:14px; cursor:pointer; }
  button:hover { background:#3f2b60; }
  button.primary { background:#7c3aed; border-color:#8d5cf0; color:#fff; font-weight:600; }
  button.primary:hover { background:#8d5cf0; }
  button.rec { background:#b3261e; border-color:#cf4238; }
  kbd { background:#33234d; border:1px solid #6b4fa0; border-radius:4px; padding:1px 6px; font-size:11px; }
  #status { color:#a68fc4; font-size:13px; min-height:18px; }
  #meter { flex:1; height:10px; border-radius:5px; background:#1b1226; overflow:hidden; border:1px solid #4a3a5c; }
  #meterBar { height:100%; width:0%; background:linear-gradient(90deg,#7c3aed,#d946ef); transition:width .05s; }
  select { background:#1b1226; color:#e6ddf0; border:1px solid #4a3a5c; border-radius:6px; padding:6px; max-width:280px; }
</style>
</head>
<body>
<div class="box">
  <header><h1>🎙️ Voice Dialog</h1><span class="meta" id="engine">…</span></header>
  <main>
    <div class="row">
      <select id="device"><option>Default microphone</option></select>
      <div id="meter"><div id="meterBar"></div></div>
    </div>
    <textarea id="transcript" placeholder="Transcripts accumulate here - edit freely, they are sent on Execute."></textarea>
    <div class="row">
      <button id="rec">● Record</button>
      <button id="del">− Remove last</button>
      <button id="clear">Clear</button>
      <span style="flex:1"></span>
      <button id="exec" class="primary">Execute → Submit</button>
    </div>
    <div class="row" style="justify-content:space-between">
      <span id="status">Ready.</span>
      <span>​<kbd>Space</kbd> record  <kbd>Enter</kbd> execute  <kbd>−</kbd> remove  <kbd>Esc</kbd> stop</span>
    </div>
  </main>
</div>
<script>
  const PORT = ` + port + `;
  const state = { recording:false, stream:null, ac:null, proc:null, analyser:null, chunks:[], takes:[], rafId:0 };
  const $ = (id) => document.getElementById(id);
  const setStatus = (s) => { $('status').textContent = s; };

  // ---- WAV encode (PCM16) ----
  function encodeWav(float32, sampleRate) {
    const buf = new ArrayBuffer(44 + float32.length * 2), v = new DataView(buf);
    const w16 = (o, s) => { for (let i=0;i<s.length;i++) v.setUint8(o+i, s.charCodeAt(i)); };
    w16(0,'RIFF'); v.setUint32(4, 36+float32.length*2, true); w16(8,'WAVE');
    w16(12,'fmt '); v.setUint32(16,16,true); v.setUint16(20,1,true); v.setUint16(22,1,true);
    v.setUint32(24,sampleRate,true); v.setUint32(28,sampleRate*2,true); v.setUint16(32,2,true); v.setUint16(34,16,true);
    w16(36,'data'); v.setUint32(40, float32.length*2, true);
    for (let i=0;i<float32.length;i++) {
      const s = Math.max(-1, Math.min(1, float32[i]));
      v.setInt16(44+i*2, s<0 ? s*0x8000 : s*0x7fff, true);
    }
    return new Blob([buf], { type: 'audio/wav' });
  }

  async function startStream(deviceId) {
    if (state.stream) { state.stream.getTracks().forEach(t=>t.stop()); }
    state.stream = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: deviceId?{exact:deviceId}:undefined,
      channelCount:1, echoCancellation:false, noiseSuppression:false, autoGainControl:true } });
    state.ac = new AudioContext({ sampleRate: 16000 });
    const src = state.ac.createMediaStreamSource(state.stream);
    state.analyser = state.ac.createAnalyser(); state.analyser.fftSize = 512;
    src.connect(state.analyser);
    state.proc = state.ac.createScriptProcessor(4096, 1, 1);
    state.proc.onaudioprocess = (e) => {
      if (!state.recording) return;
      state.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    };
    src.connect(state.proc);
    const sink = state.ac.createGain(); sink.gain.value = 0; state.proc.connect(sink); sink.connect(state.ac.destination);
    drawMeter();
  }

  function drawMeter() {
    if (state.analyser) {
      const a = new Uint8Array(state.analyser.frequencyBinCount);
      state.analyser.getByteFrequencyData(a);
      let sum = 0; for (let i=0;i<a.length;i++) sum += a[i]*a[i];
      const rms = Math.sqrt(sum/a.length);
      $('meterBar').style.width = state.recording ? Math.min(100, rms*2.2)+'%' : '0%';
    }
    state.rafId = requestAnimationFrame(drawMeter);
  }

  async function toggleRecord() {
    if (state.recording) { stopRecord(); return; }
    try { await startStream($('device').value !== 'Default microphone' ? $('device').value : undefined); }
    catch(e) { setStatus('❌ Mic access denied: ' + e.message); return; }
    state.recording = true; state.chunks = [];
    $('rec').textContent = '■ Stop'; $('rec').className = 'rec';
    setStatus('Recording… (Space or Esc to stop)');
  }

  function stopRecord() {
    if (!state.recording) return;
    state.recording = false;
    $('rec').textContent = '● Record'; $('rec').className = '';
    const sr = state.ac ? state.ac.sampleRate : 16000;
    const samples = state.chunks.reduce((n,c)=>n+c.length,0);
    if (samples < 1600) { setStatus('⚠️ No audio captured - Space to retry'); return; }
    const merged = new Float32Array(samples);
    let off=0; for (const c of state.chunks) { merged.set(c, off); off += c.length; }
    setStatus('⏳ Transcribing…');
    transcribe(encodeWav(merged, sr));
  }

  async function transcribe(blob) {
    try {
      const r = await fetch('/transcribe', { method:'POST', headers:{'Content-Type':'application/octet-stream'}, body: blob });
      const j = await r.json();
      if (j.text && j.text.trim()) {
        state.takes.push(j.text.trim());
        $('transcript').value = state.takes.map(t=>t.replace(/\.+$/, '')).join('. ') + (state.takes.length ? '.' : '');
        setStatus('✓ Added take ' + state.takes.length + ' - Space for more, Enter to execute');
      } else {
        setStatus('⚠️ ' + (j.error || 'No speech detected - Space to retry'));
      }
    } catch(e) { setStatus('❌ Transcription failed: ' + e.message); }
  }

  async function execute() {
    const text = $('transcript').value.trim();
    if (!text) { setStatus('Nothing to submit.'); return; }
    $('exec').disabled = true;
    try {
      const r = await fetch('/execute', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ text }) });
      const j = await r.json();
      if (j.ok) { setStatus('✓ Sent to agent - keep dictating (Space), Esc to stop'); state.takes = []; $('transcript').value = ''; }
      else setStatus('⚠️ ' + (j.error || 'Submit failed'));
    } catch(e) { setStatus('❌ ' + e.message); }
    finally { $('exec').disabled = false; }
  }

  function removeLast() {
    if (state.takes.length) { state.takes.pop();
      $('transcript').value = state.takes.map(t=>t.replace(/\.+$/, '')).join('. ') + (state.takes.length ? '.' : '');
      setStatus('Removed last take.'); } else { $('transcript').value=''; setStatus('Cleared.'); }
  }
function clearAll() { state.takes = []; $('transcript').value = ''; $('transcript').focus(); }

  $('rec').onclick = toggleRecord; $('exec').onclick = execute; $('del').onclick = removeLast; $('clear').onclick = clearAll;
  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
      if (e.key === 'Escape') { if (state.recording) { stopRecord(); e.preventDefault(); } } return;
    }
    if (e.code === 'Space' && !e.repeat) { e.preventDefault(); toggleRecord(); }
    else if (e.key === 'Enter') { e.preventDefault(); execute(); }
    else if (e.key === '-') { if (!$('transcript').value) { removeLast(); e.preventDefault(); } }
    else if (e.key === 'Escape' && state.recording) { stopRecord(); e.preventDefault(); }
  });

  (async () => {
    try {
      const h = await (await fetch('/health')).json();
      $('engine').textContent = h.engine + ' · ' + h.model;
    } catch {}
    try {
      if (!navigator.mediaDevices) return;
      setTimeout(async () => { // labels need mic permission; populate best-effort
        try {
          const ds = (await navigator.mediaDevices.enumerateDevices()).filter(d=>d.kind==='audioinput');
          if (ds.length) { $('device').innerHTML = ''; ds.forEach((d,i)=>{ const o=document.createElement('option');
            o.value = d.deviceId; o.textContent = (d.label||('Mic '+(i+1))).substring(0,32); $('device').appendChild(o); }); }
        } catch {}
      }, 800);
    } catch {}
    $('transcript').focus();
  })();
</script>
</body>
</html>`;
}

async function openWebDialog(pi: ExtensionAPI, ctx: ExtensionCommandContext): Promise<void> {
  log(
    `[vd] /vd invoked pid=${process.pid} version=${WEB_DIALOG_VERSION} thinkrail=${!!process.env.THINKRAIL_STATIC_DIR} tty=${process.stdout?.isTTY ? "yes" : "no"}`,
  );
  const settings = loadSettings();
  const webCfg: any = (settings as any).dialog?.web ?? {};  if (webCfg.enabled === false) {
    log('[web] /vd requested but dialog.web.enabled=false in voice-settings.json');
    ctx.ui.notify("🎙️ /vd web dialog is disabled (dialog.web.enabled=false)", "info");
    return;
  }

  try {
    const { port } = await ensureWebDialogServer(pi);
    const url = `http://127.0.0.1:${port}/`;
    log(`[web] opening dialog at ${url}`);
    announceWebDialog(ctx, url);
    if (webCfg.autoOpen !== false && !process.env.VOICE_DIALOG_NO_AUTOOPEN) {
      spawn("xdg-open", [url], { stdio: 'ignore', detached: true })
        .on("error", () => {
          log("[web] xdg-open failed (non-GUI host?) - URL shown in chat + widget");
          ctx.ui.notify(
            `🎙️ Could not open a browser automatically. Open the voice dialog manually at ${url}`,
            "warning",
          );
        })
        .on("spawn", () => log("[web] browser open requested via xdg-open"));
    }
    // Keep the dialog reachable: reuse on subsequent /vd. Notify each time so
    // the URL is always visible.
  } catch (e: any) {
    log(`[web] failed to start dialog server: ${e?.message || e}`);
    ctx.ui.notify(`❌ /vd web dialog failed: ${e?.message || e}`, "error");
  }
}

/** Feedback layers: ephemeral toast + persistent widget strip above the composer. */
function announceWebDialog(ctx: ExtensionCommandContext, url: string): void {
  try {
    ctx.ui.notify(`🎙️ /vd voice dialog (web2): ${url}`, "info");
    ctx.ui.setWidget("voice-dialog", [
      `🎙️ /vd voice dialog (web2): ${url}`,
      "Dictate, edit, Execute — text lands in this chat as a follow-up.",
    ]);
  } catch (e: any) {
    log(`[web] announce failed: ${e?.message || e}`);
  }
}

/** Start (or reuse) the localhost dialog server. Singleton per pi process. */
function ensureWebDialogServer(pi: ExtensionAPI): Promise<WebServerState> {
  if (webServerState) return Promise.resolve(webServerState);

  return new Promise((resolve, reject) => {
    const http = require('node:http');
    const cfg: any = loadSettings();
    const engine = cfg.transcription.engine === "parakeet" ? "parakeet" : "whisper";
    const basePort = Number(cfg.dialog?.web?.port) || WEB_DEFAULT_PORT;

    const server = http.createServer((req: any, res: any) => {
      const closeBody = () => { try { req.destroy(); } catch {} };
      // CORS: same origin only - serve our own page; never add ACAO headers.
      if (req.method === "GET" && (req.url === "/" || req.url.startsWith("/?"))) {
        res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
                             "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; media-src blob:; connect-src 'self'" });
        res.end(buildWebDialogPage(webServerState?.port ?? basePort));
        return;
      }
      if (req.method === "GET" && req.url === "/health") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true, engine, model: cfg.transcription.model.size }));
        return;
      }
      if (req.method === "POST" && req.url === "/transcribe") {
        let size = 0; const bufs: Buffer[] = [];
        req.on("data", (c: Buffer) => { size += c.length; if (size <= WEB_MAX_UPLOAD) bufs.push(c); else closeBody(); });
        req.on("end", async () => {
          try {
            if (size === 0 || size > WEB_MAX_UPLOAD) { res.writeHead(413); res.end(JSON.stringify({ error: "bad upload size" })); return; }
            const ts = new Date().toISOString().replace(/[:.]/g, "-");
            const wavPath = join(tmpdir(), `pi_voice_web_${ts}.wav`);
            writeFileSync(wavPath, Buffer.concat(bufs));
            log(`[web] upload ${size} bytes -> ${wavPath}`);
            const result = await transcribeWavWithDaemon(wavPath);
            try { unlinkSync(wavPath); } catch {}
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(JSON.stringify(result));
          } catch (e: any) {
            log(`[web] transcribe handler error: ${e?.message || e}`);
            res.writeHead(500); res.end(JSON.stringify({ error: String(e?.message || e) }));
          }
        });
        req.on("error", closeBody);
        return;
      }
      if (req.method === "POST" && req.url === "/execute") {
        let size = 0; const bufs: Buffer[] = [];
        req.on("data", (c: Buffer) => { size += c.length; if (size <= 1024 * 1024) bufs.push(c); else closeBody(); });
        req.on("end", () => {
          try {
            const body = JSON.parse(Buffer.concat(bufs).toString("utf-8") || "{}");
            const text = String(body.text || "").trim();
            if (!text) { res.writeHead(400); res.end(JSON.stringify({ error: "empty text" })); return; }
            pi.sendUserMessage(text, { deliverAs: "followUp" });
            log(`[web] executed -> agent: ${text.substring(0, 100)}`);
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ ok: true }));
          } catch (e: any) {
            log(`[web] execute error: ${e?.message || e}`);
            res.writeHead(500); res.end(JSON.stringify({ error: String(e?.message || e) }));
          }
        });
        req.on("error", closeBody);
        return;
      }
      res.writeHead(404); res.end();
    });

    // Listen with small port-jump fallback if a stale server holds the port.
    let attempt = 0;
    const listen = (port: number) => {
      attempt++;
      server.once("error", (e: any) => {
        if (e && e.code === "EADDRINUSE" && attempt <= 5) { log(`[web] port ${port} busy, trying ${port + 1}`); setTimeout(() => listen(port + 1), 50); }
        else { log(`[web] server error: ${e?.message || e}`); reject(e); }
      });
      server.listen(port, "127.0.0.1", () => {
        const address: any = server.address();
        const boundPort = address.port;
        log(`[web] dialog server listening on 127.0.0.1:${boundPort}`);
        webServerState = { server, port: boundPort };
        resolve(webServerState);
      });
    };
    listen(basePort);
  });
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
      if (isThinkRailWeb()) { await openWebDialog(pi, ctx); return; }
      await openDialog(ctx);
    },
  });

  // Alias: /vd-dialog
  pi.registerCommand("vd-dialog", {
    description: "Alias for /vd - dictation stays open after execute",
    handler: async (_args, ctx: ExtensionCommandContext) => {
      if (isThinkRailWeb()) { await openWebDialog(pi, ctx); return; }
      await openDialog(ctx);
    },
  });

  // Agent-callable tool: the ThinkRail chat can reach the dialog WITHOUT slash
  // commands ("open the voice dialog" -> the agent calls this tool). The tool
  // context has ui (bridge + widget), so feedback uses the same channels.
  pi.registerTool({
    name: "voice_dialog_open",
    label: "Open voice dialog",
    description:
      "Open the /vd voice dictation dialog. In a web/ThinkRail host this starts the " +
      "localhost mic page (returns the URL and shows it in the chat UI); ask the user " +
      "to open that URL and dictate, then Execute. In a terminal host this opens the TUI overlay.",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, _signal, _onUpdate, ctx) {
      let hasUi = false;
      try { hasUi = !!(ctx as any).hasUI; } catch {}
      log(`[vd] tool voice_dialog_open invoked hasUI=${hasUi}`);
      if (isThinkRailWeb()) {
        await openWebDialog(pi, { ui: (ctx as any).ui } as ExtensionCommandContext);
        const port = webServerState?.port ?? WEB_DEFAULT_PORT;
        return {
          content: [{ type: "text", text: `Voice dialog running at http://127.0.0.1:${port}/ — URL shown as a chat widget; the user dictates there, Execute lands in the chat.` }],
          details: undefined,
        } as any;
      }
      try {
        const openCtx = ctx as unknown as ExtensionCommandContext;
        await openDialog(openCtx);
        return {
          content: [{ type: "text", text: "Voice dialog overlay opened (terminal mode)." }],
          details: undefined,
        } as any;
      } catch (e: any) {
        return {
          content: [{ type: "text", text: `Voice dialog failed to open: ${e?.message || e}` }],
          details: undefined,
        } as any;
      }
    },
  });

  // Log every activation so liveness/which-code-is-live is checkable without
  // the UI: /tmp/voice-dialog.log is shared across pi processes on this host.
  pi.on("session_start", async () => {
    log(
      `[vd] session_start pid=${process.pid} version=${WEB_DIALOG_VERSION} thinkrail=${!!process.env.THINKRAIL_STATIC_DIR} tty=${process.stdout?.isTTY ? "yes" : "no"}`,
    );
  });

  // Clean shutdown: stop the localhost dialog server with the session.
  pi.on("session_shutdown", async () => {
    if (webServerState) {
      try { webServerState.server.close(); } catch {}
      log(`[web] server closed on session_shutdown (port ${webServerState.port})`);
      webServerState = null;
    }
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
