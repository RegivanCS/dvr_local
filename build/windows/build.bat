@echo off
setlocal
cd /d "%~dp0..\.."
chcp 65001 >nul

set ICON_FILE=build\windows\assets\dvr_icon.ico
set ISS_FILE=build\windows\installer.iss
set REQ_BUILD_CORE=build\windows\requirements-build-core.txt
set REQ_BUILD_OPTIONAL=build\windows\requirements-build-optional.txt

echo ============================================
echo   DVR Local — Build Windows v1.1
echo ============================================
echo.

:: ── 0. Pré-validações de arquivos obrigatórios ──────────────────────────────
if not exist "%ISS_FILE%" (
    echo ERRO: Arquivo do Inno Setup nao encontrado: %ISS_FILE%
    pause & exit /b 1
)

for %%f in (dvr_launcher.py app.py rtsp_proxy.py tunnel_relay.py motion_recorder.py recordings_relay.py requirements.txt) do (
    if not exist "%%f" (
        echo ERRO: Arquivo obrigatorio ausente: %%f
        pause & exit /b 1
    )
)

for %%f in (%REQ_BUILD_CORE% %REQ_BUILD_OPTIONAL%) do (
    if not exist "%%f" (
        echo ERRO: Arquivo de requisitos do build ausente: %%f
        pause & exit /b 1
    )
)

if not exist "build\windows\assets" mkdir "build\windows\assets"

:: Fallback: gera um .ico simples caso o icone oficial nao exista.
if not exist "%ICON_FILE%" (
    echo [INFO] Icone nao encontrado. Gerando fallback em %ICON_FILE% ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName System.Drawing; $bmp = New-Object System.Drawing.Bitmap 256,256; $g=[System.Drawing.Graphics]::FromImage($bmp); $g.SmoothingMode='HighQuality'; $g.Clear([System.Drawing.Color]::FromArgb(24,24,24)); $bg = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(76,175,80)); $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(200,255,200),8); $g.FillRectangle($bg,28,70,150,110); $g.DrawRectangle($pen,28,70,150,110); $poly = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(56,142,60)); $pts = @((New-Object System.Drawing.Point 178,86),(New-Object System.Drawing.Point 232,62),(New-Object System.Drawing.Point 232,188),(New-Object System.Drawing.Point 178,164)); $g.FillPolygon($poly,$pts); $l1 = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(20,20,20)); $l2 = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(129,199,132)); $g.FillEllipse($l1,62,100,70,70); $g.FillEllipse($l2,80,118,34,34); $hIcon = $bmp.GetHicon(); $icon = [System.Drawing.Icon]::FromHandle($hIcon); $fs = [System.IO.File]::Create('%ICON_FILE%'); $icon.Save($fs); $fs.Close(); $g.Dispose(); $bmp.Dispose(); [System.Runtime.InteropServices.Marshal]::Release($hIcon) | Out-Null"
)

if not exist "%ICON_FILE%" (
    echo ERRO: Falha ao criar/obter icone em %ICON_FILE%
    pause & exit /b 1
)

:: ── 1. Verificar Python ──────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo ERRO: Python não encontrado. Instale Python 3.11+ e marque "Add to PATH".
    pause & exit /b 1
)
echo [OK] Python encontrado.

:: Valida versao minima do Python (3.11+)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
    echo ERRO: Python 3.11+ necessario para este build.
    python --version
    pause & exit /b 1
)
echo [OK] Python 3.11+ confirmado.

:: ── 2. Criar/atualizar ambiente virtual ─────────────────────────────────────
if not exist ".venv" (
    echo [1/5] Criando ambiente virtual...
    python -m venv .venv
)

set VENV_PY=.venv\Scripts\python.exe
set VENV_PIP=%VENV_PY% -m pip

echo [2/5] Atualizando ferramentas de empacotamento...
%VENV_PIP% install -q --upgrade pip setuptools wheel
if errorlevel 1 (
    echo [AVISO] Falha ao atualizar pip/setuptools/wheel. Continuando...
)

echo [2/5] Instalando dependencias criticas do build...
%VENV_PIP% install -q -r %REQ_BUILD_CORE%
if errorlevel 1 (
    echo ERRO: Falha ao instalar dependencias criticas do build.
    pause & exit /b 1
)

echo [2/5] Instalando dependencias opcionais do build...
%VENV_PIP% install -q -r %REQ_BUILD_OPTIONAL%
if errorlevel 1 (
    echo [AVISO] Algumas dependencias opcionais falharam, por exemplo pythonnet ou pywebview. Continuando...
)

%VENV_PY% -c "import flask, requests, PIL, cv2; print('deps-ok')"
if errorlevel 1 (
    echo ERRO: Dependencias criticas nao estao importaveis no ambiente virtual.
    pause & exit /b 1
)

:: ── 3. Gerar executável com PyInstaller ─────────────────────────────────────
echo [3/5] Compilando com PyInstaller...
%VENV_PY% -m PyInstaller ^
    --name dvr_launcher ^
    --onedir ^
    --windowed ^
    --icon %ICON_FILE% ^
    --add-data "app.py;." ^
    --add-data "rtsp_proxy.py;." ^
    --add-data "tunnel_relay.py;." ^
    --add-data "motion_recorder.py;." ^
    --add-data "recordings_relay.py;." ^
    --add-data "cameras_config.json;." ^
    --add-data "requirements.txt;." ^
    --hidden-import webview ^
    --hidden-import pystray ^
    --hidden-import PIL ^
    --noconfirm ^
    dvr_launcher.py

if errorlevel 1 (
    echo ERRO: PyInstaller falhou.
    pause & exit /b 1
)
if not exist "dist\dvr_launcher\dvr_launcher.exe" (
    echo ERRO: Executavel nao encontrado apos PyInstaller: dist\dvr_launcher\dvr_launcher.exe
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
    %ISCC% %ISS_FILE%
    if errorlevel 1 (
        echo ERRO: Inno Setup falhou ao compilar %ISS_FILE%
        pause & exit /b 1
    )
    if not exist "dist\DVR_Local_Setup_v1.1.exe" (
        echo ERRO: Instalador nao encontrado em dist\DVR_Local_Setup_v1.1.exe
        pause & exit /b 1
    )
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
