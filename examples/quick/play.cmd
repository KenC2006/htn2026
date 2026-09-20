@echo off
rem Plays one quick demo.   quick\play python | c | arkts        (run from the demo folder; about 40 to 50 seconds each)
rem python = a real run, cut down.  c and arkts = scripted replays of real code and real checker results (the screen says so).
set SPEED=3.5
if /i "%1"=="c" set SPEED=2
if /i "%1"=="arkts" set SPEED=1.8
if "%1"=="" (echo usage: quick\play python ^| c ^| arkts & exit /b 1)
cd /d "%~dp0.."
call parity watch quick-%1 --replay --speed %SPEED%
