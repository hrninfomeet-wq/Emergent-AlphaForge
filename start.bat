@echo off
REM AlphaForge compatibility launcher (Windows).
REM The detailed startup assistant lives in start-app.bat.
cd /d "%~dp0"
call "%~dp0start-app.bat" %*
