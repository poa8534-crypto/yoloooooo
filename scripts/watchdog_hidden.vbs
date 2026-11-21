' Runs watchdog.ps1 with no visible window.
'
' Task Scheduler starting powershell.exe directly flashes a console every five
' minutes: Windows draws the console before -WindowStyle Hidden takes effect,
' and python.exe / curl.exe started by the script can flash their own. wscript
' has no console, and Run with window style 0 creates PowerShell's console
' hidden from the start, which the child programs then share.
'
' The exit code of watchdog.ps1 is passed through so the task's "last result"
' still shows an outage.
Dim shell, fso, here, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & here & "\watchdog.ps1"""
WScript.Quit shell.Run(cmd, 0, True)
