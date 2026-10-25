@echo off
title Transcribing...
rem Drag and drop one or more video/audio files onto this .bat to transcribe them
rem without opening the app. Transcripts go to Documents\Transcripts.
echo ============================================================
echo   Whisper Transcriber (medium model)
echo ============================================================
echo.

if "%~1"=="" (
    echo ERROR: No file provided.
    echo Drag and drop a video or audio file onto this .bat file.
    echo.
    pause
    exit /b 1
)

cd /d "%~dp0"
python "%~dp0transcribe_video.py" %* --model medium

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ============================================================
    echo   Something failed. Make sure Python, ffmpeg and the packages
    echo   in requirements.txt are installed.
    echo ============================================================
)
echo.
pause
