from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from .models import Match  # Asumiendo que existe el modelo Match
import json
import base64
from io import BytesIO
import qrcode

# NUEVO: para guardar archivos
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
import os
import uuid


def index(request):
    return render(request, "scoreboard/index.html")


def _get_owned_match_or_403(request, code):
    """Trae el match por code y valida que sea del organizador/jugador logueado.

    Los matches viejos (organizador=None y jugador=None, creados antes de
    la Etapa 1) quedan accesibles para cualquier Organizador logueado, para
    no romper partidos ya en curso.
    """
    match = get_object_or_404(Match, code=code)
    organizador = getattr(request.user, "organizador", None)
    jugador = getattr(request.user, "jugador", None)

    if match.organizador_id:
        if not organizador or match.organizador_id != organizador.id:
            raise PermissionDenied("Este marcador pertenece a otro Organizador.")
    elif match.jugador_id:
        if not jugador or match.jugador_id != jugador.id:
            raise PermissionDenied("Este marcador pertenece a otro Jugador.")
    elif not organizador and not jugador:
        raise PermissionDenied("Tu cuenta no tiene acceso a este marcador.")

    return match


@login_required
def new_match(request):
    """Crear marcador desde una cuenta de Organizador (Dueño de cancha / Liga)."""
    organizador = getattr(request.user, "organizador", None)
    if not organizador:
        raise PermissionDenied(
            "Tu cuenta no tiene perfil de Organizador. Registrate como "
            "Dueño de cancha o Representante de Liga para crear un marcador."
        )
    m = Match.objects.create(organizador=organizador)
    return redirect("scoreboard:personalizar", code=m.code)


@login_required
def new_match_auto(request):
    """
    Destino único del botón "Crear marcador" público: una vez logueado
    (directo, o justo después de loguearse en el modal), decide a qué
    flujo mandarlo según el/los perfil(es) de la cuenta.
    """
    if hasattr(request.user, "jugador"):
        return redirect("scoreboard:new_match_jugador")
    if hasattr(request.user, "organizador"):
        return redirect("scoreboard:new_match")
    raise PermissionDenied(
        "Tu cuenta todavía no tiene un perfil de Jugador ni de Organizador."
    )


@login_required
def new_match_jugador(request):
    """
    Crear marcador desde una cuenta de Jugador: siempre gratis, sin
    personalizar, con la identidad fija de GPADEL (azul, logo GPADEL,
    Titillium). Salta el paso de personalización.
    """
    jugador = getattr(request.user, "jugador", None)
    if not jugador:
        raise PermissionDenied(
            "Tu cuenta no es de Jugador. Registrate como Jugador para usar "
            "el marcador gratuito de GPADEL."
        )
    m = Match.objects.create(
        jugador=jugador,
        personalizado=False,
        color_principal=Match.GPADEL_COLOR,
        tipografia=Match.FONT_SANS,
    )
    return redirect("scoreboard:choose_mode", code=m.code)


@login_required
def personalizar(request, code):
    """
    Paso de identidad visual del marcador (solo Organizador). Si elige "no
    personalizar", el marcador queda con la identidad fija de GPADEL.
    """
    match = _get_owned_match_or_403(request, code)

    if request.method == "POST":
        personalizado = request.POST.get("personalizado") == "si"
        match.personalizado = personalizado
        match.nombre_creador = request.POST.get("nombre_creador", "").strip()

        if personalizado:
            color = (request.POST.get("color_principal") or "").strip()
            if color:
                match.color_principal = color
            tipografia = request.POST.get("tipografia")
            if tipografia in dict(Match.FONT_CHOICES):
                match.tipografia = tipografia
            logo_file = request.FILES.get("logo")
            if logo_file:
                match.logo = logo_file
        else:
            match.color_principal = Match.GPADEL_COLOR
            match.tipografia = Match.FONT_SANS
            match.logo = None

        match.save()
        return redirect("scoreboard:choose_mode", code=match.code)

    return render(request, "scoreboard/personalizar.html", {"match": match})


@login_required
def choose_mode(request, code):
    """
    Pantalla para elegir modalidad del partido:
    - 3 sets
    - 2 sets + super tie-break
    """
    match = _get_owned_match_or_403(request, code)

    if request.method == "POST":
        mode = request.POST.get("mode", "3_sets")
        match.mode = mode
        match.save()
        return redirect("scoreboard:control", code=match.code)

    return render(request, "scoreboard/choose_mode.html", {"match": match})


def viewer(request, code):
    """Vista pública del marcador."""
    match = get_object_or_404(Match, code=code)
    qr_data_uri = None
    try:
        viewer_url = request.build_absolute_uri(f"/ver/{match.code}/")
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(viewer_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        qr_data_uri = f"data:image/png;base64,{qr_b64}"
    except Exception:
        qr_data_uri = None

    return render(
        request,
        "scoreboard/viewer.html",
        {"match": match, "qr_data_uri": qr_data_uri},
    )


@login_required
def control(request, code):
    """Panel de control (operador del partido)."""
    match = _get_owned_match_or_403(request, code)

    # Generar QR del viewer
    qr_data_uri = None
    try:
        viewer_url = request.build_absolute_uri(f"/ver/{match.code}/")
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(viewer_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        qr_data_uri = f"data:image/png;base64,{qr_b64}"
    except Exception:
        qr_data_uri = None

    super_tb = match.check_super_tiebreak()  # Asumiendo que esta función existe en el modelo

    return render(
        request,
        "scoreboard/control.html",
        {"match": match, "qr_data_uri": qr_data_uri, "super_tiebreak": super_tb},
    )


@csrf_exempt
@require_POST
def update_scores(request, code):
    """
    Recibe actualización del control.html (JSON) → guarda en BD → envía por WebSocket.
    Solo el Organizador dueño del partido (o cualquier Organizador logueado,
    para partidos viejos sin dueño) puede actualizar el score.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Sesión expirada. Volvé a iniciar sesión."}, status=401)
    try:
        match = _get_owned_match_or_403(request, code)
    except PermissionDenied as e:
        return JsonResponse({"error": str(e)}, status=403)
    try:
        # Parsear el cuerpo de la petición (JSON)
        if request.content_type and "application/json" in request.content_type:
            data = json.loads(request.body.decode("utf-8"))
        else:
            # Usar request.POST.dict() como fallback si no es JSON
            data = request.POST.dict()

        # ⭐ Extraer el 'payload' interno (que envía control.html), o usar 'data' si no está anidado
        payload = data.get("payload") or data

        # Actualizar campos básicos
        if "team_a" in payload:
            match.team_a = payload["team_a"]
        if "team_b" in payload:
            match.team_b = payload["team_b"]
        if "a1_last" in payload:
            match.a1_last = payload["a1_last"]
        if "a2_last" in payload:
            match.a2_last = payload["a2_last"]
        if "b1_last" in payload:
            match.b1_last = payload["b1_last"]
        if "b2_last" in payload:
            match.b2_last = payload["b2_last"]
        if "a1_full" in payload:
            match.a1_full = payload["a1_full"]
        if "a2_full" in payload:
            match.a2_full = payload["a2_full"]
        if "b1_full" in payload:
            match.b1_full = payload["b1_full"]
        if "b2_full" in payload:
            match.b2_full = payload["b2_full"]
        if "current_set" in payload:
            try:
                match.current_set = int(payload["current_set"])
            except ValueError:
                pass

        # Sets
        sets_data = payload.get("sets")
        if isinstance(sets_data, list):
            def v(i, side):
                try:
                    val = sets_data[i].get(side, 0)
                    return int(val) if val else 0
                except Exception:
                    return 0

            match.set1_a, match.set1_b = v(0, "a"), v(0, "b")
            match.set2_a, match.set2_b = v(1, "a"), v(1, "b")
            match.set3_a, match.set3_b = v(2, "a"), v(2, "b")

        # ⭐ SPONSORS: ahora vienen como lista de URLs (strings)
        sp = payload.get("sponsors")
        if isinstance(sp, list):
            match.sponsors = sp

        # Puntos a mostrar
        if "showPoints" in payload:
            match.show_points = payload["showPoints"]

        match.save()

        # Armar payload WS
        out = match.as_payload()
        cg = payload.get("current_game") or {}

        out["current_game"] = {
            "mode": cg.get("mode", "regular"),
            "a": cg.get("a", 0),
            "b": cg.get("b", 0),
        }

        # Información de partido finalizado
        finished = payload.get("finished")
        winner = payload.get("winner")
        out["finished"] = bool(finished)
        out["winner"] = winner or ""
        out["show_points"] = match.show_points

        # Broadcast WebSocket
        layer = get_channel_layer()
        async_to_sync(layer.group_send)(
            f"match_{match.code}",
            {"type": "match_update", "payload": out},
        )

        return JsonResponse({"ok": True, "match": out})
    except Exception as e:
        return HttpResponseBadRequest(str(e))


@require_GET
def get_state(request, code):
    """Devuelve el estado actual del partido (para viewer.html si entra antes del WS)."""
    match = get_object_or_404(Match, code=code)
    try:
        out = match.as_payload()
        return JsonResponse(out)
    except Exception as e:
        return HttpResponseBadRequest(str(e))


# ⚠️ NUEVO: endpoint para subir sponsors
@csrf_exempt
@require_POST
def upload_sponsor(request):
    """
    Recibe un archivo 'file' (imagen), lo guarda en /media/sponsors/
    y devuelve {"url": "/media/sponsors/xxx.png"}
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Sesión expirada. Volvé a iniciar sesión."}, status=401)

    file = request.FILES.get("file")
    if not file:
        return JsonResponse({"error": "Falta archivo 'file'"}, status=400)

    # Extensión segura
    ext = os.path.splitext(file.name)[1].lower()
    if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        ext = ".png"

    filename = f"{uuid.uuid4()}{ext}"
    path = os.path.join("sponsors", filename)

    default_storage.save(path, ContentFile(file.read()))
    url = f"/media/{path}"

    return JsonResponse({"url": url})
