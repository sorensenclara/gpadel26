import datetime

from django.conf import settings
from django.db import models

from accounts.models import Organizador


class Cancha(models.Model):
    """Una cancha de pádel que un Organizador (Dueño de cancha) pone a
    disposición para reservar."""

    organizador = models.ForeignKey(Organizador, related_name="canchas", on_delete=models.CASCADE)
    nombre = models.CharField(max_length=100)
    ciudad = models.CharField(max_length=120)
    direccion = models.CharField(max_length=200, blank=True)
    techada = models.BooleanField(default=False)
    precio_hora = models.DecimalField(max_digits=10, decimal_places=2)

    hora_apertura = models.TimeField(default=datetime.time(8, 0))
    hora_cierre = models.TimeField(default=datetime.time(23, 0))
    duracion_turno_minutos = models.PositiveIntegerField(default=90)

    activa = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ciudad", "nombre"]

    def __str__(self):
        return f"{self.nombre} ({self.ciudad})"

    def turnos_del_dia(self, fecha):
        """Lista de dicts con hora_inicio/hora_fin/disponible generada
        desde apertura/cierre/duración, cruzada contra las reservas
        confirmadas de ese día."""
        ocupados = set(
            self.reservas.filter(fecha=fecha, estado=Reserva.ESTADO_CONFIRMADA).values_list(
                "hora_inicio", flat=True
            )
        )
        turnos = []
        cursor = datetime.datetime.combine(fecha, self.hora_apertura)
        cierre = datetime.datetime.combine(fecha, self.hora_cierre)
        paso = datetime.timedelta(minutes=self.duracion_turno_minutos)
        while cursor + paso <= cierre:
            hora_inicio = cursor.time()
            hora_fin = (cursor + paso).time()
            turnos.append(
                {
                    "hora_inicio": hora_inicio,
                    "hora_fin": hora_fin,
                    "disponible": hora_inicio not in ocupados,
                }
            )
            cursor += paso
        return turnos


class Reserva(models.Model):
    ESTADO_CONFIRMADA = "confirmada"
    ESTADO_CANCELADA = "cancelada"
    ESTADO_CHOICES = [
        (ESTADO_CONFIRMADA, "Confirmada"),
        (ESTADO_CANCELADA, "Cancelada"),
    ]

    cancha = models.ForeignKey(Cancha, related_name="reservas", on_delete=models.CASCADE)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="reservas", on_delete=models.CASCADE
    )
    fecha = models.DateField()
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=ESTADO_CONFIRMADA)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-hora_inicio"]

    def __str__(self):
        return f"{self.cancha} — {self.fecha} {self.hora_inicio}"
