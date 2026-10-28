#!/usr/bin/env python3
"""
Voice recorder with Voice Activity Detection (VAD)
Writes status to file for TUI polling. No stdout writing to prevent display corruption.
"""

import argparse
import json
import os
import signal
import sys
import wave
import warnings
warnings.filterwarnings('ignore')

try:
    import pyaudio
    import webrtcvad
    import audioop
except ImportError:
    sys.stderr.write("Error: Need pyaudio, webrtcvad, audioop\n")
    sys.exit(1)

def write_status(path, data):
    """Atomic write of status to file."""
    if path:
        try:
            tmp = path + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(data, f)
            os.rename(tmp, path)
        except Exception:
            pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--list-devices', action='store_true')
    parser.add_argument('--device', type=int)
    parser.add_argument('--pause', type=float, default=5.0)
    parser.add_argument('output', nargs='?', help='Output WAV file')
    parser.add_argument('--status-file', help='Status file to write')
    args = parser.parse_args()
    
    if args.list_devices:
        p = pyaudio.PyAudio()
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:
                sys.stderr.write(f"{i}: {info['name']}\n")
        p.terminate()
        sys.exit(0)
    
    if not args.output or not args.status_file:
        parser.error('--status-file and output are required for recording')
    
    status_file = args.status_file
    output_file = args.output
    device_index = args.device
    pause_seconds = args.pause
    frame_duration_ms = 30
    
    # Suppress ALSA errors
    old_stderr = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 2)
    
    p = pyaudio.PyAudio()
    
    if device_index is None:
        device_index = p.get_default_input_device_info()['index']
    
    device_info = p.get_device_info_by_index(device_index)
    device_name = device_info['name']
    native_rate = int(device_info['defaultSampleRate'])
    
    # VAD only supports specific rates
    supported_rates = [8000, 16000, 32000, 48000]
    if native_rate in supported_rates:
        vad_rate = native_rate
        needs_resample = False
    elif native_rate == 44100:
        vad_rate = 16000
        needs_resample = True
    else:
        vad_rate = min(supported_rates, key=lambda x: abs(x - native_rate))
        needs_resample = True
    
    vad = webrtcvad.Vad(1)
    
    stream = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=native_rate,
        input=True,
        input_device_index=device_index,
        frames_per_buffer=int(native_rate * frame_duration_ms / 1000)
    )
    
    vad_samples = int(vad_rate * frame_duration_ms / 1000)
    vad_bytes = vad_samples * 2
    native_samples = int(native_rate * frame_duration_ms / 1000)
    native_bytes = native_samples * 2
    max_silent = int(pause_seconds * 1000 / frame_duration_ms)
    
    frames = []
    vad_buffer = b''
    silent_frames = 0
    speech_detected = False
    shutdown = False
    
    def on_sigterm(signum, frame):
        nonlocal shutdown
        shutdown = True
    
    signal.signal(signal.SIGTERM, on_sigterm)
    
    # Main loop
    try:
        while not shutdown:
            try:
                chunk = stream.read(native_samples, exception_on_overflow=False)
            except:
                break
            
            frames.append(chunk)
            vad_buffer += chunk
            
            while len(vad_buffer) >= native_bytes:
                # Get audio for VAD
                if needs_resample:
                    native_chunk = vad_buffer[:native_bytes]
                    vad_buffer = vad_buffer[native_bytes:]
                    resampled, _ = audioop.ratecv(native_chunk, 2, 1, native_rate, vad_rate, None)
                    if len(resampled) < vad_bytes:
                        continue
                    vad_frame = resampled[:vad_bytes]
                    rms = audioop.rms(native_chunk, 2)
                else:
                    audio_chunk = vad_buffer[:vad_bytes]
                    vad_buffer = vad_buffer[vad_bytes:]
                    vad_frame = audio_chunk
                    rms = audioop.rms(audio_chunk, 2)
                
                # Level meter (0-20)
                level = min(20, int(rms / 500))
                level_bar = "#" * level + "-" * (20 - level)
                
                # Speech detection
                is_speech = vad.is_speech(vad_frame, vad_rate)
                is_loud = rms > 500
                
                if is_speech and is_loud:
                    silent_frames = 0
                    speech_detected = True
                    write_status(status_file, {
                        "status": "speaking",
                        "level_bar": level_bar,
                        "countdown_bar": "#####"
                    })
                else:
                    silent_frames += 1
                    
                    if speech_detected and silent_frames <= max_silent:
                        remaining = max_silent - silent_frames
                        countdown = max(0, int(remaining * frame_duration_ms / 1000 + 0.99))
                        bar = "#" * countdown + "-" * (5 - countdown)
                        write_status(status_file, {
                            "status": "countdown",
                            "level_bar": level_bar,
                            "countdown_bar": bar,
                            "countdown": countdown
                        })
                    elif not speech_detected:
                        write_status(status_file, {
                            "status": "listening",
                            "level_bar": level_bar,
                            "countdown_bar": "-----"
                        })
                
                if speech_detected and silent_frames >= max_silent:
                    write_status(status_file, {"status": "done", "has_speech": True})
                    break
            
            if speech_detected and silent_frames >= max_silent:
                break
    
    except KeyboardInterrupt:
        write_status(status_file, {"status": "done", "has_speech": speech_detected})
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()
        os.dup2(old_stderr, 2)
        os.close(old_stderr)
    
    # Save audio
    if speech_detected:
        with wave.open(output_file, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
            wf.setframerate(native_rate)
            for f in frames:
                wf.writeframes(f)
        sys.exit(0)
    else:
        write_status(status_file, {"status": "done", "has_speech": False})
        sys.exit(1)

if __name__ == '__main__':
    main()
