@echo off
rem 프로젝트의 POSIX sh 스크립트를 Windows 셸에서 실행한다.
rem Git for Windows 의 sh.exe 는 보통 PATH 에 없으므로 git 실행 경로에서 찾는다.
setlocal
set "SH="
for %%I in (sh.exe) do if not defined SH set "SH=%%~$PATH:I"
if not defined SH (
  for /f "delims=" %%I in ('git --exec-path 2^>nul') do set "GITEXEC=%%I"
  if defined GITEXEC if exist "%GITEXEC%\..\..\..\usr\bin\sh.exe" set "SH=%GITEXEC%\..\..\..\usr\bin\sh.exe"
)
if not defined SH if exist "%ProgramFiles%\Git\usr\bin\sh.exe" set "SH=%ProgramFiles%\Git\usr\bin\sh.exe"
if not defined SH if exist "%LOCALAPPDATA%\Programs\Git\usr\bin\sh.exe" set "SH=%LOCALAPPDATA%\Programs\Git\usr\bin\sh.exe"
if not defined SH (
  echo [error] POSIX sh 를 찾을 수 없습니다. Git for Windows 를 설치하세요. 1>&2
  exit /b 1
)
rem cmd 에서 띄운 sh 는 Git Bash 프로필을 거치지 않아 coreutils 가 PATH 에 없다.
rem uname·dirname·cygpath 없이는 스크립트가 첫 줄에서 멈춘다.
for %%I in ("%SH%") do set "SHDIR=%%~dpI"
set "GITROOT=%SHDIR%..\.."
set "PATH=%SHDIR%;%GITROOT%\mingw64\bin;%GITROOT%\cmd;%PATH%"

set "TARGET=%~1"
shift
rem shift 이후에도 %* 는 그대로이므로 남은 인자를 직접 모은다.
set "REST="
:collect
if "%~1"=="" goto run
set "REST=%REST% "%~1""
shift
goto collect
:run
"%SH%" "%TARGET%" %REST%
exit /b %ERRORLEVEL%
