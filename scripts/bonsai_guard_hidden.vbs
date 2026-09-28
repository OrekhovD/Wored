<#
.SYNOPSIS
    Invisible wrapper for the WORED bonsai guard scheduled task.

.DESCRIPTION
    Scheduled tasks that run powershell.exe directly flash a console window
    every cycle.  wscript.exe has no console at all, so launching the guard
    through this VBScript wrapper produces zero window activity.  PowerShell
    output is discarded here; bonsai_guard.ps1 already writes everything to
    the log file (logs\bonsai_guard.log) and state JSON.
'VBScript section follows:
#>
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
guard = fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "bonsai_guard.ps1")
If Not fso.FileExists(guard) Then WScript.Quit 1
' 0 = SW_HIDE window style for the spawned powershell.exe
shell.Run "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & guard & """ -Port 8088", 0, False
WScript.Quit 0