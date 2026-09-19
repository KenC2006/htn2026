@echo off
rem Parity console. Add this folder to PATH, then type: parity
set "PYTHONPATH=%~dp0..;%PYTHONPATH%"
set "PYTHONUTF8=1"
"%~dp0..\.venv-swarm\Scripts\python.exe" -m parity %*
