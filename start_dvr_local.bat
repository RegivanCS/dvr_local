@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

title DVR Stack Launcher
color 0A
echo ============================================
echo   DVR Stack - Inicializacao Completa
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
	echo ERRO: Python nao encontrado no PATH.
	echo Instale Python e marque a opcao "Add Python to PATH".
	pause
	exit /b 1
)

if not exist "logs" mkdir "logs"

echo [1/4] Iniciando rtsp_proxy.py ...
start "rtsp_proxy" cmd /k "cd /d %CD% && python rtsp_proxy.py 1>>logs\rtsp_proxy.log 2>>&1"

echo [2/4] Iniciando tunnel_relay.py ...
start "tunnel_relay" cmd /k "cd /d %CD% && python tunnel_relay.py 1>>logs\tunnel_relay.log 2>>&1"

echo [3/4] Iniciando motion_recorder.py ...
start "motion_recorder" cmd /k "cd /d %CD% && python motion_recorder.py 1>>logs\motion_recorder.log 2>>&1"

echo [4/4] Iniciando recordings_relay.py ...
start "recordings_relay" cmd /k "cd /d %CD% && python recordings_relay.py 1>>logs\recordings_relay.log 2>>&1"

echo.
echo Servicos iniciados em janelas separadas.
echo Aguarde 10-20 segundos para os tunnels subirem.
echo.
echo Logs em: %CD%\logs\
echo.
pause
