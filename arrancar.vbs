' Lanza arrancar.cmd sin que aparezca ninguna ventana.
' El 0 es el estilo de ventana (oculta); el False significa no esperar.
' Hace falta porque la tarea necesita un shell para redirigir al log, y
' cmd.exe por si solo abriria una consola negra en el escritorio.
CreateObject("WScript.Shell").Run "cmd /c """ & _
  CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & _
  "\arrancar.cmd""", 0, False
