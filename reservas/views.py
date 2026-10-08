import datetime

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from .models import Cancha, Reserva

MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _parse_fecha(valor):
    try:
        return datetime.date.fromisoformat(valor)
    except (ValueError, TypeError):
        return None


def _parse_hora(valor):
    try:
        return datetime.time.fromisoformat(valor)
    except (ValueError, TypeError):
        return None


def _describir_fecha(fecha):
    return f"el {DIAS[fecha.weekday()]} {fecha.day} de {MESES[fecha.month - 1]}"


def buscar_canchas(request):
    """
    Público: buscador de disponibilidad (lugar + día + horario). Sin
    parámetros en la URL todavía no se hizo ninguna búsqueda — se muestra
    la invitación a buscar en vez de listar todo sin contexto.
    """
    hubo_busqueda = bool(request.GET)

    lugar = request.GET.get("lugar", "").strip()
    fecha = _parse_fecha(request.GET.get("fecha", "")) or datetime.date.today()
    desde = _parse_hora(request.GET.get("desde", ""))
    hasta = _parse_hora(request.GET.get("hasta", ""))
    tipo = request.GET.get("tipo", "")  # "" | "techada" | "aire_libre"

    resultados = []
    canchas_mapa = []

    if hubo_busqueda:
        canchas = Cancha.objects.filter(activa=True)
        if lugar:
            canchas = canchas.filter(ciudad__icontains=lugar)
        if tipo == "techada":
            canchas = canchas.filter(techada=True)
        elif tipo == "aire_libre":
            canchas = canchas.filter(techada=False)

        for cancha in canchas:
            turnos = cancha.turnos_del_dia(fecha)
            disponibles = [t["hora_inicio"] for t in turnos if t["disponible"]]
            if desde:
                disponibles = [h for h in disponibles if h >= desde]
            if hasta:
                disponibles = [h for h in disponibles if h < hasta]
            if not disponibles:
                continue
            resultados.append({"cancha": cancha, "horarios": disponibles})
            canchas_mapa.append({
                "id": cancha.id,
                "nombre": cancha.nombre,
                "ciudad": cancha.ciudad,
                "precio_hora": str(cancha.precio_hora),
                "cantidad_horarios": len(disponibles),
                "url": f"/canchas/{cancha.id}/?fecha={fecha.isoformat()}",
                "lat": None,
                "lon": None,
            })

    return render(
        request,
        "sitio/reservar_cancha.html",
        {
            "hubo_busqueda": hubo_busqueda,
            "lugar": lugar,
            "fecha": fecha,
            "desde": desde,
            "hasta": hasta,
            "tipo": tipo,
            "resultados": resultados,
            "canchas_mapa": canchas_mapa,
            "descripcion_fecha": _describir_fecha(fecha) if hubo_busqueda else "",
        },
    )


def cancha_detalle(request, cancha_id):
    """Público: ver una cancha y sus turnos disponibles para una fecha."""
    cancha = get_object_or_404(Cancha, pk=cancha_id, activa=True)
    fecha = _parse_fecha(request.GET.get("fecha", "")) or datetime.date.today()
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
    en el template (lista de resultados o detalle de cancha), así que
    llegar acá sin sesión solo pasa si alguien saltea el JS: en ese caso
    lo mandamos de vuelta sin reservar nada, con un aviso.

    `next` (opcional, en el POST): a dónde volver después — así reservar
    desde la lista de resultados vuelve a la lista, no siempre al detalle.
    """
    cancha = get_object_or_404(Cancha, pk=cancha_id, activa=True)
    fecha_str = request.POST.get("fecha", "")
    volver = request.POST.get("next") or f"/canchas/{cancha.id}/?fecha={fecha_str}"

    if request.method != "POST":
        return redirect(volver)

    fecha = _parse_fecha(fecha_str)
    hora_inicio = _parse_hora(request.POST.get("hora_inicio", ""))
    if fecha is None or hora_inicio is None:
        messages.error(request, "Turno inválido.")
        return redirect(volver)

    if not request.user.is_authenticated:
        messages.error(request, "Necesitás iniciar sesión para reservar.")
        return redirect(volver)

    # Buscamos el turno exacto (hora_fin) entre los turnos válidos del día,
    # para no confiar en datos sueltos que pueda mandar el cliente.
    turno = next(
        (t for t in cancha.turnos_del_dia(fecha) if t["hora_inicio"] == hora_inicio),
        None,
    )
    if turno is None:
        messages.error(request, "Ese turno no existe.")
        return redirect(volver)
    if not turno["disponible"]:
        messages.error(request, "Ese turno ya fue reservado por otra persona.")
        return redirect(volver)

    # Doble chequeo contra condición de carrera (dos reservas casi simultáneas).
    ya_ocupado = Reserva.objects.filter(
        cancha=cancha, fecha=fecha, hora_inicio=hora_inicio, estado=Reserva.ESTADO_CONFIRMADA
    ).exists()
    if ya_ocupado:
        messages.error(request, "Ese turno ya fue reservado por otra persona.")
        return redirect(volver)

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
    return redirect(volver)
