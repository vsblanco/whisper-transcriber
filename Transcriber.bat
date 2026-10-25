@echo off
rem Opens the Whisper Transcriber window. Files dropped onto this .bat are added to the queue.
cd /d "%~dp0"
where pythonw >nul 2>nul
if %ERRORLEVEL%==0 (
    start "" pythonw "%~dp0app.py" %*
) else (
    start "" python "%~dp0app.py" %*
)
