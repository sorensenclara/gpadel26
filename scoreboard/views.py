
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from .models import Match

def index(request):
    return render(request, "scoreboard/index.html")

def new_match(request):
    m = Match.objects.create()
    return redirect("control", code=m.code)

def viewer(request, code):
    match = get_object_or_404(Match, code=code)
    qr_data_uri = None
    try:
        import qrcode
        from io import BytesIO
        import base64

        viewer_url = request.build_absolute_uri(f"/ver/{match.code}/")
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(viewer_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        qr_b64 = base64.b64encode(buf.read()).decode('ascii')
        qr_data_uri = f"data:image/png;base64,{qr_b64}"
    except Exception:
        qr_data_uri = None

    return render(request, "scoreboard/viewer.html", {"match": match, "qr_data_uri": qr_data_uri})

def control(request, code):
    match = get_object_or_404(Match, code=code)
    # Generar QR que apunte a la URL pública del viewer para este match
    qr_data_uri = None
    try:
        import qrcode
        from io import BytesIO
        import base64

        viewer_url = request.build_absolute_uri(f"/ver/{match.code}/")
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(viewer_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        qr_b64 = base64.b64encode(buf.read()).decode('ascii')
        qr_data_uri = f"data:image/png;base64,{qr_b64}"
    except Exception:
        qr_data_uri = None

    return render(request, "scoreboard/control.html", {"match": match, "qr_data_uri": qr_data_uri})

@csrf_exempt
@require_POST
@csrf_exempt
@require_POST
def update_scores(request, code):
    match = get_object_or_404(Match, code=code)
    try:
        # Soporta JSON y form-data
        if request.content_type and "application/json" in request.content_type:
            import json
            payload = json.loads(request.body.decode())
        else:
            payload = request.POST.dict()

        # Persistir a DB lo que corresponde
        if 'team_a' in payload: match.team_a = payload['team_a']
        if 'team_b' in payload: match.team_b = payload['team_b']
        if 'current_set' in payload: match.current_set = int(payload['current_set'])

        if 'sets' in payload and isinstance(payload['sets'], list):
            s = payload['sets']
            def v(i, side):
                try: return int(s[i].get(side, 0))
                except: return 0
            match.set1_a, match.set1_b = v(0,'a'), v(0,'b')
            match.set2_a, match.set2_b = v(1,'a'), v(1,'b')
            match.set3_a, match.set3_b = v(2,'a'), v(2,'b')
        else:
            for name in ['set1_a','set1_b','set2_a','set2_b','set3_a','set3_b']:
                if name in payload:
                    setattr(match, name, int(payload[name]))

        match.save()

        # --- Armar payload para WS, incluyendo current_game ---
        out = match.as_payload()  # tu dict habitual (team_a, team_b, sets, current_set, ...)
        cg = payload.get("current_game")
        if isinstance(cg, dict):
            # control manda p.ej. {mode:'regular'|'tiebreak', a:..., b:...}
            try:
                a = int(cg.get("a", 0))
                b = int(cg.get("b", 0))
            except:
                a = b = 0
            out["current_game"] = {"mode": cg.get("mode", "regular"), "a": a, "b": b}
        else:
            out["current_game"] = {"mode": "regular", "a": 0, "b": 0}

        # Broadcast → llama a MatchConsumer.match_update
        layer = get_channel_layer()
        async_to_sync(layer.group_send)(
            f"match_{match.code}",
            {"type": "match_update", "payload": out}
        )

        return JsonResponse({"ok": True, "match": out})

    except Exception as e:
        return HttpResponseBadRequest(str(e))


# ...existing code...
        if 'sets' in payload and isinstance(payload['sets'], list):
            s = payload['sets']
            def v(i, side):
                try: return int(s[i].get(side, 0))
                except: return 0
            match.set1_a, match.set1_b = v(0,'a'), v(0,'b')
            match.set2_a, match.set2_b = v(1,'a'), v(1,'b')
            match.set3_a, match.set3_b = v(2,'a'), v(2,'b')
        else:
            for name in ['set1_a','set1_b','set2_a','set2_b','set3_a','set3_b']:
                if name in payload:
                    setattr(match, name, int(payload[name]))

        # --- Persistir sponsors (si vienen en el payload) ---
        if 'sponsors' in payload:
            sp = payload['sponsors']
            if isinstance(sp, list):
                match.sponsors = sp
            else:
                try:
                    import json
                    parsed = json.loads(sp)
                    if isinstance(parsed, list):
                        match.sponsors = parsed
                except Exception:
                    # si no se puede parsear, ignoramos para evitar fallos
                    pass

        match.save()
# ...existing code...
