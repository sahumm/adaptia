#CONEXIÓN A LA BASE DE DATOS
import sqlite3

conexion = sqlite3.connect("adaptiadb")
conexion.execute ("PRAGMA foreign_keys = ON")
print("Conectado a la base de datos")
conexion.close()