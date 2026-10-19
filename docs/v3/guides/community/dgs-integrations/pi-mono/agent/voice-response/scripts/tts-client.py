#!/usr/bin/env python3
"""
TTS Client - Connects to daemon for fast TTS
Usage: tts-client.py "text" [voice] [speed]
"""

import os
import sys
import socket
import json
import struct

# Pi TTS Server - named socket/pid files for shared access across PI sessions
SOCKET_PATH = "/tmp/pi-tts-server.sock"
PID_FILE= "/tmp/pi-tts-server.pid"

# Shared sanitizer: agents speak markdown-rich output; clean it at the
# engine boundary so every caller is covered (see tts_sanitize.py).
def _load_sanitize():
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from tts_sanitize import sanitize
        return sanitize
    except Exception:
        return lambda t: t

_SANITIZE = _load_sanitize()

def is_running():
    if not os.path.exists(PID_FILE):
        return False
    try:
        with open(PID_FILE, 'r') as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return os.path.exists(SOCKET_PATH)
    except:
        return False

def speak(text, voice="af_heart", speed=1.5):
    text = _SANITIZE(text)
    if not text.strip():
        return None, "Nothing worth speaking"
    if not is_running():
        return None, "Daemon not running"
    
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(SOCKET_PATH)
        
        req = json.dumps({'text': text, 'voice': voice, 'speed': speed}).encode()
        sock.sendall(struct.pack('!I', len(req)))
        sock.sendall(req)
        
        raw = sock.recv(4)
        if not raw:
            return None, "No response"
        
        msg_len = struct.unpack('!I', raw)[0]
        data = b''
        while len(data) < msg_len:
            chunk = sock.recv(msg_len - len(data))
            if not chunk:
                break
            data += chunk
        
        sock.close()
        res = json.loads(data.decode())
        
        if res['success']:
            return res.get('file'), res.get('time')
        return None, res.get('error')
    except Exception as e:
        return None, str(e)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: tts-client.py \"text\" [voice] [speed]")
        sys.exit(1)
    
    text = sys.argv[1]
    voice = sys.argv[2] if len(sys.argv) > 2 else "af_heart"
    speed = float(sys.argv[3]) if len(sys.argv) > 3 else 1.5
    
    out, time = speak(text, voice, speed)
    
    if out:
        print(f"✓ Spoken: {voice} (speed: {speed}, time: {time:.2f}s)")
        print(f"  Audio: {out}")
    else:
        print(f"Error: {time}", file=sys.stderr)
        sys.exit(1)
