"""
Dominio de la competencia dentro de un torneo.

Una `torneos.Categoria` ES la competencia (por categoría o la única SUMA N).
Acá vive todo lo que cuelga de ella: propuestas de zonas, zonas definitivas,
partidos, resultados, llave y auditoría.

Principios:
- Las PROPUESTAS son datos independientes (PropuestaFase → PropuestaZona →
  PropuestaAsignacion). Generarlas o compararlas no modifica nada de las
  inscripciones: la zona definitiva (Zona/ZonaPareja) recién se crea cuando el
  organizador selecciona una propuesta.
- Un Resultado no cuenta para puntos ni posiciones hasta ser OFICIAL.
- El estado de publicación (grupos / llave) vive en `Categoria`, no acá, para
  tener una única fuente de verdad.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from torneos.models import Categoria, Inscripcion

from .sets import resumen_sets, validar_sets

User = settings.AUTH_USER_MODEL


# ---------------------------------------------------------------------------
# Propuestas de organización de la fase de grupos (borrador, comparables)
# ---------------------------------------------------------------------------
class PropuestaFase(models.Model):
    """Una alternativa completa de zonas ("Opción A") para una competencia."""

    categoria = models.ForeignKey(
        Categoria, on_delete=models.CASCADE, related_name="propuestas_fase"
    )
    nombre = models.CharField(max_length=30, help_text='Ej.: "Opción A".')
    cantidad_zonas = models.PositiveSmallIntegerField()
    distribucion = models.JSONField(
        default=list, help_text="Parejas por zona, ej.: [4, 4, 3, 3]."
    )
    total_partidos = models.PositiveIntegerField(default=0)
    compatibilidad = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="0–100. Nulo si no había disponibilidad suficiente para calcularla.",
    )
    # --- Ranking usado para priorizar la distribución (opcional) ------------------------
    # Se guarda la VERSIÓN EXACTA (inmutable) y una foto de los puntos usados, para que la
    # distribución aprobada no cambie retroactivamente aunque el ranking se actualice después.
    # El puntaje de cada pareja sale de UNA sola versión: nunca se mezclan rankings.
    CRITERIO_DISPONIBILIDAD = "disponibilidad"
    CRITERIO_RANKING = "ranking_luego_disponibilidad"
    CRITERIO_CHOICES = [
        (CRITERIO_DISPONIBILIDAD, "Solo disponibilidad horaria"),
        (CRITERIO_RANKING, "Ranking primero y disponibilidad después"),
    ]
    ranking_version = models.ForeignKey(
        "rankings.VersionRanking", null=True, blank=True, on_delete=models.PROTECT,
        related_name="propuestas_fase", help_text="Vacía = sin ranking.",
    )
    criterio_prioridad = models.CharField(max_length=30, choices=CRITERIO_CHOICES, default=CRITERIO_DISPONIBILIDAD)
    puntajes_snapshot = models.JSONField(
        default=dict, blank=True,
        help_text="Foto de los puntos usados por pareja (incluye quién quedó 'sin ranking'), al generar.",
    )

    generada_en = models.DateTimeField(auto_now_add=True)
    seleccionada_en = models.DateTimeField(null=True, blank=True)
    seleccionada_por = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["categoria_id", "nombre"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(criterio_prioridad="ranking_luego_disponibilidad") | Q(ranking_version__isnull=False),
                name="propuesta_criterio_ranking_requiere_version",
            ),
            models.UniqueConstraint(
                fields=["categoria", "nombre"], name="propuesta_nombre_unico_por_categoria"
            ),
            # A lo sumo UNA propuesta seleccionada por competencia.
            models.UniqueConstraint(
                fields=["categoria"],
                condition=Q(seleccionada_en__isnull=False),
                name="propuesta_una_seleccionada_por_categoria",
            ),
        ]

    def __str__(self):
        return f"{self.nombre} — {self.categoria}"

    @property
    def seleccionada(self):
        return self.seleccionada_en is not None


class PropuestaZona(models.Model):
    """Una zona dentro de una propuesta (todavía no es una zona real)."""

    propuesta = models.ForeignKey(PropuestaFase, on_delete=models.CASCADE, related_name="zonas")
    nombre = models.CharField(max_length=10, help_text='Ej.: "A".')
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["propuesta_id", "orden", "nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["propuesta", "nombre"], name="propuesta_zona_nombre_unico"
            ),
        ]

    def __str__(self):
        return f"Zona {self.nombre} — {self.propuesta}"


class PropuestaAsignacion(models.Model):
    """
    Qué pareja (Inscripcion) queda en qué zona de UNA propuesta. Es la entidad
    intermedia que permite guardar varias propuestas completas a la vez sin
    tocar la zona definitiva de ninguna inscripción.
    """

    propuesta = models.ForeignKey(
        PropuestaFase, on_delete=models.CASCADE, related_name="asignaciones",
        editable=False,
        help_text="Redundante con propuesta_zona.propuesta (se completa solo): permite "
        "garantizar con una restricción que una pareja aparece una sola vez por propuesta.",
    )
    propuesta_zona = models.ForeignKey(
        PropuestaZona, on_delete=models.CASCADE, related_name="asignaciones"
    )
    inscripcion = models.ForeignKey(
        Inscripcion, on_delete=models.CASCADE, related_name="asignaciones_propuesta"
    )
    orden = models.PositiveSmallIntegerField(default=0)
    # Suma de los puntos de los dos integrantes en la versión de ranking de la propuesta.
    # Vacío si no se usó ranking o si algún integrante está "sin ranking" (SR): en ese caso
    # `ranking_incompleto` es verdadero y NO se inventa una suma parcial ni un cero.
    puntaje_pareja = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    ranking_incompleto = models.BooleanField(default=False)

    class Meta:
        ordering = ["propuesta_zona_id", "orden"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(ranking_incompleto=True) | Q(puntaje_pareja__isnull=True),
                name="asignacion_incompleta_sin_puntaje",
            ),
            models.UniqueConstraint(
                fields=["propuesta", "inscripcion"], name="asignacion_pareja_unica_por_propuesta"
            ),
        ]

    def clean(self):
        if self.propuesta_zona_id and self.inscripcion_id:
            categoria_id = self.propuesta_zona.propuesta.categoria_id
            if self.inscripcion.categoria_id != categoria_id:
                raise ValidationError("La pareja pertenece a otra categoría.")

    def save(self, *args, **kwargs):
        self.propuesta_id = self.propuesta_zona.propuesta_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.inscripcion} → {self.propuesta_zona}"


# ---------------------------------------------------------------------------
# Zonas definitivas (se crean al seleccionar una propuesta)
# ---------------------------------------------------------------------------
class Zona(models.Model):
    categoria = models.ForeignKey(Categoria, on_delete=models.CASCADE, related_name="zonas")
    nombre = models.CharField(max_length=10)
    orden = models.PositiveSmallIntegerField(default=0)
    propuesta_origen = models.ForeignKey(
        PropuestaFase, null=True, blank=True, on_delete=models.SET_NULL, related_name="zonas_creadas",
        help_text="Propuesta aprobada de la que salió esta zona (informativo).",
    )
    creada_en = models.DateTimeField(auto_now_add=True)
    creada_por = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["categoria_id", "orden", "nombre"]
        constraints = [
            models.UniqueConstraint(fields=["categoria", "nombre"], name="zona_nombre_unico_por_categoria"),
        ]

    def __str__(self):
        return f"Zona {self.nombre} — {self.categoria}"


class ZonaPareja(models.Model):
    """Pareja integrante de una zona definitiva. Una pareja, una sola zona."""

    zona = models.ForeignKey(Zona, on_delete=models.CASCADE, related_name="integrantes")
    inscripcion = models.OneToOneField(
        Inscripcion, on_delete=models.CASCADE, related_name="zona_asignada"
    )
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["zona_id", "orden"]

    def clean(self):
        if self.zona_id and self.inscripcion_id and self.inscripcion.categoria_id != self.zona.categoria_id:
            raise ValidationError("La pareja pertenece a otra categoría.")

    def __str__(self):
        return f"{self.inscripcion} en {self.zona}"


# ---------------------------------------------------------------------------
# Llave eliminatoria
# ---------------------------------------------------------------------------
class Llave(models.Model):
    """Estructura eliminatoria de una competencia. Su visibilidad
    (borrador/publicada) está en Categoria.llave_publicacion."""

    categoria = models.OneToOneField(Categoria, on_delete=models.CASCADE, related_name="llave")
    generada_en = models.DateTimeField(auto_now_add=True)
    generada_por = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    def __str__(self):
        return f"Llave — {self.categoria}"


# ---------------------------------------------------------------------------
# Partidos
# ---------------------------------------------------------------------------
class Partido(models.Model):
    """Modelo único para fase de grupos y eliminatoria."""

    TIPO_GRUPO = "grupo"
    TIPO_ELIMINATORIA = "eliminatoria"
    TIPO_CHOICES = [(TIPO_GRUPO, "Fase de grupos"), (TIPO_ELIMINATORIA, "Eliminatoria")]

    RONDA_OCTAVOS = "octavos"
    RONDA_CUARTOS = "cuartos"
    RONDA_SEMIFINAL = "semifinal"
    RONDA_FINAL = "final"
    RONDA_CHOICES = [
        (RONDA_OCTAVOS, "Octavos de final"),
        (RONDA_CUARTOS, "Cuartos de final"),
        (RONDA_SEMIFINAL, "Semifinal"),
        (RONDA_FINAL, "Final"),
    ]

    ESTADO_PENDIENTE = "pendiente"
    ESTADO_PROGRAMADO = "programado"
    ESTADO_RESULTADO_INFORMADO = "resultado_informado"
    ESTADO_RESULTADO_EN_REVISION = "resultado_en_revision"
    ESTADO_FINALIZADO = "finalizado"
    ESTADO_CHOICES = [
        (ESTADO_PENDIENTE, "Pendiente (sin fecha)"),
        (ESTADO_PROGRAMADO, "Programado"),
        (ESTADO_RESULTADO_INFORMADO, "Resultado informado"),
        (ESTADO_RESULTADO_EN_REVISION, "Resultado en revisión"),
        (ESTADO_FINALIZADO, "Finalizado"),
    ]

    FIN_NORMAL = "normal"
    FIN_WO = "wo"
    FIN_ABANDONO = "abandono"
    FINALIZACION_CHOICES = [
        (FIN_NORMAL, "Normal"),
        (FIN_WO, "W.O."),
        (FIN_ABANDONO, "Abandono"),
    ]

    categoria = models.ForeignKey(Categoria, on_delete=models.CASCADE, related_name="partidos")
    tipo = models.CharField(max_length=14, choices=TIPO_CHOICES)
    zona = models.ForeignKey(Zona, null=True, blank=True, on_delete=models.CASCADE, related_name="partidos")
    llave = models.ForeignKey(Llave, null=True, blank=True, on_delete=models.CASCADE, related_name="partidos")
    ronda = models.CharField(max_length=10, choices=RONDA_CHOICES, blank=True, default="")
    numero = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Posición dentro de la ronda (para dibujar la llave)."
    )

    # RESTRICT: no se puede borrar una inscripción que ya tiene partidos,
    # salvo que se borre todo junto (torneo/categoría completos).
    pareja_a = models.ForeignKey(
        Inscripcion, null=True, blank=True, on_delete=models.RESTRICT, related_name="partidos_como_a",
        help_text="Nulo en la llave mientras todavía no se conoce quién llega.",
    )
    pareja_b = models.ForeignKey(
        Inscripcion, null=True, blank=True, on_delete=models.RESTRICT, related_name="partidos_como_b"
    )

    fecha_hora = models.DateTimeField(null=True, blank=True)
    fecha_informada_por = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    fecha_informada_en = models.DateTimeField(null=True, blank=True)

    estado = models.CharField(max_length=22, choices=ESTADO_CHOICES, default=ESTADO_PENDIENTE)
    tipo_finalizacion = models.CharField(
        max_length=10, choices=FINALIZACION_CHOICES, blank=True, default="",
        help_text="Se completa al finalizar. El tratamiento de W.O./abandono en puntos y "
        "estadísticas está pendiente de definición.",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["categoria_id", "tipo", "zona_id", "ronda", "numero", "id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(pareja_a=F("pareja_b")), name="partido_parejas_distintas"
            ),
            models.CheckConstraint(
                condition=(
                    Q(tipo="grupo", zona__isnull=False, llave__isnull=True, ronda="")
                    | (
                        Q(tipo="eliminatoria", zona__isnull=True, llave__isnull=False)
                        & ~Q(ronda="")
                    )
                ),
                name="partido_tipo_coherente_con_zona_llave_ronda",
            ),
            models.CheckConstraint(
                condition=~Q(estado="programado") | Q(fecha_hora__isnull=False),
                name="partido_programado_requiere_fecha",
            ),
            models.CheckConstraint(
                condition=~Q(estado="pendiente") | Q(fecha_hora__isnull=True),
                name="partido_pendiente_no_tiene_fecha",
            ),
            models.CheckConstraint(
                condition=~Q(estado="finalizado") | ~Q(tipo_finalizacion=""),
                name="partido_finalizado_requiere_tipo_finalizacion",
            ),
        ]

    def __str__(self):
        return f"{self.pareja_a or '¿?'} vs {self.pareja_b or '¿?'}"

    # ---- participantes -------------------------------------------------
    def usuarios_de(self, lado):
        """IDs de cuenta de los integrantes de la pareja 'a' o 'b' (los que
        tengan cuenta: el compañero puede no tenerla)."""
        pareja = self.pareja_a if lado == "a" else self.pareja_b
        if pareja is None:
            return set()
        return {uid for uid in (pareja.usuario_id, pareja.usuario_2_id) if uid}

    def lado_de(self, usuario):
        """'a', 'b' o None según a qué pareja pertenece la cuenta en este partido."""
        if usuario is None or not getattr(usuario, "pk", None):
            return None
        if usuario.pk in self.usuarios_de("a"):
            return "a"
        if usuario.pk in self.usuarios_de("b"):
            return "b"
        return None

    def es_participante(self, usuario):
        return self.lado_de(usuario) is not None

    # ---- resultado oficial --------------------------------------------
    @property
    def resultado_oficial(self):
        """El Resultado solo si ya es oficial; si no, None. Es lo único que
        puede usarse para puntos y posiciones."""
        resultado = getattr(self, "resultado", None)
        return resultado if resultado is not None and resultado.es_oficial else None

    def clean(self):
        errores = {}
        if self.zona_id and self.zona.categoria_id != self.categoria_id:
            errores["zona"] = "La zona pertenece a otra categoría."
        if self.llave_id and self.llave.categoria_id != self.categoria_id:
            errores["llave"] = "La llave pertenece a otra categoría."
        for campo in ("pareja_a", "pareja_b"):
            pareja = getattr(self, campo)
            if pareja is not None and pareja.categoria_id != self.categoria_id:
                errores[campo] = "La pareja pertenece a otra categoría."
        if self.pareja_a_id and self.pareja_a_id == self.pareja_b_id:
            errores["pareja_b"] = "Una pareja no puede jugar contra sí misma."
        if self.tipo == self.TIPO_GRUPO and self.zona_id and self.pareja_a_id and self.pareja_b_id:
            repetido = (
                Partido.objects.filter(zona_id=self.zona_id)
                .filter(
                    Q(pareja_a_id=self.pareja_a_id, pareja_b_id=self.pareja_b_id)
                    | Q(pareja_a_id=self.pareja_b_id, pareja_b_id=self.pareja_a_id)
                )
                .exclude(pk=self.pk)
                .exists()
            )
            if repetido:
                errores["pareja_b"] = "Ese cruce ya existe en la zona."
        if (
            self.estado == self.ESTADO_FINALIZADO
            and self.tipo_finalizacion == self.FIN_NORMAL
            and self.pk
            and self.resultado_oficial is None
        ):
            errores["estado"] = "Un partido finalizado normalmente necesita un resultado oficial."
        if errores:
            raise ValidationError(errores)


# ---------------------------------------------------------------------------
# Resultados
# ---------------------------------------------------------------------------
class ResultadoQuerySet(models.QuerySet):
    def oficiales(self):
        """Los únicos resultados que impactan en puntos, estadísticas y posiciones."""
        return self.filter(estado=Resultado.ESTADO_OFICIAL)


class Resultado(models.Model):
    """
    Resultado de un partido y su ciclo de vida:

        INFORMADO ──(rival confirma)────────────► OFICIAL
            │
            └─(rival reporta problema)► DISPUTADO ──(organizador resuelve)► OFICIAL

        carga directa del ORGANIZADOR ───────────► OFICIAL (sin confirmación)

    Mientras no sea OFICIAL no cuenta para nada (ver `oficiales()`).
    `Partido.estado` se mantiene sincronizado automáticamente.
    """

    ESTADO_INFORMADO = "informado"
    ESTADO_DISPUTADO = "disputado"
    ESTADO_OFICIAL = "oficial"
    ESTADO_CHOICES = [
        (ESTADO_INFORMADO, "Informado (a confirmar por el rival)"),
        (ESTADO_DISPUTADO, "En revisión (disputado)"),
        (ESTADO_OFICIAL, "Oficial"),
    ]

    ORIGEN_JUGADOR = "jugador"
    ORIGEN_ORGANIZADOR = "organizador"
    ORIGEN_CHOICES = [(ORIGEN_JUGADOR, "Jugador"), (ORIGEN_ORGANIZADOR, "Organizador")]

    VIA_CONFIRMACION_RIVAL = "confirmacion_rival"
    VIA_CARGA_ORGANIZADOR = "carga_organizador"
    VIA_RESOLUCION_ORGANIZADOR = "resolucion_organizador"
    VIA_CHOICES = [
        (VIA_CONFIRMACION_RIVAL, "Confirmado por el rival"),
        (VIA_CARGA_ORGANIZADOR, "Cargado por el organizador"),
        (VIA_RESOLUCION_ORGANIZADOR, "Resuelto por el organizador"),
    ]

    partido = models.OneToOneField(Partido, on_delete=models.CASCADE, related_name="resultado")
    sets = models.JSONField(
        validators=[validar_sets],
        help_text='Ej.: [{"a":6,"b":3},{"a":4,"b":6},{"a":10,"b":7,"tipo":"super_tiebreak"}]',
    )
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default=ESTADO_INFORMADO)
    origen = models.CharField(max_length=12, choices=ORIGEN_CHOICES)

    informado_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    informado_en = models.DateTimeField(default=timezone.now)

    # Disputa del rival
    disputado_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    disputado_en = models.DateTimeField(null=True, blank=True)
    motivo_disputa = models.TextField(blank=True)

    # Resolución por el organizador (de una disputa)
    resuelto_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    resuelto_en = models.DateTimeField(null=True, blank=True)
    nota_resolucion = models.TextField(blank=True)

    # Oficialización (quién y cómo lo convirtió en oficial)
    oficializado_en = models.DateTimeField(null=True, blank=True)
    oficializado_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    oficializado_via = models.CharField(max_length=24, choices=VIA_CHOICES, blank=True, default="")

    objects = ResultadoQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    (Q(estado="oficial", oficializado_en__isnull=False) & ~Q(oficializado_via=""))
                    | (~Q(estado="oficial") & Q(oficializado_en__isnull=True, oficializado_via=""))
                ),
                name="resultado_oficial_solo_si_oficializado",
            ),
            models.CheckConstraint(
                condition=~Q(estado="disputado") | Q(disputado_en__isnull=False),
                name="resultado_disputado_requiere_fecha_disputa",
            ),
            models.CheckConstraint(
                condition=~Q(origen="organizador") | Q(estado="oficial"),
                name="resultado_del_organizador_es_oficial",
            ),
        ]

    def __str__(self):
        return f"{self.partido} — {self.get_estado_display()}"

    @property
    def es_oficial(self):
        return self.estado == self.ESTADO_OFICIAL

    @property
    def resumen(self):
        return resumen_sets(self.sets)

    @property
    def ganador(self):
        """'a' o 'b'."""
        return self.resumen["ganador"]

    _ESTADO_PARTIDO = {
        ESTADO_INFORMADO: Partido.ESTADO_RESULTADO_INFORMADO,
        ESTADO_DISPUTADO: Partido.ESTADO_RESULTADO_EN_REVISION,
        ESTADO_OFICIAL: Partido.ESTADO_FINALIZADO,
    }

    def clean(self):
        resumen_sets(self.sets)  # valida el detalle completo, no solo campo a campo

    @transaction.atomic
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        partido = self.partido
        partido.estado = self._ESTADO_PARTIDO[self.estado]
        if self.es_oficial and not partido.tipo_finalizacion:
            partido.tipo_finalizacion = Partido.FIN_NORMAL
        partido.save(update_fields=["estado", "tipo_finalizacion"])


@receiver(post_delete, sender=Resultado)
def _volver_estado_partido(sender, instance, **kwargs):
    """Si se borra el resultado, el partido vuelve a programado/pendiente."""
    partido = Partido.objects.filter(pk=instance.partido_id).first()
    if partido is None:
        return
    partido.estado = Partido.ESTADO_PROGRAMADO if partido.fecha_hora else Partido.ESTADO_PENDIENTE
    partido.tipo_finalizacion = ""
    partido.save(update_fields=["estado", "tipo_finalizacion"])


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------
class EventoAuditoria(models.Model):
    """Registro de quién hizo qué y cuándo en una competencia. No existía
    infraestructura de auditoría en el proyecto, así que es deliberadamente
    simple: un log append-only."""

    ACCIONES = [
        ("propuestas_generadas", "Propuestas de zonas generadas"),
        ("propuesta_seleccionada", "Propuesta de zonas seleccionada"),
        ("grupos_publicados", "Fase de grupos publicada"),
        ("partido_modificado", "Partido modificado"),
        ("fecha_informada", "Fecha de partido informada"),
        ("resultado_informado", "Resultado informado"),
        ("resultado_confirmado", "Resultado confirmado por el rival"),
        ("resultado_disputado", "Resultado disputado"),
        ("resultado_resuelto", "Disputa resuelta por el organizador"),
        ("resultado_corregido", "Resultado oficial corregido"),
        ("llave_generada", "Llave generada"),
        ("llave_publicada", "Llave publicada"),
        ("competencia_finalizada", "Competencia finalizada"),
    ]

    categoria = models.ForeignKey(Categoria, on_delete=models.CASCADE, related_name="eventos_auditoria")
    partido = models.ForeignKey(Partido, null=True, blank=True, on_delete=models.CASCADE, related_name="eventos_auditoria")
    accion = models.CharField(max_length=30, choices=ACCIONES)
    usuario = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Nulo si lo hizo el sistema.",
    )
    detalle = models.JSONField(
        default=dict, blank=True,
        help_text="Datos útiles del cambio (p. ej. el resultado anterior al corregir).",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en", "-id"]

    def __str__(self):
        return f"{self.get_accion_display()} — {self.categoria} ({self.creado_en:%d/%m/%Y %H:%M})"
