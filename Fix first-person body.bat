@echo off
rem Drag one or more save folders onto this file.
where py >nul 2>nul && (py -3 "%~dp0fpp_body_fix.py" %*) || (python "%~dp0fpp_body_fix.py" %*)
echo.
pause
