#!/usr/bin/env python3
"""
GPU-Accelerated Text-to-Speech using Python Kokoro
Fast TTS for agentic code response with CUDA support
"""

import sys
import os
import time
import re

# Shared sanitizer: agents speak markdown-rich output; clean it at the
# engine boundary so every caller is covered (see tts_sanitize.py).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from tts_sanitize import sanitize as _sanitize
except ImportError:
    def _sanitize(text):
        return text

# Set CUDA device before importing torch/kokoro
os.environ['CUDA_VISIBLE_DEVICES'] = '0'

try:
    import torch
    import soundfile as sf
    from kokoro import KPipeline
    
    # Force GPU
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    
except ImportError as e:
    print(f"Error: Missing dependency - {e}", file=sys.stderr)
    print("Install with: pip install kokoro soundfile torch", file=sys.stderr)
    sys.exit(1)

# Voice mapping (Python Kokoro uses different voice codes)
VOICE_MAP = {
    # American Male
    'am_michael': 'am_michael',
    'am_fenrir': 'am_fenrir', 
    'am_puck': 'am_puck',
    # American Female
    'af_bella': 'af_bella',
    'af_sarah': 'af_sarah',
    'af_nicole': 'af_nicole',
    'af_heart': 'af_heart',
    'af_kore': 'af_kore',
    'af_aoede': 'af_aoede',
    # British
    'bf_emma': 'bf_emma',
}

# Default voice
DEFAULT_VOICE = 'am_michael'
DEFAULT_SPEED = 1.0

# Cache pipeline for reuse
_pipeline_cache = {}

def get_pipeline(lang_code='a', device=None, repo_id='hexgrad/Kokoro-82M'):
    """Get or create Kokoro pipeline with caching"""
    global _pipeline_cache
    device_str = str(device)
    
    if (lang_code, device_str) not in _pipeline_cache:
        pipeline = KPipeline(
            lang_code=lang_code,
            device=device,
        )
        _pipeline_cache[(lang_code, device_str)] = pipeline
    
    return _pipeline_cache[(lang_code, device_str)]

def speak(text, voice=DEFAULT_VOICE, speed=DEFAULT_SPEED, output_file=None):
    """
    Generate speech using GPU-accelerated Kokoro
    
    Args:
        text: Text to speak
        voice: Voice ID (e.g., 'am_michael', 'af_bella')
        speed: Speech rate (0.5 to 2.0)
        output_file: Optional file to save audio (default: play directly)
    
    Returns:
        Path to audio file or None if played directly
    """
    start_time = time.time()
    
    text = _sanitize(text)
    if not text.strip():
        return None, 0.0
    
    # Validate voice
    if voice not in VOICE_MAP:
        print(f"Warning: Unknown voice '{voice}', using default {DEFAULT_VOICE}", file=sys.stderr)
        voice = DEFAULT_VOICE
    
    # Map to actual voice code
    voice_code = VOICE_MAP[voice]
    
    # Get pipeline (English)
    pipeline = get_pipeline('a', device)
    
    # Generate audio
    generator = pipeline(
        text, 
        voice=voice_code,
        speed=speed,
        split_pattern=r'\n+'
    )
    
    # Collect audio segments
    audio_segments = []
    for _, (_, _, audio) in enumerate(generator):
        audio_segments.append(audio)
    
    if not audio_segments:
        raise RuntimeError("No audio generated")
    
    # Concatenate audio
    audio = sum(audio_segments)
    
    # Save to file
    if output_file is None:
        output_file = f'/tmp/voice_response_{int(time.time())}.wav'
    
    sf.write(output_file, audio, 24000)
    
    # Play audio
    os.system(f'aplay "{output_file}" 2>/dev/null || paplay "{output_file}" 2>/dev/null || ffplay -nodisp -autoexit "{output_file}" 2>/dev/null')
    
    # Cleanup temporary file (optional - keep for debugging)
    # os.remove(output_file)
    
    elapsed = time.time() - start_time
    return output_file, elapsed

def main():
    if len(sys.argv) < 2:
        print("Usage: speak.py \"Text to speak\" [voice] [speed]", file=sys.stderr)
        print(f"Available voices: {', '.join(VOICE_MAP.keys())}", file=sys.stderr)
        sys.exit(1)
    
    text = sys.argv[1]
    voice = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_VOICE
    speed = float(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_SPEED
    
    # Show GPU status
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"[GPU] {gpu_name} ({gpu_mem:.1f} GB)", file=sys.stderr)
    else:
        print("[CPU Mode - No GPU detected]", file=sys.stderr)
    
    try:
        output_file, elapsed = speak(text, voice, speed)
        print(f"✓ Spoken using voice: {voice} (speed: {speed}, time: {elapsed:.2f}s)")
        print(f"  Audio: {output_file}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
