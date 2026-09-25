from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import CategoriaEditFormSet, CategoriaForm, CategoriaFormSet, InscripcionForm, TorneoForm
from .models import Categoria, Inscripcion, Torneo


def _organizador_o_403(request):
    organizador = getattr(request.user, "organizador", None)
    if not organizador:
        raise PermissionDenied(
            "Tu cuenta no tiene perfil de Organizador. Registrate como "
            "Dueño de cancha o Representante de Liga para crear torneos."
        )
    return organizador


def _torneo_propio_o_403(request, codigo):
    torneo = get_object_or_404(Torneo, codigo=codigo)
    organizador = _organizador_o_403(request)
    if torneo.organizador_id != organizador.id:
        raise PermissionDenied("Este torneo pertenece a otro Organizador.")
    return torneo


@login_required
def mis_torneos(request):
    organizador = _organizador_o_403(request)
    torneos = list(organizador.torneos.all())

    # Conteos por estado visual (para los contadores de las pestañas)
    conteos = {"todos": len(torneos), "publicado": 0, "borrador": 0, "finalizado": 0}
    for t in torneos:
        if t.estado_visual == "borrador":
            conteos["borrador"] += 1
        elif t.estado_visual == "finalizado":
            conteos["finalizado"] += 1
        else:
            conteos["publicado"] += 1

    estado_filtro = request.GET.get("estado", "todos")
    if estado_filtro != "todos":
        if estado_filtro == "publicado":
            torneos = [t for t in torneos if t.estado_visual in ("inscripcion_abierta", "en_juego")]
        else:
            torneos = [t for t in torneos if t.estado_visual == estado_filtro]

    busqueda = request.GET.get("q", "").strip()
    if busqueda:
        torneos = [t for t in torneos if busqueda.lower() in t.nombre.lower()]

    orden = request.GET.get("orden", "recientes")
    if orden == "proximos":
        torneos.sort(key=lambda t: t.fecha_inicio)
    elif orden == "nombre":
        torneos.sort(key=lambda t: t.nombre.lower())
    else:  # recientes: más nuevos primero
        torneos.sort(key=lambda t: t.creado, reverse=True)

    return render(
        request,
        "torneos/mis_torneos.html",
        {
            "torneos": torneos,
            "conteos": conteos,
            "estado_filtro": estado_filtro,
            "busqueda": busqueda,
            "orden": orden,
        },
    )


@login_required
def crear_torneo(request):
    """
    Paso 1 (formato + datos generales) y Paso 2 (categorías + cupo mínimo)
    de la especificación, en una sola pantalla con dos pasos visuales.
    """
    organizador = _organizador_o_403(request)

    if request.method == "POST":
        torneo_form = TorneoForm(request.POST)
        categoria_formset = CategoriaFormSet(request.POST, prefix="cat")

        if torneo_form.is_valid() and categoria_formset.is_valid():
            with transaction.atomic():
                torneo = torneo_form.save(commit=False)
                torneo.organizador = organizador
                torneo.save()

                orden = 0
                for cat_form in categoria_formset:
                    if not cat_form.cleaned_data or cat_form.cleaned_data.get("DELETE"):
                        continue
                    categoria = cat_form.save(commit=False)
                    categoria.torneo = torneo
                    categoria.orden = orden
                    categoria.save()
                    orden += 1

            return redirect("torneos:organizador_detalle", codigo=torneo.codigo)
    else:
        torneo_form = TorneoForm()
        categoria_formset = CategoriaFormSet(prefix="cat")

    return render(
        request,
        "torneos/crear_torneo.html",
        {"torneo_form": torneo_form, "categoria_formset": categoria_formset},
    )


@login_required
def editar_torneo(request, codigo):
    """Editar datos generales y categorías (agregar nuevas / borrar existentes)."""
    torneo = _torneo_propio_o_403(request, codigo)
    queryset = torneo.categorias.all()

    if request.method == "POST":
        torneo_form = TorneoForm(request.POST, instance=torneo)
        categoria_formset = CategoriaEditFormSet(request.POST, prefix="cat", queryset=queryset)

        if torneo_form.is_valid() and categoria_formset.is_valid():
            with transaction.atomic():
                torneo_form.save()
                categorias = categoria_formset.save(commit=False)
                for categoria in categorias:
                    categoria.torneo = torneo
                    categoria.save()
                for obj in categoria_formset.deleted_objects:
                    obj.delete()

            messages.success(request, "Torneo actualizado.")
            return redirect("torneos:organizador_detalle", codigo=torneo.codigo)
    else:
        torneo_form = TorneoForm(instance=torneo)
        categoria_formset = CategoriaEditFormSet(prefix="cat", queryset=queryset)

    return render(
        request,
        "torneos/editar_torneo.html",
        {"torneo": torneo, "torneo_form": torneo_form, "categoria_formset": categoria_formset},
    )


@login_required
def eliminar_torneo(request, codigo):
    """Borrar un torneo entero. Solo tiene sentido mientras está en borrador
    (uno publicado ya puede tener inscripciones reales de jugadores)."""
    torneo = _torneo_propio_o_403(request, codigo)
    if request.method == "POST":
        if torneo.estado != Torneo.ESTADO_BORRADOR:
            messages.error(request, "Solo se puede eliminar un torneo que esté en borrador.")
            return redirect("torneos:organizador_detalle", codigo=torneo.codigo)
        nombre = torneo.nombre
        torneo.delete()
        messages.success(request, f'Torneo "{nombre}" eliminado.')
    return redirect("torneos:mis_torneos")


@login_required
def cambiar_estado_torneo(request, codigo):
    """Publicar/cerrar un torneo de un click, desde la lista de 'Mis torneos'."""
    torneo = _torneo_propio_o_403(request, codigo)
    if request.method == "POST":
        accion = request.POST.get("accion")
        if accion == "publicar":
            torneo.estado = Torneo.ESTADO_PUBLICADO
            torneo.save(update_fields=["estado"])
            messages.success(request, f'"{torneo.nombre}" publicado.')
        elif accion == "cerrar":
            torneo.estado = Torneo.ESTADO_CERRADO
            torneo.save(update_fields=["estado"])
            messages.success(request, f'"{torneo.nombre}" finalizado.')
    return redirect("torneos:mis_torneos")


@login_required
def agregar_categoria(request, codigo):
    """Alta rápida de una categoría desde el modal de gestión del torneo."""
    torneo = _torneo_propio_o_403(request, codigo)
    if request.method == "POST":
        form = CategoriaForm(request.POST)
        if form.is_valid():
            categoria = form.save(commit=False)
            categoria.torneo = torneo
            categoria.orden = torneo.categorias.count()
            categoria.save()
            messages.success(request, f'Categoría "{categoria.nombre}" agregada.')
        else:
            for error in form.errors.values():
                messages.error(request, " ".join(error))
    return redirect("torneos:organizador_detalle", codigo=torneo.codigo)


@login_required
def editar_categoria(request, codigo, categoria_id):
    """Edición rápida (nombre/cupo mínimo) de una categoría, desde el modal."""
    torneo = _torneo_propio_o_403(request, codigo)
    categoria = get_object_or_404(Categoria, pk=categoria_id, torneo=torneo)
    if request.method == "POST":
        form = CategoriaForm(request.POST, instance=categoria)
        if form.is_valid():
            form.save()
            messages.success(request, "Categoría actualizada.")
        else:
            for error in form.errors.values():
                messages.error(request, " ".join(error))
    return redirect("torneos:organizador_detalle", codigo=torneo.codigo)


@login_required
def eliminar_categoria(request, codigo, categoria_id):
    """Borrar una categoría (si ya tiene inscriptos, se borran junto con ella)."""
    torneo = _torneo_propio_o_403(request, codigo)
    categoria = get_object_or_404(Categoria, pk=categoria_id, torneo=torneo)
    if request.method == "POST":
        nombre = categoria.nombre
        categoria.delete()
        messages.success(request, f'Categoría "{nombre}" eliminada.')
    return redirect("torneos:organizador_detalle", codigo=torneo.codigo)


@login_required
def organizador_detalle(request, codigo):
    """Ver inscriptos por categoría y publicar/cerrar el torneo."""
    torneo = _torneo_propio_o_403(request, codigo)

    if request.method == "POST":
        accion = request.POST.get("accion")
        if accion == "publicar":
            torneo.estado = Torneo.ESTADO_PUBLICADO
            torneo.save(update_fields=["estado"])
            messages.success(request, "Torneo publicado — ya es visible para los jugadores.")
        elif accion == "cerrar":
            torneo.estado = Torneo.ESTADO_CERRADO
            torneo.save(update_fields=["estado"])
            messages.success(request, "Torneo cerrado.")
        elif accion == "confirmar_inscripcion":
            inscripcion = get_object_or_404(
                Inscripcion, pk=request.POST.get("inscripcion_id"), torneo=torneo
            )
            inscripcion.estado = Inscripcion.ESTADO_CONFIRMADA
            inscripcion.save(update_fields=["estado"])
            messages.success(request, "Inscripción confirmada.")
        return redirect("torneos:organizador_detalle", codigo=torneo.codigo)

    categorias = torneo.categorias.prefetch_related("inscripciones")
    return render(
        request,
        "torneos/organizador_detalle.html",
        {"torneo": torneo, "categorias": categorias, "categoria_choices": Categoria.NOMBRE_CHOICES},
    )


def listado_torneos(request):
    """Público: torneos publicados o finalizados (no borradores), para /torneos/."""
    torneos = list(
        Torneo.objects.exclude(estado=Torneo.ESTADO_BORRADOR).prefetch_related("categorias")
    )
    mi_organizador_id = getattr(getattr(request.user, "organizador", None), "id", None)

    # ---------- Filtros (todos por GET, se pueden combinar) ----------
    q = request.GET.get("q", "").strip()
    anio = request.GET.get("anio", "").strip()
    mes = request.GET.get("mes", "").strip()
    categoria_sel = request.GET.get("categoria", "").strip()
    ciudad_sel = request.GET.get("ciudad", "").strip()
    solo_abiertos = request.GET.get("abiertos") == "1"
    orden = request.GET.get("orden", "proximos")

    if q:
        ql = q.lower()
        torneos = [
            t for t in torneos
            if ql in t.nombre.lower() or ql in t.ciudad.lower() or ql in t.sede.lower()
        ]
    if anio:
        torneos = [t for t in torneos if str(t.fecha_inicio.year) == anio]
    if mes:
        torneos = [t for t in torneos if str(t.fecha_inicio.month) == mes]
    if categoria_sel:
        torneos = [
            t for t in torneos
            if any(c.nombre == categoria_sel for c in t.categorias.all())
        ]
    if ciudad_sel:
        torneos = [t for t in torneos if ciudad_sel.lower() in t.ciudad.lower()]
    if solo_abiertos:
        torneos = [t for t in torneos if t.estado_visual == "inscripcion_abierta"]

    if orden == "recientes":
        torneos.sort(key=lambda t: t.creado, reverse=True)
    elif orden == "precio":
        torneos.sort(key=lambda t: t.precio_inscripcion)
    else:  # proximos
        torneos.sort(key=lambda t: t.fecha_inicio)

    # Años disponibles para el filtro (de todos los torneos listables, no solo los ya filtrados)
    anios_disponibles = sorted({
        t.fecha_inicio.year for t in Torneo.objects.exclude(estado=Torneo.ESTADO_BORRADOR)
    })

    torneos_con_mapa = [
        {
            "nombre": t.nombre,
            "ciudad": t.ciudad,
            "lat": float(t.latitud) if t.latitud is not None else None,
            "lon": float(t.longitud) if t.longitud is not None else None,
            "url": reverse("sitio:torneo_detalle", args=[t.codigo]),
            "es_mio": t.organizador_id == mi_organizador_id if mi_organizador_id else False,
            "finalizado": t.estado_visual == "finalizado",
        }
        for t in torneos
        if t.ciudad  # sin ciudad no hay nada que ubicar, ni siquiera con fallback
    ]

    return render(
        request,
        "sitio/torneos.html",
        {
            "torneos": torneos,
            "torneos_mapa": torneos_con_mapa,
            "mi_organizador_id": mi_organizador_id,
            "categoria_choices": Categoria.NOMBRE_CHOICES,
            "anios_disponibles": anios_disponibles,
            "meses": [
                (1, "Enero"), (2, "Febrero"), (3, "Marzo"), (4, "Abril"),
                (5, "Mayo"), (6, "Junio"), (7, "Julio"), (8, "Agosto"),
                (9, "Septiembre"), (10, "Octubre"), (11, "Noviembre"), (12, "Diciembre"),
            ],
            "filtros": {
                "q": q, "anio": anio, "mes": mes, "categoria": categoria_sel,
                "ciudad": ciudad_sel, "abiertos": solo_abiertos, "orden": orden,
            },
        },
    )


def torneo_detalle(request, codigo):
    """Público: detalle del torneo + formulario de inscripción."""
    torneo = get_object_or_404(Torneo, codigo=codigo)
    if torneo.estado == Torneo.ESTADO_BORRADOR:
        raise Http404("Este torneo todavía no fue publicado.")
    cerrado = (
        torneo.estado_visual != "inscripcion_abierta"
        or torneo.fecha_limite_inscripcion < timezone.localdate()
    )

    if request.method == "POST" and not cerrado:
        if not request.user.is_authenticated:
            messages.error(request, "Necesitás iniciar sesión para inscribirte.")
            return redirect("sitio:torneo_detalle", codigo=torneo.codigo)
        form = InscripcionForm(request.POST, torneo=torneo)
        if form.is_valid():
            form.save(torneo=torneo)
            messages.success(
                request,
                "¡Listo! Tu inscripción quedó registrada. "
                "Se confirma cuando el organizador reciba/valide el pago.",
            )
            return redirect("sitio:torneo_detalle", codigo=torneo.codigo)
    else:
        form = InscripcionForm(torneo=torneo)

    return render(
        request,
        "sitio/torneo_detalle.html",
        {"torneo": torneo, "form": form, "cerrado": cerrado},
    )
