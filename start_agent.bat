@echo off
title DVR Local Agent
color 0A
echo ============================================
echo   DVR Local Agent - Iniciando...
echo ============================================
echo.

:: Verifica se Python esta instalado
python --version >nul 2>&1
if errorlevel 1 (
    echo ERRO: Python nao encontrado.
    echo Instale o Python em https://python.org e tente novamente.
    pause
    exit /b 1
)

:: Instala dependencias se necessario
echo Verificando dependencias...
pip install flask requests --quiet

echo.
echo Abrindo interface no navegador...
echo Para encerrar, feche esta janela ou pressione Ctrl+C
echo.

python agent_ui.py

pause
