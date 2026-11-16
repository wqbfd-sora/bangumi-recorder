@echo off
setlocal
cd /d "%~dp0"
for /f "delims=" %%v in ('python -c "from bangumi_recorder import __version__; print(__version__)"') do set "VER=%%v"
if not defined VER goto :error
set "APP=dist\Bangumi Recorder %VER%"
echo Building Bangumi Recorder %VER%

echo [1/3] Installing dependencies and PyInstaller...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

echo.
echo [2/3] Building, this takes 1-3 minutes...
python -m PyInstaller --noconfirm --clean "Bangumi Recorder.spec"
if errorlevel 1 goto :error

echo.
echo [3/3] Pointing the exe at this project's data folder...
python -c "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')" "%APP%\data_path.txt" "%~dp0data"
if errorlevel 1 goto :error

rem build\ only holds intermediate files; its exe cannot run on its own, so remove it
if exist build rmdir /s /q build

rem Shortcut next to the exe, inside this version's folder
set "LNK=%~dp0%APP%\Bangumi Recorder.lnk"
rem Remove the shortcut that 1.0.12's first build.bat put directly in dist\
if exist "%~dp0dist\Bangumi Recorder.lnk" del "%~dp0dist\Bangumi Recorder.lnk"
set "EXE=%~dp0%APP%\Bangumi Recorder.exe"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath=$env:EXE; $s.WorkingDirectory=(Split-Path $env:EXE); $s.IconLocation=$env:EXE+',0'; $s.Description='Bangumi Recorder '+$env:VER; $s.Save()"
if errorlevel 1 (
    echo Could not create the shortcut. The exe still works: %APP%\Bangumi Recorder.exe
) else (
    echo Shortcut: %APP%\Bangumi Recorder.lnk
)

echo.
echo Done: %APP%\Bangumi Recorder.exe
pause
exit /b 0

:error
echo.
echo Build failed. Send the messages above to Claude.
pause
exit /b 1
