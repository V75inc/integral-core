"""Offline host speech fallback for the exact private review prompt."""

import asyncio
import platform
import shutil
import tempfile
import wave
from pathlib import Path

_MAX_AUDIO_BYTES = 6_000_000


async def _run_local_speech(*command: str, text: str | None = None) -> bool:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=(
            asyncio.subprocess.PIPE if text is not None else asyncio.subprocess.DEVNULL
        ),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(10):
            await process.communicate(text.encode() if text is not None else None)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return process.returncode == 0


async def synthesize_review_audio(text: str) -> bytes | None:
    """Produce one bounded WAV using a trusted, installed local synthesizer.

    No shell, user-chosen executable, cloud speech service or persistent audio.
    """
    if not 0 < len(text) <= 2600:
        return None
    with tempfile.TemporaryDirectory(prefix="integral-review-") as folder:
        output = Path(folder) / "prompt.wav"
        if platform.system() == "Darwin":
            source = Path(folder) / "prompt.aiff"
            if not await _run_local_speech(
                "/usr/bin/say", "-v", "Samantha", "-o", str(source), text=text
            ):
                return None
            if not await _run_local_speech(
                "/usr/bin/afconvert",
                "-f",
                "WAVE",
                "-d",
                "LEI16@24000",
                str(source),
                str(output),
            ):
                return None
        else:
            executable = shutil.which("espeak-ng") or shutil.which("espeak")
            if not executable or not await _run_local_speech(
                executable,
                "-v",
                "en",
                "-s",
                "155",
                "-w",
                str(output),
                "--stdin",
                text=text,
            ):
                return None
        if not output.is_file() or not 44 < output.stat().st_size <= _MAX_AUDIO_BYTES:
            return None
        with wave.open(str(output), "rb") as wav:
            if (
                wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or not 8000 <= wav.getframerate() <= 48000
            ):
                return None
        return output.read_bytes()
