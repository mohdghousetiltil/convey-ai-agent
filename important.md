powershell -ExecutionPolicy Bypass -File .\build_desktop.ps1

 & "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" "/Odist\installer" "/DMyAppVersion=0.0.213" ".\installer\ConveyAgent.iss"