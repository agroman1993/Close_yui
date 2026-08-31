' Lanza la consolidacion de la memoria, sin ventana.
'
' Pensado para que el programador de tareas lo dispare cada hora y al
' iniciar sesion. Casi siempre el script de Python mira el reloj y la
' marca, ve que no toca, y se va en milisegundos. Eso es lo normal y es
' barato.
'
' Quien decide si toca es consolidar_memoria.py, NO el programador de
' tareas. Ese es el punto entero del montaje: una tarea fijada a una hora
' en punto se pierde el dia que el ordenador no esta encendido a esa hora;
' con la condicion en el script, un dia apagado no salta el turno, solo lo
' retrasa hasta el siguiente arranque.
'
' En VBScript por lo mismo que el guardian: con wscript.exe no aparece
' ninguna consola negra, y esto se dispara cada hora.

Option Explicit

Dim fso, carpeta, sh, log

Set fso = CreateObject("Scripting.FileSystemObject")
carpeta = fso.GetParentFolderName(WScript.ScriptFullName)
log = carpeta & "\consolidacion.log"

Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = carpeta
sh.Run "cmd /c chcp 65001 >nul && python """ & carpeta & _
       "\herramientas\consolidar_memoria.py"" >> """ & log & """ 2>&1", 0, False
WScript.Quit 0
