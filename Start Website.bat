@echo off
REM Starts Card Print Studio and opens it in your browser. Close this window to stop it.
cd /d "%~dp0"
title Card Print Studio
python -c "import flask, pymupdf, cv2, numpy, PIL, uharfbuzz, freetype" 2>nul || python -m pip install -r requirements.txt
python app.py
pause
