# PyInstaller recipe for the installed app.  Build from the repo root:
#     python packaging/make_icon.py
#     pyinstaller packaging/whisper_transcriber.spec --noconfirm
# Windows: put ffmpeg.exe and ffprobe.exe in packaging/ffmpeg first and they're included.
# Output: dist/Whisper Transcriber/ (Windows) or dist/Whisper Transcriber.app (Mac).
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

sys.path.insert(0, os.path.abspath(os.path.join(SPECPATH, "..")))
from transcriber import __version__  # noqa: E402

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
MAC = sys.platform == "darwin"

datas, binaries, hidden = [], [], ["transcriber"]
for pkg in ("tkinterdnd2", "sv_ttk"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h
datas += collect_data_files("faster_whisper")          # the voice-activity model it ships with
# The speaker detection add-on runs this file in its own Python, so it's shipped as a plain file too.
datas.append((os.path.join(ROOT, "transcriber", "diarize_worker.py"), "transcriber"))
datas.append((os.path.join(ROOT, "transcriber", "nvidia_worker.py"), "transcriber"))   # (runs in the add-on's Python)
binaries += collect_dynamic_libs("ctranslate2")
hidden += ["vlc", "huggingface_hub", "tokenizers", "av", "onnxruntime", "certifi", "soundcard"]
if sys.platform == "win32":
    hidden += ["proctap", "proctap._native", "proctap.backends.windows"]   # recording one app's sound

ffmpeg_dir = os.path.join(SPECPATH, "ffmpeg")
if os.path.isdir(ffmpeg_dir):
    for name in os.listdir(ffmpeg_dir):
        binaries.append((os.path.join(ffmpeg_dir, name), "ffmpeg"))

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["torch", "whisper", "pyannote", "torchaudio", "lightning", "matplotlib", "tensorflow", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = os.path.join(SPECPATH, "icon.icns" if MAC else "icon.ico")
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Whisper Transcriber",
    console=False,
    icon=icon if os.path.exists(icon) else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Whisper Transcriber")
if MAC:
    app = BUNDLE(
        coll,
        name="Whisper Transcriber.app",
        icon=icon if os.path.exists(icon) else None,
        bundle_identifier="com.vsblanco.whispertranscriber",
        info_plist={
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": "Recording voice samples so the app can recognize people.",
            "CFBundleDocumentTypes": [{"CFBundleTypeName": "Media", "CFBundleTypeRole": "Viewer",
                                       "LSItemContentTypes": ["public.movie", "public.audio"]}],
        },
    )
