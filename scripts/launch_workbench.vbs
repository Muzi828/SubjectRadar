Option Explicit
Dim fso, shell, scriptDir, root, ps1, command, code
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(scriptDir)
ps1 = fso.BuildPath(root, "scripts\start_workbench.ps1")
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """"
code = shell.Run(command, 0, True)
If code <> 0 Then
  MsgBox "SafeLens failed to start. See logs\server.stderr.log for details.", 16, "SafeLens"
End If
