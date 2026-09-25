"""
Comando idempotente para cargar datos de prueba: usuarios de los 3 tipos
(Jugador, Dueño de cancha, Representante de Liga) y torneos ya armados,
algunos publicados con inscripciones, para poder probar el sistema sin
tener que cargar todo a mano.

Uso:
    python manage.py seed_demo

Se puede correr las veces que quieras: usa get_or_create en todos lados,
así que no duplica nada si ya existen los datos.
"""

import datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounts.models import Jugador, Organizador
from reservas.models import Cancha, Reserva
from torneos.models import Categoria, Inscripcion, Torneo

User = get_user_model()
PASSWORD_DEMO = "demo1234"


class Command(BaseCommand):
    help = "Carga usuarios y torneos de prueba (idempotente, no duplica si ya existen)."

    def handle(self, *args, **options):
        hoy = datetime.date.today()

        # ---------- Jugadores ----------
        jugadores = []
        for i, (username, nombre, celular) in enumerate(
            [
                ("jugador1", "Juan Pérez", "2494100001"),
                ("jugador2", "Ana Gómez", "2494100002"),
                ("jugador3", "Carlos Ruiz", "2494100003"),
            ]
        ):
            user, creado = User.objects.get_or_create(
                username=username, defaults={"email": f"{username}@demo.gpadel.com.ar"}
            )
            if creado:
                user.set_password(PASSWORD_DEMO)
                user.save()
            jugador, _ = Jugador.objects.get_or_create(
                usuario=user, defaults={"nombre": nombre, "celular": celular}
            )
            jugadores.append(jugador)
        self.stdout.write(self.style.SUCCESS(f"Jugadores listos: {len(jugadores)}"))

        # ---------- Organizadores ----------
        organizadores = {}

        def crear_organizador(username, rol, nombre_cancha="", nombre_liga="", celular="2494200000"):
            user, creado = User.objects.get_or_create(
                username=username, defaults={"email": f"{username}@demo.gpadel.com.ar"}
            )
            if creado:
                user.set_password(PASSWORD_DEMO)
                user.save()
            organizador, _ = Organizador.objects.get_or_create(
                usuario=user,
                defaults={
                    "rol": rol,
                    "celular": celular,
                    "nombre_cancha": nombre_cancha,
                    "nombre_liga": nombre_liga,
                    "tiene_cancha": bool(nombre_cancha),
                    "es_liga": bool(nombre_liga),
                },
            )
            organizadores[username] = organizador
            return organizador

        crear_organizador(
            "cancha_tandil", Organizador.ROL_DUENO_CANCHA, nombre_cancha="Cancha Tandil Padel"
        )
        crear_organizador(
            "liga_sancayetano",
            Organizador.ROL_LIGA,
            nombre_liga="Liga de Pádel San Cayetano",
        )
        # Organizador "cruzado": Dueño de cancha que también es Liga
        crear_organizador(
            "cancha_necochea",
            Organizador.ROL_DUENO_CANCHA,
            nombre_cancha="Complejo Necochea",
            nombre_liga="Liga Costa Atlántica",
        )
        self.stdout.write(self.style.SUCCESS(f"Organizadores listos: {len(organizadores)}"))

        # ---------- Canchas ----------
        canchas = []

        def crear_cancha(organizador, nombre, ciudad, precio_hora, techada, apertura="08:00", cierre="23:00"):
            cancha, _ = Cancha.objects.get_or_create(
                organizador=organizador,
                nombre=nombre,
                defaults={
                    "ciudad": ciudad,
                    "precio_hora": precio_hora,
                    "techada": techada,
                    "hora_apertura": apertura,
                    "hora_cierre": cierre,
                },
            )
            canchas.append(cancha)
            return cancha

        cancha1 = crear_cancha(
            organizadores["cancha_tandil"], "Cancha 1", "Tandil, Buenos Aires", 8000, techada=True
        )
        crear_cancha(
            organizadores["cancha_tandil"], "Cancha 2", "Tandil, Buenos Aires", 7500, techada=False
        )
        crear_cancha(
            organizadores["cancha_necochea"], "Cancha Central", "Necochea, Buenos Aires", 9000, techada=True
        )
        self.stdout.write(self.style.SUCCESS(f"Canchas listas: {len(canchas)}"))

        # Una reserva de ejemplo, para ver un turno ya ocupado
        if jugadores:
            Reserva.objects.get_or_create(
                cancha=cancha1,
                usuario=jugadores[0].usuario,
                fecha=hoy + datetime.timedelta(days=1),
                hora_inicio="17:00:00",
                defaults={"hora_fin": "18:30:00"},
            )

        # ---------- Torneos ----------
        def crear_torneo(organizador, nombre, ciudad, lat, lon, dias_inicio, formato, precio, estado, categorias):
            torneo, creado = Torneo.objects.get_or_create(
                organizador=organizador,
                nombre=nombre,
                defaults={
                    "ciudad": ciudad,
                    "latitud": lat,
                    "longitud": lon,
                    "fecha_inicio": hoy + datetime.timedelta(days=dias_inicio),
                    "fecha_fin": hoy + datetime.timedelta(days=dias_inicio + 1),
                    "fecha_limite_inscripcion": hoy + datetime.timedelta(days=dias_inicio - 5),
                    "formato": formato,
                    "precio_inscripcion": precio,
                    "estado": estado,
                },
            )
            if creado:
                for orden, (nombre_cat, cupo) in enumerate(categorias):
                    Categoria.objects.get_or_create(
                        torneo=torneo, nombre=nombre_cat, defaults={"cupo_minimo": cupo, "orden": orden}
                    )
            return torneo

        torneo1 = crear_torneo(
            organizadores["liga_sancayetano"],
            "4ta Edición Liga Pádel",
            "San Cayetano, Buenos Aires",
            "-38.3527780",
            "-59.8333330",
            dias_inicio=20,
            formato=Torneo.FORMATO_GRUPOS_ELIMINACION,
            precio=8000,
            estado=Torneo.ESTADO_PUBLICADO,
            categorias=[("5ta LIBRE", 3), ("6ta LIBRE", 3), ("8va DAMAS", 3)],
        )

        torneo2 = crear_torneo(
            organizadores["cancha_tandil"],
            "Apertura Cancha Tandil",
            "Tandil, Buenos Aires",
            "-37.3216930",
            "-59.1332560",
            dias_inicio=35,
            formato=Torneo.FORMATO_SOLO_ELIMINACION,
            precio=6000,
            estado=Torneo.ESTADO_PUBLICADO,
            categorias=[("4ta LIBRE", 4), ("7ma LIBRE", 4)],
        )

        crear_torneo(
            organizadores["cancha_necochea"],
            "Torneo Costa Atlántica (borrador)",
            "Necochea, Buenos Aires",
            "-38.5545000",
            "-58.7395000",
            dias_inicio=45,
            formato=Torneo.FORMATO_GRUPOS_ELIMINACION,
            precio=7000,
            estado=Torneo.ESTADO_BORRADOR,
            categorias=[("3ra LIBRE", 3), ("1ra DAMAS", 3)],
        )

        self.stdout.write(self.style.SUCCESS("Torneos listos: 3 (2 publicados, 1 en borrador)"))

        # ---------- Inscripciones de ejemplo (en el torneo publicado de San Cayetano) ----------
        cat_5ta = torneo1.categorias.get(nombre="5ta LIBRE")
        Inscripcion.objects.get_or_create(
            torneo=torneo1,
            categoria=cat_5ta,
            nombre_1="Juan Pérez",
            nombre_2="Martín López",
            defaults={
                "dni_1": "30111222",
                "localidad_1": "San Cayetano",
                "dni_2": "30333444",
                "localidad_2": "San Cayetano",
                "telefono": "2494100001",
                "metodo_pago": Inscripcion.PAGO_TRANSFERENCIA,
                "estado": Inscripcion.ESTADO_CONFIRMADA,
            },
        )
        Inscripcion.objects.get_or_create(
            torneo=torneo1,
            categoria=cat_5ta,
            nombre_1="Ana Gómez",
            nombre_2="Lucía Fernández",
            defaults={
                "dni_1": "30555666",
                "localidad_1": "Tres Arroyos",
                "dni_2": "30777888",
                "localidad_2": "Tres Arroyos",
                "telefono": "2494100002",
                "metodo_pago": Inscripcion.PAGO_EFECTIVO,
                "estado": Inscripcion.ESTADO_PENDIENTE,
            },
        )
        self.stdout.write(self.style.SUCCESS("Inscripciones de ejemplo listas: 2"))

        # ---------- Resumen ----------
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Listo. Credenciales de prueba (todas con la misma contraseña):"))
        self.stdout.write(f"  Contraseña para todos los usuarios: {PASSWORD_DEMO}")
        self.stdout.write("  Jugadores: jugador1, jugador2, jugador3")
        self.stdout.write("  Dueño de cancha: cancha_tandil")
        self.stdout.write("  Representante de Liga: liga_sancayetano")
        self.stdout.write("  Dueño de cancha + Liga (cruzado): cancha_necochea")
        self.stdout.write(f"  Canchas cargadas: {len(canchas)} (Tandil x2, Necochea x1)")
