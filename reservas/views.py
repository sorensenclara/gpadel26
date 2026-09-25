import datetime

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from .models import Cancha, Reserva


def buscar_canchas(request):
    """Público: buscar canchas por ciudad y ver disponibilidad de un día."""
    ciudad = request.GET.get("ciudad", "").strip()
    fecha_str = request.GET.get("fecha", "")
    try:
        fecha = datetime.date.fromisoformat(fecha_str)
    except ValueError:
        fecha = datetime.date.today()

    canchas = Cancha.objects.filter(activa=True)
    if ciudad:
        canchas = canchas.filter(ciudad__icontains=ciudad)

    return render(
        request,
        "sitio/reservar_cancha.html",
        {"canchas": canchas, "ciudad": ciudad, "fecha": fecha},
    )


def cancha_detalle(request, cancha_id):
    """Público: ver una cancha y sus turnos disponibles para una fecha."""
    cancha = get_object_or_404(Cancha, pk=cancha_id, activa=True)
    fecha_str = request.GET.get("fecha", "")
    try:
        fecha = datetime.date.fromisoformat(fecha_str)
    except ValueError:
        fecha = datetime.date.today()
    if fecha < datetime.date.today():
        fecha = datetime.date.today()

    turnos = cancha.turnos_del_dia(fecha)
    return render(
        request,
        "sitio/cancha_detalle.html",
        {"cancha": cancha, "fecha": fecha, "turnos": turnos},
    )


def reservar_turno(request, cancha_id):
    """
    Confirma la reserva de un turno puntual. Requiere estar logueado — si
    no lo está, el formulario ya viene interceptado por el modal de login
    en el template (ver cancha_detalle.html), así que llegar acá sin
    sesión solo pasa si alguien saltea el JS: en ese caso lo mandamos de
    vuelta sin reservar nada, con un aviso.
    """
    cancha = get_object_or_404(Cancha, pk=cancha_id, activa=True)

    if request.method != "POST":
        return redirect("sitio:cancha_detalle", cancha_id=cancha.id)

    fecha_str = request.POST.get("fecha", "")
    hora_str = request.POST.get("hora_inicio", "")
    try:
        fecha = datetime.date.fromisoformat(fecha_str)
        hora_inicio = datetime.time.fromisoformat(hora_str)
    except ValueError:
        messages.error(request, "Turno inválido.")
        return redirect("sitio:cancha_detalle", cancha_id=cancha.id)

    if not request.user.is_authenticated:
        messages.error(request, "Necesitás iniciar sesión para reservar.")
        return redirect(f"/canchas/{cancha.id}/?fecha={fecha.isoformat()}")

    # Buscamos el turno exacto (hora_fin) entre los turnos válidos del día,
    # para no confiar en datos sueltos que pueda mandar el cliente.
    turno = next(
        (t for t in cancha.turnos_del_dia(fecha) if t["hora_inicio"] == hora_inicio),
        None,
    )
    if turno is None:
        messages.error(request, "Ese turno no existe.")
        return redirect(f"/canchas/{cancha.id}/?fecha={fecha.isoformat()}")
    if not turno["disponible"]:
        messages.error(request, "Ese turno ya fue reservado por otra persona.")
        return redirect(f"/canchas/{cancha.id}/?fecha={fecha.isoformat()}")

    # Doble chequeo contra condición de carrera (dos reservas casi simultáneas).
    ya_ocupado = Reserva.objects.filter(
        cancha=cancha, fecha=fecha, hora_inicio=hora_inicio, estado=Reserva.ESTADO_CONFIRMADA
    ).exists()
    if ya_ocupado:
        messages.error(request, "Ese turno ya fue reservado por otra persona.")
        return redirect(f"/canchas/{cancha.id}/?fecha={fecha.isoformat()}")

    Reserva.objects.create(
        cancha=cancha,
        usuario=request.user,
        fecha=fecha,
        hora_inicio=turno["hora_inicio"],
        hora_fin=turno["hora_fin"],
    )
    messages.success(
        request,
        f"¡Reserva confirmada! {cancha.nombre} el {fecha.strftime('%d/%m/%Y')} a las "
        f"{turno['hora_inicio'].strftime('%H:%M')}.",
    )
    return redirect(f"/canchas/{cancha.id}/?fecha={fecha.isoformat()}")
