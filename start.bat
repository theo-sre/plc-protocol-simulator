@echo off
REM Lance le simulateur. Installe les dependances au premier demarrage.
cd /d "%~dp0"

python -c "import yaml, pymodbus, asyncua" 2>nul
if errorlevel 1 (
    echo Installation des dependances...
    python -m pip install -r requirements.txt || goto :err
)

python main.py %*
goto :eof

:err
echo.
echo Echec de l'installation. Python 3.10+ est-il installe ?
echo   winget install --id Python.Python.3.12 -e
pause
