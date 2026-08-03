"""Stub for auto_split_scenes_v1_stage3_export.py's `import whisperx`.

This environment has neither whisperx nor torch installed, and the real
module is imported unconditionally at the top of that script — so any
subprocess test of it needs a stand-in just to get past the import, whether or
not the test cares about transcription itself.

Doubles as the "transcription was never reached" sentinel for the channel-
assignment-refusal regression: any attribute access is a real function
(PEP 562 module-level __getattr__) that, if actually CALLED, marks a sentinel
file (named by the WHISPERX_STUB_SENTINEL env var) and raises — so a refusal
that happens before transcription leaves no sentinel, and a refusal that
happens (bug!) after transcription started would leave one.
"""
import os


def __getattr__(name):
    def _called(*args, **kwargs):
        sentinel = os.environ.get("WHISPERX_STUB_SENTINEL")
        if sentinel:
            with open(sentinel, "w", encoding="utf-8") as f:
                f.write(f"whisperx.{name} was called\n")
        raise RuntimeError(
            f"whisperx.{name}() was called — transcription must never be "
            f"reached when the channel assignment was refused")
    return _called
