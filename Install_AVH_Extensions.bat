@echo off
setlocal enableextensions
title AVH Revit tools installer

rem ------------------------------------------------------------------
rem  Installs or updates the AVH Revit tools extension for pyRevit.
rem
rem  Hands the work to the pyRevit CLI, which ships with pyRevit itself:
rem
rem    first run  ->  pyrevit extend ui AVH <repo> --branch=main
rem    later runs ->  pyrevit extensions update AVH
rem
rem  Because the extension is installed as a git clone, pyRevit's own
rem  Update tool can also keep it current without this file.
rem
rem  No admin rights needed. Everything lands in %APPDATA%.
rem  Revit may stay open, but pyRevit needs a Reload afterwards.
rem ------------------------------------------------------------------

set "EXTNAME=AVH"
set "REPO=https://github.com/AVH-bjornt/AVH.extension.git"
set "BRANCH=main"
set "PYREVIT=https://github.com/pyrevitlabs/pyRevit/releases"


set "TARGET=%APPDATA%\pyRevit\Extensions\AVH.extension"

echo.
echo   AVH Revit tools
echo   ==================================================
echo.

if "%APPDATA%"=="" goto no_appdata

rem --- pyRevit and its CLI have to be there --------------------------
where pyrevit >NUL 2>&1
if errorlevel 1 goto no_pyrevit

rem --- note whether Revit is up, but do not stand in the way ---------
set "REVIT_OPEN="
tasklist /FI "IMAGENAME eq Revit.exe" 2>NUL | find /I "Revit.exe" >NUL
if not errorlevel 1 set "REVIT_OPEN=1"

rem --- install, update, or replace ------------------------------------
if exist "%TARGET%\.git\" goto do_update
if exist "%TARGET%" goto replace_old
goto do_install

:replace_old
echo   An older copy is installed that did not come from GitHub.
echo   Replacing it so that future updates work...
rmdir /s /q "%TARGET%"
goto do_install

:do_update
echo   Updating...
pyrevit extensions update %EXTNAME%
if errorlevel 1 goto retry_clean
goto report

:retry_clean
echo   The update did not go through. Reinstalling from scratch...
rmdir /s /q "%TARGET%"
goto do_install

:do_install
echo   Installing...
pyrevit extend ui %EXTNAME% "%REPO%" --branch=%BRANCH%
if not exist "%TARGET%\extension.yaml" goto install_failed

:report

set "VER=unknown"
for /f "tokens=2 delims==" %%v in ('findstr /b "__version__" "%TARGET%\lib\avh_schedules\__init__.py" 2^>NUL') do set "VER=%%v"
set VER=%VER: =%
set VER=%VER:"=%

echo.
echo   ==================================================
echo   Done. Version %VER% is installed.
echo.
echo   Installed to:
echo   %TARGET%
echo.
if defined REVIT_OPEN goto finish_revit_open

echo   Start Revit. You will find the tool on the AVH tab,
echo   under Schedules.
goto finish

:finish_revit_open
echo   Revit is open, so this version is not loaded yet.
echo   Go to the pyRevit tab and click Reload, or restart Revit.
echo.
echo   The tool is then on the AVH tab, under Schedules.

:finish
echo   ==================================================
echo.
pause
endlocal
exit /b 0


rem ================= things that can go wrong =========================

:no_appdata
echo   Windows did not tell this script where your AppData folder is,
echo   which should never happen. Nothing was changed.
goto fail

:no_pyrevit
echo   The pyrevit command was not found on this computer.
echo.
echo   This tool is an add on for pyRevit, so pyRevit has to be
echo   installed first. It is free, and its installer provides the
echo   pyrevit command this script needs.
echo.
echo     1. Open  %PYREVIT%
echo     2. Download and run the newest installer.
echo     3. Sign out of Windows and back in, or restart the computer.
echo     4. Run this file again.
echo.
echo   If pyRevit is already installed, step 3 is almost certainly
echo   what is missing.
echo.
echo   Nothing was changed.
goto fail

:install_failed
echo.
echo   The install did not finish. The message above from pyrevit
echo   says why.
echo.
echo   Two common ones:
echo.
echo     reference 'refs/remotes/origin/...' not found
echo       the branch name is wrong, tell Bjorn
echo.
echo     anything mentioning LibGit2Sharp
echo       a known bug in some pyRevit versions, tell Bjorn and he
echo       will send you the older installer instead
echo.
goto fail

:fail
echo.
pause
endlocal
exit /b 1
