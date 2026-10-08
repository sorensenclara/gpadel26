from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .forms import (
    ActivarJugadorForm,
    ActivarOrganizadorForm,
    GPadelAuthenticationForm,
    RegistroForm,
)
from torneos.servicios import categoria_minima_declarada, declaradas_por_terceros

from .models import NIVEL_CHOICES, NIVEL_LABELS, Identidad, puede_subir_categoria


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

            if getattr(user, "reclamo_identidad_creado", None) is not None:
                messages.warning(
                    request,
                    "Tu DNI ya figura en GPADEL: verificaremos que sos esa persona antes de asociarte su "
                    "historial. Mientras tanto podés usar la plataforma con normalidad.",
                )
                return redirect("accounts:mi_cuenta")
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
    celular = getattr(getattr(request.user, "jugador", None), "celular", None) or getattr(
        getattr(request.user, "organizador", None), "celular", None
    )

    categoria_oficial = getattr(getattr(request.user, "jugador", None), "categoria_oficial", None)
    # Solo se ofrecen categorías MEJORES que la actual (la regla también se valida al enviar).
    categorias_para_subir = [
        (valor, nombre) for valor, nombre in NIVEL_CHOICES
        if categoria_oficial and valor < categoria_oficial
    ]

    # Categoría declarada por terceros, pendiente de confirmación del titular.
    categoria_piso = None
    declaradas = []
    if categoria_oficial is None:
        categoria_piso = categoria_minima_declarada(request.user)
        declaradas = [
            {"torneo": insc.torneo, "categoria": NIVEL_LABELS[insc.categoria_oficial_2]}
            for insc in declaradas_por_terceros(request.user)
        ]
    categorias_para_confirmar = [
        (valor, nombre) for valor, nombre in NIVEL_CHOICES if categoria_piso and valor <= categoria_piso
    ]
    revision_abierta = request.user.revisiones_identidad.filter(estado="abierta").exists()
    reclamo_abierto = request.user.reclamos_identidad.filter(estado__in=["pendiente", "en_conflicto"]).first()

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
            "celular": celular,
            "categoria_oficial": categoria_oficial,
            "categoria_oficial_label": NIVEL_LABELS.get(categoria_oficial, ""),
            "categorias_para_subir": categorias_para_subir,
            "categoria_piso": categoria_piso,
            "categoria_piso_label": NIVEL_LABELS.get(categoria_piso, ""),
            "declaradas_por_terceros": declaradas,
            "categorias_para_confirmar": categorias_para_confirmar,
            "revision_abierta": revision_abierta,
            "reclamo_abierto": reclamo_abierto,
            "tiene_jugador": tiene_jugador,
            "tiene_organizador": tiene_organizador,
            "jugador_form": jugador_form,
            "organizador_form": organizador_form,
        },
    )


@login_required
@require_POST
def actualizar_categoria(request):
    """
    Desde su propia cuenta, un jugador puede SUBIR su categoría oficial. Nunca
    bajarla, y la primera vez se declara al inscribirse a un torneo. Lo mismo
    que se valida en la inscripción, acá también en backend.
    """
    jugador = getattr(request.user, "jugador", None)
    if jugador is None:
        messages.error(request, "Esta acción es solo para perfiles de Jugador.")
        return redirect("accounts:mi_cuenta")

    try:
        nueva = int(request.POST.get("categoria_oficial", ""))
    except ValueError:
        nueva = None
    if nueva not in NIVEL_LABELS:
        messages.error(request, "Elegí una categoría válida.")
    elif jugador.categoria_oficial is None:
        # Todavía sin categoría oficial. Si otras personas ya declararon una (y el
        # organizador la confirmó), el titular puede CONFIRMARLA o declarar una
        # superior, nunca una inferior. Sin ninguna declaración previa, la
        # primera categoría se declara al inscribirse a un torneo.
        piso = categoria_minima_declarada(request.user)
        if piso is None:
            messages.error(request, "Tu categoría oficial se declara en tu primera inscripción a un torneo.")
        elif not puede_subir_categoria(piso, nueva):
            messages.error(
                request,
                f"Ya figurás declarado como {NIVEL_LABELS[piso]}: podés confirmarla o elegir una superior, no una inferior.",
            )
        else:
            jugador.categoria_oficial = nueva
            jugador.save(update_fields=["categoria_oficial"])
            messages.success(request, f"Confirmaste tu categoría oficial: {NIVEL_LABELS[nueva]}.")
    elif nueva == jugador.categoria_oficial:
        messages.info(request, "Esa ya es tu categoría oficial.")
    elif not puede_subir_categoria(jugador.categoria_oficial, nueva):
        messages.error(
            request,
            f"Tu categoría oficial es {NIVEL_LABELS[jugador.categoria_oficial]}: podés subirla, no bajarla.",
        )
    else:
        jugador.categoria_oficial = nueva
        jugador.save(update_fields=["categoria_oficial"])
        messages.success(request, f"Tu categoría oficial ahora es {NIVEL_LABELS[nueva]}.")
    return redirect("accounts:mi_cuenta")
