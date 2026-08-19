' Launches run_tunnel_watch.cmd with no visible window (Task Scheduler flashes a
' console when it runs a .cmd in the interactive session; wscript does not).
Set sh = CreateObject("WScript.Shell")
sh.Run """" & CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\run_tunnel_watch.cmd""", 0, False
