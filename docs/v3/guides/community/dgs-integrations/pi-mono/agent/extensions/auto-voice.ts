import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { spawn } from "node:child_process";

export default function (pi: ExtensionAPI) {
  const SPEAK_SCRIPT = "~/.pi/agent/skills/voice-response/scripts/speak.sh";
  const RESOLVE_VOICE_SCRIPT = "~/.pi/agent/skills/voice-response/scripts/resolve-voice.sh";
  
  let lastUserMessage = ""; // Store last user request

  // Resolve the voice + engine for a repo (by cwd) via the per-repo voice map
  // and the resolve-voice.sh helper. Falls back to the active/default voice and
  // engine when the repo isn't mapped. Empty strings mean "leave speak.sh to its
  // own defaults".
  function resolveRepoVoice(cwd: string | undefined): { voice: string; engine: string } {
    const none = { voice: "", engine: "" };
    if (!cwd) return none;
    const script = RESOLVE_VOICE_SCRIPT.replace("~", process.env.HOME || "~");
    try {
      const { execFileSync } = require("node:child_process");
      const out = execFileSync("bash", [script, cwd], { encoding: "utf8" }).trim().split("\n");
      return { voice: (out[0] || "").trim(), engine: (out[1] || "").trim() };
    } catch {
      return none;
    }
  }
  
  // Clip long text at a sentence boundary so the ending never reads mid-word
  // (a hard character cut makes the engine speak a broken word).
  function clipToSentence(text: string, limit: number): string {
    if (text.length <= limit) return text;
    const head = text.slice(0, limit);
    let cut = -1;
    for (const marker of [".", "!", "?", "\n"]) {
      const idx = head.lastIndexOf(marker);
      if (idx > cut) cut = idx;
    }
    const floor = Math.floor(limit * 0.4);
    if (cut >= floor) return head.slice(0, cut + 1);
    const space = head.lastIndexOf(" ");
    return space >= floor ? head.slice(0, space) : head;
  }

  function speak(text: string, isThinking: boolean = false, cwd?: string) {
    if (!text.trim()) return;
    
    const speakText = clipToSentence(text, 400);
    const prefix = isThinking ? "Thinking: " : "";
    
    const speakScript = SPEAK_SCRIPT.replace("~", process.env.HOME || "~");
    const resolved = resolveRepoVoice(cwd);
    const env = { ...process.env };
    if (resolved.voice) env.voice = resolved.voice;
    if (resolved.engine) env.GROK_TTS_ENGINE = resolved.engine;
    const child = spawn("bash", [speakScript, prefix + speakText], {
      detached: true,
      stdio: "ignore",
      env,
    });
    child.unref();
  }
  
  // Store user messages to include in context
  pi.on("message_end", async (event, _ctx) => {
    const message = event.message;
    if (message.role === "user") {
      // Store the user's request
      const userText = message.content
        .filter(c => c.type === "text")
        .map(c => (c as any).text || "")
        .filter(t => t.length > 0)
        .join(" ");
      
      // Truncate if too long
      lastUserMessage = userText.length > 100 
        ? userText.slice(0, 100) + "..." 
        : userText;
    }
  });
  
  // Speak thinking blocks during message updates
  pi.on("message_update", async (event, ctx) => {
    if (!enabled) return;
    
    const message = event.message;
    if (message.role !== "assistant") return;
    
    // Check for thinking content in the message
    const hasThinkingBlock = message.content.some(c => 
      c.type === "text" && (c as any).text?.includes("<thinking>")
    );
    
    if (hasThinkingBlock) {
      // Extract thinking content
      const thinkingText = message.content
        .filter(c => c.type === "text")
        .map(c => {
          const text = (c as any).text || "";
          const match = text.match(/<thinking>([\s\S]*?)<\/thinking>/);
          return match ? match[1].trim() : "";
        })
        .filter(t => t.length > 0)
        .join(" ");
      
      if (thinkingText && thinkingText.length > 10) {
        speak(thinkingText, true, ctx.cwd);
      }
    }
  });
  
  // Auto-speak assistant messages at the end
  pi.on("message_end", async (event, ctx) => {
    if (!enabled) return;
    
    const message = event.message;
    if (message.role !== "assistant") return;
    
    // Get final response text (excluding thinking blocks)
    const text = message.content
      .filter(c => c.type === "text")
      .map(c => {
        let text = (c as any).text || "";
        // Remove thinking blocks from final speech
        return text.replace(/<thinking>[\s\S]*?<\/thinking>/g, "").trim();
      })
      .filter(t => t.length > 0)
      .join(" ");
    
    if (!text.trim()) return;
    
    // Construct full speech: "User: ...request... Response."
    let fullText = "";
    if (lastUserMessage) {
      fullText = `User: ${lastUserMessage}. Response: ${text}`;
    } else {
      fullText = text;
    }
    
    speak(fullText, false, ctx.cwd);
    
    // Clear stored user message
    lastUserMessage = "";
  });
  
  // Toggle command
  let enabled = true;
  pi.registerCommand("toggle-voice", {
    description: "Toggle automatic voice responses",
    handler: async (_args, ctx) => {
      enabled = !enabled;
      ctx.ui.notify(`Auto-voice ${enabled ? "enabled" : "disabled"}`, "info");
    }
  });
}
