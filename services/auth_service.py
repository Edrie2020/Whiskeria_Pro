import hmac
import hashlib
from passlib.context import CryptContext
from fastapi import Request

# Cambiamos "bcrypt" por "pbkdf2_sha256" para evitar el error de Python 3.13
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# 🔑 LLAVE SECRETA DEL CLUB (Cámbiala por la que quieras en producción)
SECRET_KEY = "WHISKERIA_2000_PRO_SECURE_SIGNING_KEY_JWT_HMAC_9981"

def obtener_hash(password: str):
    return pwd_context.hash(password)

def verificar_password(password_plano, password_hash):
    return pwd_context.verify(password_plano, password_hash)

# --- SISTEMA DE CONTROL DE FIRMAS CRIPTOGRÁFICAS EN COOKIES ---

def firmar_valor(valor: str) -> str:
    """Añade una firma SHA256 al final del valor para certificar su autenticidad."""
    firma = hmac.new(SECRET_KEY.encode(), valor.encode(), hashlib.sha256).hexdigest()
    return f"{valor}.{firma}"

def verificar_y_obtener_valor(valor_firmado: str) -> str | None:
    """Verifica que la firma de la cookie no haya sido manipulada."""
    if not valor_firmado or "." not in valor_firmado:
        return None
    try:
        valor, firma_recibida = valor_firmado.rsplit(".", 1)
        firma_esperada = hmac.new(SECRET_KEY.encode(), valor.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(firma_recibida, firma_esperada):
            return valor
    except Exception:
        pass
    return None

def obtener_usuario_sesion(request: Request):
    """Devuelve (username, role) de la sesión de manera 100% segura y verificada."""
    cookie_user = request.cookies.get("session_user")
    cookie_role = request.cookies.get("session_role")
    
    if not cookie_user or not cookie_role:
        return None, None
        
    username = verificar_y_obtener_valor(cookie_user)
    role = verificar_y_obtener_valor(cookie_role)
    
    return username, role