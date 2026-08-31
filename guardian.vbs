' Guardian de Close Yui.
'
' Mira si ya hay un "python main.py" corriendo. Si lo hay, no hace nada;
' si no, la arranca con arrancar.cmd.
'
' Existe para que una caida no se quede sin nadie: si el proceso que
' sostiene al bot muere (una terminal cerrada, una reinstalacion, un
' logout), el guardian lo levanta solo en el siguiente disparo.
'
' Va en VBScript y no en PowerShell ni en un .cmd por una razon concreta:
' con wscript.exe no aparece NINGUNA ventana. Un .cmd programado abre una
' consola negra cada vez que se dispara, y esto esta pensado para
' dispararse cada pocos minutos.
'
' Es idempotente a proposito: la tarea programada puede repetirse cada
' cinco minutos sin miedo a levantar dos copias a la vez, porque comprueba
' antes.
'
' Para activarlo: Programador de tareas -> nueva tarea, desencadenador
' "al iniciar sesion" y repeticion cada 5 minutos, accion:
' wscript.exe "<ruta>\guardian.vbs"

Option Explicit

Dim fso, carpeta, wmi, procesos, p, cmdline, sh

Set fso = CreateObject("Scripting.FileSystemObject")
carpeta = fso.GetParentFolderName(WScript.ScriptFullName)

' Ya esta viva? Se busca "main.py" en la linea de comandos de cualquier python.
On Error Resume Next
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
If Err.Number <> 0 Then WScript.Quit 0        ' sin WMI no se arriesga a duplicar
On Error GoTo 0

Set procesos = wmi.ExecQuery( _
    "SELECT CommandLine FROM Win32_Process WHERE Name = 'python.exe'")

For Each p In procesos
    cmdline = p.CommandLine
    If Not IsNull(cmdline) Then
        If InStr(1, cmdline, "main.py", vbTextCompare) > 0 Then
            WScript.Quit 0                     ' ya esta corriendo: no se toca
        End If
    Next
End If

' No esta. Se arranca igual que arrancar.vbs: consola oculta y sin esperar.
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c """ & carpeta & "\arrancar.cmd""", 0, False
WScript.Quit 0
