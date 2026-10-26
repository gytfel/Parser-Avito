@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Запуск на Windows: двойной клик по файлу или "start.bat" в терминале.
rem Можно передать команду: start.bat scan -c phones -q "iphone 13"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :no_python
%PY% -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul || goto :no_python

if not exist ".venv\Scripts\python.exe" (
    echo Первый запуск: создаю окружение в папке .venv ...
    %PY% -m venv .venv || goto :fail
)

rem Зависимости ставятся при первом запуске и после изменения requirements.txt
fc /b requirements.txt .venv\requirements.installed >nul 2>nul
if errorlevel 1 (
    echo Устанавливаю зависимости, это займёт пару минут ...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail
    copy /y requirements.txt .venv\requirements.installed >nul
)

set "AVITO_FLIP_CMD=start.bat"
".venv\Scripts\python.exe" -m avito_flip %*
set "CODE=%errorlevel%"
rem При запуске двойным кликом окно не закрывается сразу
if "%~1"=="" pause
exit /b %CODE%

:no_python
echo Не найден Python 3.10 или новее.
echo Скачайте его с https://www.python.org/downloads/ и при установке
echo отметьте галочку "Add python.exe to PATH", затем запустите start.bat снова.
pause
exit /b 1

:fail
echo Не удалось подготовить окружение. Проверьте интернет и запустите start.bat снова.
echo Если ошибка повторяется, удалите папку .venv и попробуйте ещё раз.
pause
exit /b 1
