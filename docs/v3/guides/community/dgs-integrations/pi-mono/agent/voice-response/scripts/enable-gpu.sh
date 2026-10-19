#!/bin/bash
# Enable GPU acceleration for voice-response skill
# Run this before using the TTS server for GPU support

# cuDNN library location (from system cache)
# Note: This is a mounted path that must be absolute
CUDNN_PATH="$HOME/.cache/uv/archive-v0/LDMDYT2Tst6ZaV8bda21L/nvidia/cudnn/lib"
CUDA_PATH="/usr/local/cuda-12.8/targets/x86_64-linux/lib"

# Add to library path
if [[ -d "$CUDNN_PATH" ]]; then
    export LD_LIBRARY_PATH="$CUDNN_PATH:$CUDA_PATH:$LD_LIBRARY_PATH"
    echo "✓ GPU libraries configured"
    echo "  cuDNN: $CUDNN_PATH"
    echo "  CUDA: $CUDA_PATH"
else
    echo "⚠ GPU libraries not found at $CUDNN_PATH"
    echo "  Running in CPU mode"
fi

# Also export for use with voice-response
export voice_response_use_gpu=true
