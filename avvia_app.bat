@echo off
chcp 65001 > nul
cd /d "G:\Il mio Drive\Viaggi\Bot_Code"

echo 📦 Verifica e installa dipendenze...
pip install -r requirements.txt

echo.
echo 🚀 Avvio app in corso...
echo.

REM Mostra l'IP locale
for /f "delims=" %%a in ('powershell -Command "Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -like '192.168.*'} | Select-Object -ExpandProperty IPAddress | Select-Object -First 1"') do set IP=%%a

if defined IP (
    echo.
    echo ✅ Accedi dal cellulare a: http://%IP%:8501
    echo.
) else (
    echo ⚠️ Non riesco a trovare l'IP. Accedi comunque al localhost.
)

streamlit run app.py --server.address 0.0.0.0 --server.port 8501

pause
