from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render

from .forms import (
    ActivarJugadorForm,
    ActivarOrganizadorForm,
    GPadelAuthenticationForm,
    RegistroForm,
)
from .models import Identidad


def _perfiles_de(user):
    """(tiene_jugador, tiene_organizador) para el usuario dado."""
    return hasattr(user, "jugador"), hasattr(user, "organizador")


def _perfil_a_usar(user):
    """
    A qué perfil entra la cuenta al loguearse, sin preguntar cada vez:
    - Un solo perfil habilitado -> ese.
    - Los dos habilitados -> el último que usó (Identidad.ultimo_perfil);
      si todavía no hay preferencia registrada, Jugador por defecto.
    - Ninguno (no debería pasar) -> None.
    """
    tiene_jugador, tiene_organizador = _perfiles_de(user)
    if tiene_jugador and tiene_organizador:
        identidad = getattr(user, "identidad", None)
        if identidad and identidad.ultimo_perfil == Identidad.PERFIL_ORGANIZADOR:
            return Identidad.PERFIL_ORGANIZADOR
        return Identidad.PERFIL_JUGADOR
    if tiene_organizador:
        return Identidad.PERFIL_ORGANIZADOR
    if tiene_jugador:
        return Identidad.PERFIL_JUGADOR
    return None


def _redirect_post_login(request):
    """A dónde cae la cuenta después de loguearse (login, registro, o al
    volver a entrar ya logueado), según el perfil a usar."""
    perfil = _perfil_a_usar(request.user)
    if perfil == Identidad.PERFIL_JUGADOR:
        return redirect("sitio:jugadores")
    if perfil == Identidad.PERFIL_ORGANIZADOR:
        return redirect("sitio:canchas_ligas")
    return redirect("scoreboard:index")


def _es_pedido_ajax(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def login_view(request):
    if request.user.is_authenticated:
        if _es_pedido_ajax(request):
            return JsonResponse({"ok": True})
        return _redirect_post_login(request)

    next_url = request.POST.get("next") or request.GET.get("next") or ""

    if request.method == "POST":
        form = GPadelAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            if _es_pedido_ajax(request):
                return JsonResponse({"ok": True})
            return redirect(next_url) if next_url else _redirect_post_login(request)
        elif _es_pedido_ajax(request):
            # Mismo mensaje de error que ya usa el form de página completa.
            errores = form.errors.get("__all__") or ["Usuario o contraseña incorrectos."]
            return JsonResponse({"ok": False, "error": errores[0]}, status=400)
    else:
        form = GPadelAuthenticationForm(request)

    return render(request, "sitio/login.html", {"form": form, "next": next_url})


def logout_view(request):
    logout(request)
    return redirect("sitio:login")


def registro_view(request):
    if request.user.is_authenticated:
        return _redirect_post_login(request)

    if request.method == "POST":
        form = RegistroForm(request.POST)
        if form.is_valid():
            user, rol, perfil = form.save()
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")

            if rol == RegistroForm.ROL_JUGADOR:
                messages.success(request, "¡Cuenta creada! Ya podés armar tu marcador.")
                return redirect("sitio:jugadores")

            messages.success(
                request, "¡Cuenta creada! Ya podés armar tu marcador personalizado."
            )
            return redirect("sitio:canchas_ligas")
    else:
        form = RegistroForm()

    return render(request, "sitio/registro.html", {"form": form})


@login_required
def cambiar_perfil(request, perfil):
    """
    Cambia de contexto (Jugador <-> Organizador) sin cerrar sesión, y
    recuerda la elección para el próximo ingreso. Solo tiene sentido si la
    cuenta tiene los dos perfiles habilitados.
    """
    tiene_jugador, tiene_organizador = _perfiles_de(request.user)
    if not (tiene_jugador and tiene_organizador):
        return redirect("sitio:inicio")

    identidad = getattr(request.user, "identidad", None)
    if perfil == Identidad.PERFIL_JUGADOR and tiene_jugador:
        if identidad:
            identidad.ultimo_perfil = Identidad.PERFIL_JUGADOR
            identidad.save(update_fields=["ultimo_perfil"])
        return redirect("sitio:jugadores")

    if perfil == Identidad.PERFIL_ORGANIZADOR and tiene_organizador:
        if identidad:
            identidad.ultimo_perfil = Identidad.PERFIL_ORGANIZADOR
            identidad.save(update_fields=["ultimo_perfil"])
        return redirect("sitio:canchas_ligas")

    return redirect("sitio:inicio")


@login_required
def mi_cuenta(request):
    """
    Datos de la persona (nombre/apellido/DNI/celular) + activar el perfil
    que todavía no tenga habilitado, sin crear una cuenta nueva.
    """
    tiene_jugador, tiene_organizador = _perfiles_de(request.user)
    identidad = getattr(request.user, "identidad", None)

    jugador_form = None
    organizador_form = None

    if request.method == "POST":
        accion = request.POST.get("accion")

        if accion == "activar_jugador" and not tiene_jugador:
            jugador_form = ActivarJugadorForm(request.POST)
            if jugador_form.is_valid():
                jugador_form.save(request.user)
                messages.success(request, "¡Listo! Ya podés usar tu marcador de Jugador.")
                return redirect("accounts:mi_cuenta")

        elif accion == "activar_organizador" and not tiene_organizador:
            organizador_form = ActivarOrganizadorForm(request.POST)
            if organizador_form.is_valid():
                organizador_form.save(request.user)
                messages.success(
                    request, "¡Listo! Ya podés crear torneos y tu marcador personalizado."
                )
                return redirect("accounts:mi_cuenta")

    if jugador_form is None and not tiene_jugador:
        jugador_form = ActivarJugadorForm()
    if organizador_form is None and not tiene_organizador:
        organizador_form = ActivarOrganizadorForm()

    return render(
        request,
        "sitio/mi_cuenta.html",
        {
            "identidad": identidad,
            "tiene_jugador": tiene_jugador,
            "tiene_organizador": tiene_organizador,
            "jugador_form": jugador_form,
            "organizador_form": organizador_form,
        },
    )
