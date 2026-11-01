@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "RH_BUILD_STATUS=1"
set "RH_PROJECT=%~dp0"
set "RH_PYTHON_FILE=%RH_PROJECT%.researchhelper\runtime\build-python-path"
if /I not "%OS%"=="Windows_NT" goto :wrong_platform
where powershell >nul 2>&1
if errorlevel 1 goto :missing_powershell
if defined RESEARCHHELPER_PYTHON goto :selected
if not exist "%RH_PYTHON_FILE%" goto :choose
set /p "RESEARCHHELPER_PYTHON=" < "%RH_PYTHON_FILE%"
if not defined RESEARCHHELPER_PYTHON goto :choose
if not exist "%RESEARCHHELPER_PYTHON%" goto :choose
if exist "%RESEARCHHELPER_PYTHON%\NUL" goto :choose
goto :selected

:choose
set "RESEARCHHELPER_PYTHON="
echo 请选择已有64位Python3.12环境，优先选择MachineLearning。不会创建环境或安装依赖。
set "RH_SELECTION=%TEMP%\researchhelper-python-%RANDOM%-%RANDOM%.txt"
powershell -NoProfile -ExecutionPolicy Bypass -File "%RH_PROJECT%packaging\select_python_environment.ps1" -ListScript "%RH_PROJECT%packaging\list_python_environments.ps1" -OutputFile "%RH_SELECTION%"
set "RH_SELECTION_STATUS=%ERRORLEVEL%"
if "%RH_SELECTION_STATUS%"=="130" goto :selection_cancelled
if not "%RH_SELECTION_STATUS%"=="0" goto :selection_failed
set /p "RESEARCHHELPER_PYTHON=" < "%RH_SELECTION%"
del /q "%RH_SELECTION%" >nul 2>&1

:selected
if not defined RESEARCHHELPER_PYTHON goto :selection_failed
set "RH_PYTHON=%RESEARCHHELPER_PYTHON%"
call :absolute_path
if errorlevel 1 goto :done
if not exist "%RH_PYTHON%" goto :missing_python
if exist "%RH_PYTHON%\NUL" goto :missing_python
cd /d "%RH_PROJECT%"
echo ResearchHelper Windows一键候选构建
echo Python：%RH_PYTHON%
echo 检查锁定依赖、.NET SDK 8和Inno Setup，不联网下载。
echo 校验后更新result\windows-candidate和项目级Skill，不安装App或全局Skill，不覆盖macOS交付或同步现有demo。
"%RH_PYTHON%" -I -B -u "%RH_PROJECT%packaging\build_current.py"
set "RH_BUILD_STATUS=%ERRORLEVEL%"
if not "%RH_BUILD_STATUS%"=="0" goto :done
goto :done

:absolute_path
if "%RH_PYTHON:~0,2%"=="\\" exit /b 0
if not "%RH_PYTHON:~1,1%"==":" goto :invalid_path
if "%RH_PYTHON:~2,1%"=="\" exit /b 0
if "%RH_PYTHON:~2,1%"=="/" exit /b 0
:invalid_path
echo Python路径须为绝对路径。
exit /b 1

:wrong_platform
echo Windows安装包须在Windows x64机器构建。
goto :done
:missing_powershell
echo 未找到Windows PowerShell。
goto :done
:missing_python
echo 已选解释器不存在或不是文件，请重新选择。
goto :done
:selection_failed
if defined RH_SELECTION del /q "%RH_SELECTION%" >nul 2>&1
echo 解释器枚举或读取失败，本次未构建。请查看上方错误。
goto :done
:selection_cancelled
if defined RH_SELECTION del /q "%RH_SELECTION%" >nul 2>&1
set "RH_BUILD_STATUS=130"
echo 已取消解释器选择，本次未构建。
:done
if "%RH_BUILD_STATUS%"=="130" echo 构建已取消，没有发布或安装新版本。
if not "%RH_BUILD_STATUS%"=="0" if not "%RH_BUILD_STATUS%"=="130" echo 构建未完成，没有发布或安装新版本。请检查上方失败阶段与日志路径。
pause
exit /b %RH_BUILD_STATUS%
