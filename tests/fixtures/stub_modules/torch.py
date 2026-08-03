"""Stub for auto_split_scenes_v1_stage3_export.py's `import torch`.

Only `torch.cuda.empty_cache()` is called anywhere in that script, and only
inside `transcribe_with_timestamps()` — never at module level — so the stub
only needs to survive the bare `import torch` and provide a `cuda` namespace
with `empty_cache()`. Like the whisperx stub, calling it marks the same
sentinel and raises, in case it is ever reached.
"""
import os
import types


def _empty_cache():
    sentinel = os.environ.get("WHISPERX_STUB_SENTINEL")
    if sentinel:
        with open(sentinel, "w", encoding="utf-8") as f:
            f.write("torch.cuda.empty_cache() was called\n")
    raise RuntimeError(
        "torch.cuda.empty_cache() was called — transcription must never be "
        "reached when the channel assignment was refused")


cuda = types.SimpleNamespace(empty_cache=_empty_cache)
