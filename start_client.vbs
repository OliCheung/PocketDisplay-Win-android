' PocketDisplay - Windows Control Client (windowless launcher)
' Double-click this file: it starts the client with NO console window and no
' cmd.exe flash (wscript runs without a console of its own).

Option Explicit

Dim fso, shell, scriptDir, cmd
Set fso   = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

shell.CurrentDirectory = scriptDir

' pythonw.exe runs the script without allocating a console window.
cmd = "pythonw """ & scriptDir & "\windows_client_web.py"""

' 1 = SW_SHOWNORMAL. Do NOT use 0 here: the style is inherited by the child's
' STARTUPINFO and pywebview's first Show() would honor it, leaving the window
' hidden (the client shows no console either way, since we use pythonw).
shell.Run cmd, 1, False
