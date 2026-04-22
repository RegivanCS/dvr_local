@echo off
setlocal
cd /d "%~dp0..\.."
chcp 65001 >nul

echo ============================================
echo   DVR Local — Build Windows v1.1
echo ============================================
echo.

:: ── 1. Verificar Python ──────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo ERRO: Python não encontrado. Instale Python 3.11+ e marque "Add to PATH".
    pause & exit /b 1
)
echo [OK] Python encontrado.

:: ── 2. Criar/atualizar ambiente virtual ─────────────────────────────────────
if not exist ".venv" (
    echo [1/5] Criando ambiente virtual...
    python -m venv .venv
)
call .venv\Scripts\activate.bat

echo [2/5] Instalando dependências...
pip install -q -r requirements.txt
pip install -q pyinstaller

:: ── 3. Gerar executável com PyInstaller ─────────────────────────────────────
echo [3/5] Compilando com PyInstaller...
pyinstaller ^
    --name dvr_launcher ^
    --onedir ^
    --windowed ^
    --icon build\windows\assets\dvr_icon.ico ^
    --add-data "app.py;." ^
    --add-data "rtsp_proxy.py;." ^
    --add-data "tunnel_relay.py;." ^
    --add-data "motion_recorder.py;." ^
    --add-data "recordings_relay.py;." ^
    --add-data "cameras_config.json;." ^
    --add-data "requirements.txt;." ^
    --hidden-import flask ^
    --hidden-import webview ^
    --hidden-import pystray ^
    --hidden-import PIL ^
    --hidden-import cv2 ^
    --noconfirm ^
    dvr_launcher.py

if errorlevel 1 (
    echo ERRO: PyInstaller falhou.
    pause & exit /b 1
)
echo [OK] Executável gerado em dist\dvr_launcher\

:: ── 4. Copiar arquivos extras para a pasta dist ──────────────────────────────
echo [4/5] Copiando scripts Python para dist...
for %%f in (app.py rtsp_proxy.py tunnel_relay.py motion_recorder.py recordings_relay.py cameras_config.json) do (
    if exist "%%f" copy /Y "%%f" "dist\dvr_launcher\" >nul
)
if not exist "dist\dvr_launcher\logs" mkdir "dist\dvr_launcher\logs"
if not exist "dist\dvr_launcher\recordings" mkdir "dist\dvr_launcher\recordings"

:: ── 5. Gerar instalador com Inno Setup (se disponível) ───────────────────────
echo [5/5] Gerando instalador...
set ISCC=
for %%p in (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    "C:\Program Files\Inno Setup 6\ISCC.exe"
) do (
    if exist %%p set ISCC=%%p
)

if defined ISCC (
    %ISCC% build\windows\installer.iss
    echo [OK] Instalador gerado em dist\DVR_Local_Setup_v1.1.exe
) else (
    echo [AVISO] Inno Setup não encontrado. Instale em: https://jrsoftware.org/isinfo.php
    echo         A pasta portátil está disponível em: dist\dvr_launcher\
)

echo.
echo ============================================
echo   Build concluído!
echo   Portátil:   dist\dvr_launcher\dvr_launcher.exe
if defined ISCC echo   Instalador: dist\DVR_Local_Setup_v1.1.exe
echo ============================================
pause
