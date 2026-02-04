from flask import Flask, render_template, request, redirect, session, send_file
from flask_socketio import SocketIO, emit
import sqlite3
from datetime import datetime
import qrcode
import os
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from io import BytesIO
import base64

# -----------------------------
# CONFIGURACIÓN APP
# -----------------------------
app = Flask(__name__)
app.secret_key = "clave_secreta"
socketio = SocketIO(app, async_mode="eventlet")

# -----------------------------
# BASE DE DATOS
# -----------------------------
def get_db():
    return sqlite3.connect("database.db")

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE,
            password TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orden_llegada (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT,
            dni TEXT,
            numero_orden INTEGER,
            fecha TEXT,
            hora TEXT,
            estado TEXT DEFAULT 'EN ESPERA'
        )
    """)

    cursor.execute("""
        INSERT OR IGNORE INTO usuarios (usuario, password)
        VALUES ('secretaria', '1234')
    """)

    conn.commit()
    conn.close()

# -----------------------------
# LOGIN
# -----------------------------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form["usuario"]
        password = request.form["password"]

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM usuarios WHERE usuario=? AND password=?",
            (usuario, password)
        )
        user = cursor.fetchone()
        conn.close()

        if user:
            session["usuario"] = usuario
            return redirect("/secretaria")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# -----------------------------
# GENERAR QR BASE64
# -----------------------------
def generar_qr_base64():
    URL_PUBLICA = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5000")
    url = URL_PUBLICA + "/registrar"

    qr = qrcode.make(url)
    buffer = BytesIO()
    qr.save(buffer, format="PNG")

    return base64.b64encode(buffer.getvalue()).decode()

# -----------------------------
# SECRETARIA
# -----------------------------
@app.route("/secretaria")
def secretaria():
    if "usuario" not in session:
        return redirect("/")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, numero_orden, nombre, dni, hora, estado
        FROM orden_llegada
        WHERE fecha = DATE('now')
        ORDER BY numero_orden
    """)
    datos = cursor.fetchall()
    conn.close()

    qr = generar_qr_base64()

    return render_template("secretaria.html", datos=datos, qr=qr)

@socketio.on("atender_turno")
def atender_turno(id_turno):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE orden_llegada
        SET estado='ATENDIDO'
        WHERE id=?
    """, (id_turno,))
    conn.commit()
    conn.close()

    emit("nuevo_turno", broadcast=True)

# -----------------------------
# REGISTRO QR
# -----------------------------
@app.route("/registrar", methods=["GET", "POST"])
def registrar():
    turno = None

    if request.method == "POST":
        nombre = request.form["nombre"]
        dni = request.form["dni"]
        fecha = datetime.now().strftime("%Y-%m-%d")
        hora = datetime.now().strftime("%H:%M:%S")

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT MAX(numero_orden)
            FROM orden_llegada
            WHERE fecha = ?
        """, (fecha,))
        ultimo = cursor.fetchone()[0]

        turno = 1 if ultimo is None else ultimo + 1

        cursor.execute("""
            INSERT INTO orden_llegada (nombre, dni, numero_orden, fecha, hora)
            VALUES (?, ?, ?, ?, ?)
        """, (nombre, dni, turno, fecha, hora))

        conn.commit()
        conn.close()

        emit("nuevo_turno", broadcast=True)

    return render_template("registrar.html", turno=turno)

# -----------------------------
# HISTORIAL
# -----------------------------
@app.route("/historial")
def historial():
    if "usuario" not in session:
        return redirect("/")

    filtro = request.args.get("filtro", "diario")

    conn = get_db()
    cursor = conn.cursor()

    if filtro == "diario":
        cursor.execute("SELECT * FROM orden_llegada WHERE fecha = DATE('now')")
    elif filtro == "semanal":
        cursor.execute("""
            SELECT * FROM orden_llegada
            WHERE fecha BETWEEN DATE('now','-7 day') AND DATE('now')
        """)
    else:
        cursor.execute("""
            SELECT * FROM orden_llegada
            WHERE strftime('%Y-%m', fecha) = strftime('%Y-%m', 'now')
        """)

    datos = cursor.fetchall()
    conn.close()

    return render_template("historial.html", datos=datos, filtro=filtro)

# -----------------------------
# PDF
# -----------------------------
def generar_pdf(datos, titulo):
    if not os.path.exists("reports"):
        os.makedirs("reports")

    archivo = "reports/reporte_turnos.pdf"
    c = canvas.Canvas(archivo, pagesize=A4)
    width, height = A4
    y = height - 50

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(width / 2, y, titulo)
    y -= 40

    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "N°")
    c.drawString(70, y, "Orden")
    c.drawString(120, y, "Nombre")
    c.drawString(320, y, "DNI")
    c.drawString(390, y, "Hora")
    c.drawString(450, y, "Estado")
    y -= 20

    c.setFont("Helvetica", 10)

    contador = 1
    for d in datos:
        c.drawString(40, y, str(contador))
        c.drawString(70, y, str(d[3]))
        c.drawString(120, y, str(d[1] or ""))
        c.drawString(320, y, str(d[2] or ""))
        c.drawString(390, y, str(d[4] or ""))
        c.drawString(450, y, str(d[5] or ""))

        y -= 18
        contador += 1

        if y < 50:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = height - 50

    c.save()
    return archivo

@app.route("/reporte_pdf/<tipo>")
def reporte_pdf(tipo):
    conn = get_db()
    cursor = conn.cursor()

    if tipo == "diario":
        cursor.execute("SELECT * FROM orden_llegada WHERE fecha = DATE('now')")
        titulo = "REPORTE DIARIO DE TURNOS"
    elif tipo == "semanal":
        cursor.execute("""
            SELECT * FROM orden_llegada
            WHERE fecha BETWEEN DATE('now','-7 day') AND DATE('now')
        """)
        titulo = "REPORTE SEMANAL DE TURNOS"
    else:
        cursor.execute("""
            SELECT * FROM orden_llegada
            WHERE strftime('%Y-%m', fecha) = strftime('%Y-%m', 'now')
        """)
        titulo = "REPORTE MENSUAL DE TURNOS"

    datos = cursor.fetchall()
    conn.close()

    archivo = generar_pdf(datos, titulo)
    return send_file(archivo, as_attachment=True)

# -----------------------------
# MAIN
# -----------------------------
if __name__ == "__main__":
    init_db()

    # SOLO ejecutar estas funciones en local, NO en Render
    if os.environ.get("RENDER") is None:
        actualizar_db()

    port = int(os.environ.get("PORT", 5000))

    socketio.run(app, host="0.0.0.0", port=port)




    



