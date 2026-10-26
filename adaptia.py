# CONEXIÓN A LA BASE DE DATOS
#   import sqlite3
#   
#   conexion = sqlite3.connect("adaptia-db")
#   conexion.execute ("PRAGMA foreign_keys = ON")
#   print("Conectado a la base de datos")
#   conexion.close() 



# Función para registrarse
def registrarse(): 
    usuario = input("Ingresa tu nombre de usuario \n> ")
    contrasena = input("Ingresa tu contraseña\n> ")
    confirmacion = input("Confirma tu contraseña porfavor\n> ")

    #Confirmación de contraseña
    if contrasena == confirmacion:
        print("Contraseña es correcta, puede continuar")
    else:
        confirmacion = input("Ingresa tu contraseña nuevamente porfavor\n> ")

    print(f"\n\n*****Bienvenid@ {usuario} configuremos tu perfil*****\n\n")


    #Configuración de rutinas
    cantidad_rutinas = int(input("\n¿Cuantas rutinas tienes? (lo que haces que no puedes cambiar)\n> "))

    for i in range (cantidad_rutinas):
        rutina = input(f"Escribe tu rutina: {i + 1}\n> ")
        hora_inicio = input(f"Escribe la hora de inicio de la rutina: {rutina}\n> ")
        hora_final_decision = int(input(f"¿La rutina: {rutina} tiene horario de fin? \n1.-SI\n2.-NO\n> "))

        if hora_final_decision == 1 :
            hora_final = input(f"Escribe el horario de finalizacion de la rutina: {rutina}\n> ")
        elif hora_final_decision == 2 :
            print("Sigamos")
        else:
            print("Opción no valida")

        rutina = ""
        hora_inicio = ""
        hora_final_decision = ""
        hora_final = ""


    #Configuración de habitos
    cantidad_habitos = int(input("\n¿Cuantos hábitos quieres generar? (como leer, estudiar, etc)\n> "))

    for i in range (cantidad_habitos):
        habito = input(f"Escribe tu habito: {i + 1}\n> ")
        importancia = int(input(f"Del 1 al 10 que tan importante es el hábito: {habito}\n> "))
        habito_inicio = input(f"Escribe la hora de inicio de tu hábito: {habito}\n> ")
        habito_final_decision = int(input(f"¿Tu habito: {habito} tiene hora de fin?\n1.-SI\n2.-NO\n> "))

        if habito_final_decision == 1:
            habito_final = input(f"Escribe la hora de fin de tu hábito: {habito}\n> ")
        elif habito_final_decision == 2:
            print("Sigamos")
        else:
            print("Opción no valida")

        habito = ""
        importancia = ""
        habito_inicio = ""
        habito_final_decision = ""
        habito_final = ""
     


# Función para iniciar sesión
def inicio_sesion():
    usuario = input("Ingresa tu nombre de usuario\n> ")
    contraseña = input("Ingresa una contraseña\n> ")

    print(f"\n\n*****Bienvenid@ {usuario} puedes continuar en tu perfil*****\n\n")


registro = int(input("1.-INICIAR SESIÓN \n2.-REGISTRARSE\n> "))

if registro == 1 :
    inicio_sesion()
elif registro == 2:
    registrarse()
else:
    print("Opción no valida")
