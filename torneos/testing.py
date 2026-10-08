"""Fábricas mínimas para tests (torneos y competencia)."""
import datetime
import itertools

from django.contrib.auth import get_user_model

from accounts.models import Identidad, Jugador, Organizador, normalizar_dni

from .models import Categoria, Inscripcion, Torneo

_n = itertools.count(1)


def crear_usuario(dni=None, categoria_oficial=None, username=None):
    User = get_user_model()
    n = next(_n)
    user = User.objects.create_user(username=username or f"u{n}", password="x")
    if dni:
        # Un DNI equivalente a otro ya existente solo puede darse en datos HISTÓRICOS; la base lo
        # admite únicamente si todas esas identidades están marcadas "en revisión", igual que
        # las deja la migración de datos (accounts.0009). Esta fábrica reproduce ese estado.
        normalizado = normalizar_dni(dni)
        previas = Identidad.objects.filter(dni_normalizado=normalizado) if normalizado else Identidad.objects.none()
        duplicado = previas.exists()
        if duplicado:
            previas.update(dni_en_revision=True)
        Identidad.objects.create(usuario=user, dni=dni, localidad="Tandil", dni_en_revision=duplicado)
        Jugador.objects.create(usuario=user, nombre=user.username, celular="1", categoria_oficial=categoria_oficial)
    return user


def crear_categoria(nombre="6ta LIBRE", **extra):
    n = next(_n)
    owner = get_user_model().objects.create_user(username=f"org{n}", password="x")
    org = Organizador.objects.create(usuario=owner, rol="liga", nombre_liga=f"Liga {n}", celular="0")
    hoy = datetime.date.today()
    torneo = Torneo.objects.create(
        organizador=org, nombre=f"Torneo {n}", ciudad="Tandil",
        fecha_inicio=hoy + datetime.timedelta(days=20), fecha_fin=hoy + datetime.timedelta(days=21),
        fecha_limite_inscripcion=hoy + datetime.timedelta(days=10), precio_inscripcion=0,
        formato=Torneo.FORMATO_SOLO_ELIMINACION, estado=Torneo.ESTADO_PUBLICADO,
    )
    return Categoria.objects.create(torneo=torneo, nombre=nombre, cupo_minimo=1, **extra)


def crear_inscripcion(categoria, usuario=None, **extra):
    n = next(_n)
    datos = dict(
        torneo=categoria.torneo, categoria=categoria, usuario=usuario,
        nombre_1=f"Jugador {n}", dni_1=f"1000{n}", celular_1="1", localidad_1="Tandil",
        categoria_oficial_1=6, nombre_2=f"Compañero {n}", dni_2=f"2000{n}", localidad_2="Tandil",
        categoria_oficial_2=6, telefono="1", estado=Inscripcion.ESTADO_CONFIRMADA,
    )
    datos.update(extra)
    return Inscripcion.objects.create(**datos)
