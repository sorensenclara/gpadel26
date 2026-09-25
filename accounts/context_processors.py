def perfil_actual(request):
    """
    Disponible en todos los templates: si la cuenta tiene Jugador,
    Organizador, o ambos habilitados, y cuál es el "contexto" actual
    (para el logo, el selector del header, etc.).
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    from .views import _perfil_a_usar, _perfiles_de

    tiene_jugador, tiene_organizador = _perfiles_de(user)
    return {
        "gp_tiene_jugador": tiene_jugador,
        "gp_tiene_organizador": tiene_organizador,
        "gp_ambos_perfiles": tiene_jugador and tiene_organizador,
        "gp_perfil_actual": _perfil_a_usar(user),
    }
