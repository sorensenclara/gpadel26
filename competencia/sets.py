"""
Lógica pura (sin base de datos) sobre el detalle de sets de un resultado.

Formato de `Resultado.sets` (JSON): lista de 2 o 3 elementos, cada uno
    {"a": 6, "b": 3}                                  -> set normal
    {"a": 10, "b": 7, "tipo": "super_tiebreak"}       -> super tie-break (solo 3.er elemento)
donde "a" es la pareja_a del partido y "b" la pareja_b.

Qué valida: estructura, que nadie "empate" un set, que el partido tenga
ganador (2 sets ganados) y que el 3.er set exista si y solo si hubo 1-1.
Qué NO valida (no está definido en el reglamento): marcadores de games
"posibles" dentro de un set (6-4, 7-5, 7-6...). Eso queda abierto a propósito.
"""
from django.core.exceptions import ValidationError

TIPO_SET = "set"
TIPO_SUPER_TIEBREAK = "super_tiebreak"
SUPER_TB_MINIMO = 10
SUPER_TB_DIFERENCIA = 2


def _entero(valor):
    return isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0


def _normalizar(sets):
    if not isinstance(sets, list) or not 2 <= len(sets) <= 3:
        raise ValidationError("El resultado debe tener 2 o 3 sets.")
    normalizados = []
    for i, item in enumerate(sets, start=1):
        if not isinstance(item, dict):
            raise ValidationError(f"El set {i} tiene un formato inválido.")
        a, b = item.get("a"), item.get("b")
        tipo = item.get("tipo", TIPO_SET)
        if not (_entero(a) and _entero(b)):
            raise ValidationError(f"El set {i} necesita marcadores enteros no negativos.")
        if tipo not in (TIPO_SET, TIPO_SUPER_TIEBREAK):
            raise ValidationError(f"El set {i} tiene un tipo desconocido.")
        if tipo == TIPO_SUPER_TIEBREAK and i != 3:
            raise ValidationError("El super tie-break solo puede ser el tercer set.")
        if a == b:
            raise ValidationError(f"El set {i} no puede terminar empatado.")
        if tipo == TIPO_SUPER_TIEBREAK and (
            max(a, b) < SUPER_TB_MINIMO or abs(a - b) < SUPER_TB_DIFERENCIA
        ):
            raise ValidationError(
                f"El super tie-break se gana con al menos {SUPER_TB_MINIMO} puntos "
                f"y {SUPER_TB_DIFERENCIA} de diferencia."
            )
        normalizados.append({"a": a, "b": b, "tipo": tipo})
    return normalizados


def resumen_sets(sets):
    """
    Valida y devuelve los totales del partido. Lanza ValidationError si el
    detalle no es un resultado completo y consistente.

    `games_a/games_b` cuentan SOLO los sets normales: el super tie-break se
    informa aparte (`super_tiebreak`) porque todavía no está definido si
    cuenta como games para la diferencia de games (criterio de desempate
    pendiente de definición).
    """
    items = _normalizar(sets)
    ganados_a = sum(1 for s in items if s["a"] > s["b"])
    ganados_b = sum(1 for s in items if s["b"] > s["a"])

    primeros_dos = items[:2]
    uno_a_uno = (primeros_dos[0]["a"] > primeros_dos[0]["b"]) != (
        primeros_dos[1]["a"] > primeros_dos[1]["b"]
    )
    if uno_a_uno and len(items) != 3:
        raise ValidationError("Con un set para cada pareja hace falta un tercer set.")
    if not uno_a_uno and len(items) == 3:
        raise ValidationError("El partido ya estaba definido en dos sets: sobra el tercero.")

    normales = [s for s in items if s["tipo"] == TIPO_SET]
    return {
        "ganador": "a" if ganados_a > ganados_b else "b",
        "sets_a": ganados_a,
        "sets_b": ganados_b,
        "games_a": sum(s["a"] for s in normales),
        "games_b": sum(s["b"] for s in normales),
        "cantidad_sets": len(items),
        "super_tiebreak": any(s["tipo"] == TIPO_SUPER_TIEBREAK for s in items),
    }


def validar_sets(sets):
    """Validador para el campo JSON (descarta el resumen)."""
    resumen_sets(sets)
