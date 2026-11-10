@echo off
rem REELFORGE scheduled-task launcher: reelforge-task.cmd <command> [slot]
rem Keeps the Task Scheduler command line short; logs to output\logs\<command>-<slot>.log
setlocal
set ROOT=%~dp0..
cd /d "%ROOT%"
if not exist "output\logs" mkdir "output\logs"
set NAME=%1
if not "%2"=="" set NAME=%1-%2
set PYTHONUTF8=1
if "%2"=="" (
  ".venv\Scripts\python.exe" -m services.scheduler.daily %1 >> "output\logs\%NAME%.log" 2>&1
) else (
  ".venv\Scripts\python.exe" -m services.scheduler.daily %1 --slot %2 >> "output\logs\%NAME%.log" 2>&1
)
endlocal
