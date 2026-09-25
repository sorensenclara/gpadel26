import random
import string

def default_code():
    """Genera un código aleatorio de 6 caracteres para identificar cada partido."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
