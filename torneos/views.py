import re
import unicodedata

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import Identidad, Jugador, dni_en_uso_por_identidad, normalizar_dni

from .forms import CategoriaEditFormSet, CategoriaForm, CategoriaFormSet, InscripcionForm, TorneoForm
from .models import Categoria, Inscripcion, Torneo
from .participaciones import categorias_disponibles, estado_por_torneo, inscripciones_de, lado_del_usuario
from .servicios import buscar_companero, categoria_minima_declarada, puede_subir_categoria


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


def _sincronizar_categoria_sumatoria(torneo):
    """
    En modalidad 'Por sumatoria' el torneo ES una única categoría
    ("SUMA <n>"), armada sola a partir de `torneo.sumatoria_valor` — acá
    no hay lista de categorías para que el organizador edite a mano.
    Actualiza la categoría existente en vez de recrearla, para no romper
    inscripciones ya hechas (que apuntan a su id por FK).
    """
    nombre = f"SUMA {torneo.sumatoria_valor}"
    categorias = list(torneo.categorias.all())
    if categorias:
        principal = categorias[0]
        principal.nombre = nombre
        principal.sumatoria_minima = torneo.sumatoria_valor
        if not principal.cupo_minimo:
            principal.cupo_minimo = 1
        principal.orden = 0
        principal.save()
        for extra in categorias[1:]:  # restos de cuando era "por categoría"
            extra.delete()
    else:
        Categoria.objects.create(
            torneo=torneo, nombre=nombre, sumatoria_minima=torneo.sumatoria_valor,
            cupo_minimo=1, orden=0,
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
        es_sumatoria = request.POST.get("modalidad_categoria") == Torneo.MODALIDAD_POR_SUMATORIA
        formset_valido = es_sumatoria or categoria_formset.is_valid()

        if torneo_form.is_valid() and formset_valido:
            with transaction.atomic():
                torneo = torneo_form.save(commit=False)
                torneo.organizador = organizador
                torneo.save()

                if es_sumatoria:
                    _sincronizar_categoria_sumatoria(torneo)
                else:
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
        es_sumatoria = request.POST.get("modalidad_categoria") == Torneo.MODALIDAD_POR_SUMATORIA
        formset_valido = es_sumatoria or categoria_formset.is_valid()

        if torneo_form.is_valid() and formset_valido:
            with transaction.atomic():
                torneo_form.save()
                if es_sumatoria:
                    _sincronizar_categoria_sumatoria(torneo)
                else:
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

    # INSCRIPTO: torneos donde esta cuenta ya figura (la inscribió ella o su pareja).
    estado_inscripcion = estado_por_torneo(request.user, torneos)
    for t in torneos:
        t.inscripto_en = estado_inscripcion.get(t.pk, {}).get("categorias", [])
        t.puede_otra_categoria = estado_inscripcion.get(t.pk, {}).get("puede_otra", False)

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


def _sincronizar_datos_cuenta(usuario, inscripcion):
    """
    El Jugador 1 de una inscripción es siempre la propia persona logueada.
    Si tocó "Editar" y cambió nombre/DNI/celular/localidad para esa
    inscripción puntual, reflejamos esos mismos cambios en su cuenta
    (User/Identidad/Jugador·Organizador), para que "Mi cuenta" y la
    próxima inscripción ya vengan actualizados.
    """
    nombre, apellido = inscripcion.nombre_1.strip(), inscripcion.apellido_1.strip()
    if nombre and apellido and (usuario.first_name != nombre or usuario.last_name != apellido):
        usuario.first_name = nombre
        usuario.last_name = apellido
        usuario.save(update_fields=["first_name", "last_name"])

    identidad = getattr(usuario, "identidad", None)
    if identidad:
        cambios = []
        nuevo_dni = inscripcion.dni_1.strip()
        if nuevo_dni and nuevo_dni != identidad.dni:
            # No pisamos si ese DNI ya es de otra cuenta (lo dejamos como
            # estaba; la inscripción en sí ya quedó guardada igual).
            if not dni_en_uso_por_identidad(nuevo_dni, excluir_pk=identidad.pk):
                identidad.dni = nuevo_dni
                cambios.append("dni")
        nueva_localidad = inscripcion.localidad_1.strip()
        if nueva_localidad and nueva_localidad != identidad.localidad:
            identidad.localidad = nueva_localidad
            cambios.append("localidad")
        if cambios:
            identidad.save(update_fields=cambios)

    nuevo_celular = inscripcion.celular_1.strip()
    jugador = getattr(usuario, "jugador", None)
    if jugador is None:
        # Participa como jugador desde una cuenta que solo tenía perfil de
        # Organizador: se le activa el de Jugador SIN tocar el de Organizador
        # (una misma cuenta puede tener ambos). Arranca sin categoría oficial;
        # se completa más abajo con la que declaró y validó el formulario.
        jugador = Jugador.objects.create(
            usuario=usuario,
            nombre=usuario.get_full_name().strip(),
            celular=nuevo_celular,
        )
    if nuevo_celular:
        if jugador is not None:
            if jugador.celular != nuevo_celular:
                jugador.celular = nuevo_celular
                jugador.save(update_fields=["celular"])
        else:
            organizador = getattr(usuario, "organizador", None)
            if organizador is not None and organizador.celular != nuevo_celular:
                organizador.celular = nuevo_celular
                organizador.save(update_fields=["celular"])

    # La categoría oficial del perfil solo se guarda/mejora acá. El form ya
    # lo validó, pero la regla se vuelve a aplicar (por si el perfil cambió
    # entre que se abrió la pantalla y se confirmó): nunca se baja.
    # Sin categoría oficial, el límite es la mejor ya declarada para esta cuenta
    # (confirmada, por ella o por terceros): no se puede registrar una inferior.
    if jugador is not None and inscripcion.categoria_oficial_1:
        nueva = inscripcion.categoria_oficial_1
        limite = (
            jugador.categoria_oficial
            if jugador.categoria_oficial is not None
            else categoria_minima_declarada(usuario)
        )
        if jugador.categoria_oficial != nueva and puede_subir_categoria(limite, nueva):
            jugador.categoria_oficial = nueva
            jugador.save(update_fields=["categoria_oficial"])

    # La categoría que se declaró para el COMPAÑERO no se guarda en su perfil:
    # queda en la inscripción (dato histórico) hasta que el titular la confirme
    # desde su cuenta. La categoría oficial solo la define su dueño.


@login_required
def mis_inscripciones(request):
    """Todas las inscripciones que hizo la cuenta logueada, sin importar el torneo."""
    # Las que hizo la cuenta Y las que le hizo su pareja (si está registrada y vinculada).
    inscripciones = list(
        inscripciones_de(request.user, incluir_rechazadas=True)
        .select_related("torneo", "categoria", "persona_1", "persona_2")
        .order_by("-creado")
    )
    for insc in inscripciones:
        insc.mi_lado = lado_del_usuario(insc, request.user)
        insc.mi_companero = insc.nombre_completo_2 if insc.mi_lado == 1 else insc.nombre_completo_1
        insc.la_hizo_mi_pareja = insc.mi_lado == 2 and insc.usuario_id != request.user.pk
    return render(request, "sitio/mis_inscripciones.html", {"inscripciones": inscripciones})


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
        with transaction.atomic():
            # Se serializan las inscripciones de ESTE torneo: la validación de duplicados y el guardado
            # ocurren juntos, así dos pedidos simultáneos no pueden colarse ambos (PostgreSQL toma el
            # bloqueo de fila; SQLite ya serializa las escrituras).
            Torneo.objects.select_for_update().get(pk=torneo.pk)
            form = InscripcionForm(request.POST, torneo=torneo, usuario=request.user)
            guardada = form.is_valid()
            if guardada:
                inscripcion = form.save(torneo=torneo)
                _sincronizar_datos_cuenta(request.user, inscripcion)
        if guardada:
            messages.success(
                request,
                "¡Listo! Tu inscripción quedó registrada. "
                "Se confirma cuando el organizador reciba/valide el pago.",
            )
            return redirect("sitio:torneo_detalle", codigo=torneo.codigo)
    else:
        form = InscripcionForm(torneo=torneo, usuario=request.user)

    previas = []
    if request.user.is_authenticated:
        previas = list(
            inscripciones_de(request.user).filter(torneo=torneo).select_related("categoria", "persona_1")
        )
        for e in previas:
            e.mi_lado = lado_del_usuario(e, request.user)
            e.mi_companero = e.nombre_completo_2 if e.mi_lado == 1 else e.nombre_completo_1
    hay_otra_categoria = bool(categorias_disponibles(torneo, previas)) if previas else False
    return render(
        request,
        "sitio/torneo_detalle.html",
        {
            "torneo": torneo, "form": form, "cerrado": cerrado, "inscripciones_previas": previas,
            "bloqueado_por_inscripcion": bool(previas) and not hay_otra_categoria,
            "puede_otra_categoria": bool(previas) and hay_otra_categoria,
        },
    )


def categoria_companero(request):
    """
    Consulta (JSON) usada por el formulario de inscripción apenas se escribe el DNI del
    compañero. Devuelve si tiene cuenta y su categoría oficial; y, SOLO si ya está en la
    plataforma con su nombre, su nombre, apellido y localidad para no volver a pedírselos.
    Nunca devuelve datos de personas sin cuenta ni de DNI ambiguos. Requiere sesión y tiene
    un límite de consultas por minuto: no es un buscador público de DNIs.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "login"}, status=401)
    if _limite_de_consultas_superado(request.user):
        return JsonResponse({"error": "demasiadas_consultas"}, status=429)
    companero = buscar_companero(request.GET.get("dni", ""))
    if companero.usuario is not None and companero.usuario == request.user:
        return JsonResponse({"error": "mismo_usuario"})
    datos = {
        "tiene_cuenta": companero.tiene_cuenta,
        "categoria_oficial": companero.categoria,
        "bloqueada": companero.categoria_bloqueada,
    }
    # Claves extra solo cuando aplican (así no cambia la respuesta de los casos comunes).
    if companero.ambiguo:
        datos["ambiguo"] = True  # DNI en varias cuentas: no se identifica a nadie
    elif companero.categoria_minima is not None:
        datos["categoria_minima"] = companero.categoria_minima
    if companero.tiene_cuenta and not companero.ambiguo:
        u = companero.usuario
        if u.first_name and u.last_name:
            datos["nombre"], datos["apellido"] = u.first_name, u.last_name
            if companero.persona is not None and companero.persona.localidad:
                datos["localidad"] = companero.persona.localidad
    return JsonResponse(datos)


def _limite_de_consultas_superado(usuario, maximo=30, ventana=60):
    """Tope simple de consultas por cuenta y por minuto (frena el barrido de DNIs)."""
    clave = f"consulta_companero:{usuario.pk}"
    cache.add(clave, 0, ventana)
    try:
        return cache.incr(clave) > maximo
    except ValueError:
        cache.set(clave, 1, ventana)
        return False


def _plano(texto):
    """Minúsculas sin tildes ni signos: 'Gómez, Ana' -> 'gomez ana'."""
    sin_tildes = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", "", sin_tildes.lower())


def buscar_companeros(request):
    """
    Búsqueda de compañeros ya registrados en GPADEL, por nombre y apellido (sin importar tildes ni
    el orden) o por DNI exacto. Devuelve hasta 8 coincidencias para ELEGIR una; nunca el DNI de nadie
    (se selecciona por identificador). Solo cuentas con nombre completo, con identidad única y no
    ambigua, y distintas de quien busca. Requiere sesión y tiene límite de consultas.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "login"}, status=401)
    if _limite_de_consultas_superado(request.user, maximo=60):
        return JsonResponse({"error": "demasiadas_consultas"}, status=429)
    q = request.GET.get("q", "").strip()
    if len(q) < 3:
        return JsonResponse({"resultados": []})

    candidatas = (
        Identidad.objects.filter(usuario__isnull=False, dni_en_revision=False)
        .exclude(usuario=request.user).exclude(usuario__first_name="").exclude(usuario__last_name="")
        .select_related("usuario", "usuario__jugador")
    )
    digitos = normalizar_dni(q)
    if not re.search(r"[A-Za-zÁ-ú]", q) and len(digitos) >= 6:
        encontradas = list(candidatas.filter(dni_normalizado=digitos)[:8])
    else:
        palabras = [p for p in _plano(q).split() if len(p) >= 2]
        encontradas = []
        for identidad in candidatas.order_by("usuario__last_name", "usuario__first_name")[:2000]:
            completo = _plano(f"{identidad.usuario.first_name} {identidad.usuario.last_name}")
            if palabras and all(p in completo for p in palabras):
                encontradas.append(identidad)
                if len(encontradas) == 8:
                    break
    return JsonResponse({"resultados": [
        {
            "id": i.pk, "nombre": i.usuario.first_name, "apellido": i.usuario.last_name, "localidad": i.localidad,
            "categoria_oficial": getattr(getattr(i.usuario, "jugador", None), "categoria_oficial", None),
        }
        for i in encontradas
    ]})
