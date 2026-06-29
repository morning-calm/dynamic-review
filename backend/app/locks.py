"""Process-wide locks. The Whisper/CTranslate2 model is a singleton sharing finite
GPU/VRAM, so every transcription is serialized behind one lock (plan: 'one
GPU/Whisper lock')."""

import threading

WHISPER_LOCK = threading.Lock()
