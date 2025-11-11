from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from .models import Match
import json
import base64
from io import BytesIO
import qrcode


def index(request):
    return render(request, "scoreboard/index.html")


def new_match(request):
    m = Match.objects.create()
    return redirect("choose_mode", code=m.code)


def choose_mode(request, code):
    """
    Pantalla para elegir modalidad del partido:
    - 3 sets
    - 2 sets + super tie-break
    """
    match = get_object_or_404(Match, code=code)

    if request.method == "POST":
        mode = request.POST.get("mode", "3_sets")
        match.mode = mode
        match.save()
        return redirect("control", code=match.code)

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

    return render(request, "scoreboard/viewer.html", {"match": match, "qr_data_uri": qr_data_uri})


def control(request, code):
    """Panel de control (operador del partido)."""
    match = get_object_or_404(Match, code=code)

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

    super_tb = match.check_super_tiebreak()

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
    """
    match = get_object_or_404(Match, code=code)
    try:
        # Parsear payload
        if request.content_type and "application/json" in request.content_type:
            payload = json.loads(request.body.decode("utf-8"))
        else:
            payload = request.POST.dict()

        # Actualizar campos básicos
        if "team_a" in payload:
            match.team_a = payload["team_a"]
        if "team_b" in payload:
            match.team_b = payload["team_b"]
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
                    return int(sets_data[i].get(side, 0))
                except Exception:
                    return 0

            match.set1_a, match.set1_b = v(0, "a"), v(0, "b")
            match.set2_a, match.set2_b = v(1, "a"), v(1, "b")
            match.set3_a, match.set3_b = v(2, "a"), v(2, "b")

        # Sponsors
        sp = payload.get("sponsors")
        if isinstance(sp, list):
            match.sponsors = sp
        elif isinstance(sp, str):
            try:
                parsed = json.loads(sp)
                if isinstance(parsed, list):
                    match.sponsors = parsed
            except Exception:
                pass

        match.save()

        # Armar payload WS
        out = match.as_payload()
        cg = payload.get("current_game") or {}
        try:
            cg_a = int(cg.get("a", 0) or 0)
            cg_b = int(cg.get("b", 0) or 0)
        except Exception:
            cg_a = cg_b = 0

        out["current_game"] = {
            "mode": cg.get("mode", "regular"),
            "a": cg_a,
            "b": cg_b,
        }

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
