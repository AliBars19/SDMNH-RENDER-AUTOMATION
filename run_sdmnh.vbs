' SDMNH Automation — Silent Background Launcher
' ================================================
' This file runs automation.py completely invisibly in the background.
' No console window, no taskbar entry, nothing visible.
'
' HOW TO INSTALL (no admin required):
'   Run setup_startup.ps1  — it copies this file to your Startup folder.
'   After that, every time you log in to Windows this fires automatically.
'
' HOW TO REMOVE:
'   Delete the shortcut from:
'   C:\Users\<you>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup

Option Explicit

Dim objShell, objFSO
Dim strDir, strPythonw, strCmd

Set objShell = CreateObject("WScript.Shell")
Set objFSO   = CreateObject("Scripting.FileSystemObject")

' Project directory — update this if you move the folder
strDir = "C:\Users\aliba\Downloads\Apollova\SDMNH-RENDER-AUTOMATION"

' Find pythonw.exe (no-console Python launcher).
' Tries PATH first, then common per-user install location.
strPythonw = FindPythonw()

If strPythonw = "" Then
    ' Last resort: show a brief error so the user knows something is wrong
    MsgBox "SDMNH: Could not locate pythonw.exe." & vbCrLf & _
           "Make sure Python 3.10+ is installed and in your PATH.", _
           vbExclamation, "SDMNH Automation"
    WScript.Quit 1
End If

' Build the command — window style 0 = fully hidden, False = don't wait
strCmd = """" & strPythonw & """ """ & strDir & "\automation.py"""
objShell.CurrentDirectory = strDir
objShell.Run strCmd, 0, False

' ── Helper: locate pythonw.exe ────────────────────────────────────────────────
Function FindPythonw()
    Dim strResult, oExec

    ' 1. Ask the shell (covers PATH, conda envs, pyenv, etc.)
    On Error Resume Next
    Set oExec = objShell.Exec("cmd /c where pythonw.exe 2>nul")
    If Err.Number = 0 Then
        strResult = Trim(Split(oExec.StdOut.ReadAll(), vbCrLf)(0))
        If objFSO.FileExists(strResult) Then
            FindPythonw = strResult
            Exit Function
        End If
    End If
    On Error GoTo 0

    ' 2. Common per-user Windows Store / python.org install paths
    Dim arrPaths, p
    arrPaths = Array( _
        objShell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python313\pythonw.exe", _
        objShell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe", _
        objShell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python311\pythonw.exe", _
        objShell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python310\pythonw.exe", _
        "C:\Python313\pythonw.exe", _
        "C:\Python312\pythonw.exe", _
        "C:\Python311\pythonw.exe", _
        "C:\Python310\pythonw.exe" _
    )

    For Each p In arrPaths
        If objFSO.FileExists(p) Then
            FindPythonw = p
            Exit Function
        End If
    Next

    FindPythonw = ""
End Function
