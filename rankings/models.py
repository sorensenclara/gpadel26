"""
Rankings múltiples e independientes.

Un Ranking puede existir sin ningún torneo. Se compone de:

    Ranking ──< TemporadaRanking ──< VersionRanking ──< PuntajeRanking >── Identidad

- El ranking general GPADEL (calculado más adelante) y los rankings externos que carga una
  liga, asociación u organizador (a mano o importados más adelante) comparten esta estructura.
- Los puntajes son INDIVIDUALES y cuelgan de la identidad deportiva de la persona, tenga o
  no cuenta: se conservan aunque cambie de pareja.
- Una VERSIÓN publicada es inmutable: corregir un ranking = publicar una versión nueva. Así un
  torneo puede referirse a una versión exacta y sus zonas no cambian retroactivamente.
- Las categorías de un ranking son etiquetas propias del ranking: NO tienen relación con la
  categoría oficial de GPADEL (`Jugador.categoria_oficial`), y nada de lo que se cargue acá
  puede modificarla.
- "Sin ranking" (SR) = la persona no figura en la versión elegida. Nunca se la trata como 0.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Max, Q
from django.utils import timezone

from accounts.models import Identidad, Organizador, normalizar_dni

User = settings.AUTH_USER_MODEL


class Ranking(models.Model):
    TIPO_GENERAL = "general_gpadel"
    TIPO_EXTERNO = "externo"
    TIPO_CHOICES = [(TIPO_GENERAL, "Ranking general GPADEL"), (TIPO_EXTERNO, "Ranking externo")]

    VISIBILIDAD_PUBLICO = "publico"
    VISIBILIDAD_PRIVADO = "privado"
    VISIBILIDAD_CHOICES = [
        (VISIBILIDAD_PUBLICO, "Público: otros organizadores pueden consultarlo y usarlo en sus torneos"),
        (VISIBILIDAD_PRIVADO, "Privado: solo la entidad y sus usuarios autorizados"),
    ]

    nombre = models.CharField(max_length=150, help_text='Ej.: "Ranking Necochea".')
    tipo = models.CharField(max_length=15, choices=TIPO_CHOICES, default=TIPO_EXTERNO)
    entidad = models.ForeignKey(
        Organizador, null=True, blank=True, on_delete=models.PROTECT, related_name="rankings",
        help_text="Entidad responsable. Vacía solo en el ranking general de GPADEL.",
    )
    ambito = models.CharField(max_length=150, blank=True, help_text="Alcance geográfico opcional (texto libre).")
    descripcion = models.TextField(blank=True)
    visibilidad = models.CharField(max_length=10, choices=VISIBILIDAD_CHOICES, default=VISIBILIDAD_PRIVADO)
    por_categoria = models.BooleanField(
        default=False,
        help_text="Sí: listas independientes por categoría. No: una sola lista general. No se puede "
        "cambiar una vez publicada la primera versión.",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    creado_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["entidad", "nombre"], condition=Q(entidad__isnull=False), name="ranking_nombre_unico_por_entidad"
            ),
            # Un solo ranking general en todo el sistema.
            models.UniqueConstraint(fields=["tipo"], condition=Q(tipo="general_gpadel"), name="un_solo_ranking_general"),
            models.CheckConstraint(
                condition=(
                    Q(tipo="general_gpadel", entidad__isnull=True, visibilidad="publico")
                    | Q(tipo="externo", entidad__isnull=False)
                ),
                name="ranking_tipo_coherente_con_entidad_y_visibilidad",
            ),
        ]

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        if self.pk:
            anterior = Ranking.objects.filter(pk=self.pk).values_list("por_categoria", flat=True).first()
            if (
                anterior is not None
                and anterior != self.por_categoria
                and VersionRanking.objects.filter(
                    temporada__ranking=self, estado=VersionRanking.ESTADO_PUBLICADA
                ).exists()
            ):
                raise ValidationError("No se puede cambiar 'por categoría' una vez publicada una versión.")
        super().save(*args, **kwargs)


class TemporadaRanking(models.Model):
    ESTADO_ABIERTA = "abierta"
    ESTADO_CERRADA = "cerrada"
    ESTADO_CHOICES = [(ESTADO_ABIERTA, "Abierta"), (ESTADO_CERRADA, "Cerrada")]

    ranking = models.ForeignKey(Ranking, on_delete=models.CASCADE, related_name="temporadas")
    nombre = models.CharField(max_length=60, help_text='Ej.: "2026".')
    fecha_desde = models.DateField(null=True, blank=True)
    fecha_hasta = models.DateField(null=True, blank=True)
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default=ESTADO_ABIERTA)

    class Meta:
        ordering = ["ranking_id", "-fecha_desde", "nombre"]
        constraints = [
            models.UniqueConstraint(fields=["ranking", "nombre"], name="temporada_nombre_unico_por_ranking"),
            models.CheckConstraint(
                condition=Q(fecha_desde__isnull=True) | Q(fecha_hasta__isnull=True) | Q(fecha_hasta__gte=models.F("fecha_desde")),
                name="temporada_fechas_coherentes",
            ),
        ]

    def __str__(self):
        return f"{self.ranking} {self.nombre}"


class VersionRanking(models.Model):
    """Una "foto" publicable de la temporada. Publicada = inmutable."""

    ESTADO_BORRADOR = "borrador"
    ESTADO_PUBLICADA = "publicada"
    ESTADO_CHOICES = [(ESTADO_BORRADOR, "Borrador"), (ESTADO_PUBLICADA, "Publicada")]

    temporada = models.ForeignKey(TemporadaRanking, on_delete=models.CASCADE, related_name="versiones")
    numero = models.PositiveIntegerField(editable=False)
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default=ESTADO_BORRADOR)
    nota = models.CharField(max_length=255, blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    creada_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    publicada_en = models.DateTimeField(null=True, blank=True)
    publicada_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["temporada_id", "-numero"]
        constraints = [
            models.UniqueConstraint(fields=["temporada", "numero"], name="version_numero_unico_por_temporada"),
            # A lo sumo un borrador abierto por temporada.
            models.UniqueConstraint(
                fields=["temporada"], condition=Q(estado="borrador"), name="un_borrador_por_temporada"
            ),
            models.CheckConstraint(
                condition=Q(estado="borrador", publicada_en__isnull=True) | Q(estado="publicada", publicada_en__isnull=False),
                name="version_publicada_tiene_fecha",
            ),
        ]

    def __str__(self):
        return f"{self.temporada} v{self.numero} ({self.get_estado_display()})"

    @property
    def publicada(self):
        return self.estado == self.ESTADO_PUBLICADA

    def save(self, *args, **kwargs):
        if self.pk:
            previa = VersionRanking.objects.filter(pk=self.pk).first()
            if previa is not None and previa.publicada:
                raise ValidationError("Una versión publicada es inmutable: publicá una versión nueva.")
        elif not self.numero:
            maximo = VersionRanking.objects.filter(temporada=self.temporada).aggregate(m=Max("numero"))["m"]
            self.numero = (maximo or 0) + 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.publicada:
            raise ValidationError("Una versión publicada no se puede borrar.")
        return super().delete(*args, **kwargs)


class PuntajeRankingQuerySet(models.QuerySet):
    """Evita que las operaciones masivas salteen la inmutabilidad de las versiones publicadas."""

    def _bloquear_publicadas(self):
        if self.filter(version__estado=VersionRanking.ESTADO_PUBLICADA).exists():
            raise ValidationError("Los puntajes de una versión publicada son inmutables.")

    def update(self, **kwargs):
        self._bloquear_publicadas()
        return super().update(**kwargs)

    def delete(self):
        self._bloquear_publicadas()
        return super().delete()

    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        if any(o.version.publicada for o in objs):
            raise ValidationError("No se pueden agregar puntajes a una versión publicada.")
        return super().bulk_create(objs, *args, **kwargs)


class PuntajeRanking(models.Model):
    """Puntos y posición INDIVIDUALES de una persona en una versión de un ranking."""

    ORIGEN_MANUAL = "manual"
    ORIGEN_IMPORTADO = "importado"
    ORIGEN_CALCULADO = "calculado"
    ORIGEN_CHOICES = [(ORIGEN_MANUAL, "Manual"), (ORIGEN_IMPORTADO, "Importado"), (ORIGEN_CALCULADO, "Calculado")]

    VINCULO_VINCULADO = "vinculado"
    VINCULO_PENDIENTE = "pendiente"
    VINCULO_EN_REVISION = "en_revision"
    VINCULO_CHOICES = [
        (VINCULO_VINCULADO, "Vinculado a una identidad"),
        (VINCULO_PENDIENTE, "Pendiente de vincular"),
        (VINCULO_EN_REVISION, "En revisión (ambiguo o dudoso)"),
    ]

    version = models.ForeignKey(VersionRanking, on_delete=models.CASCADE, related_name="puntajes")
    persona = models.ForeignKey(Identidad, null=True, blank=True, on_delete=models.PROTECT, related_name="puntajes_ranking")
    categoria = models.CharField(
        max_length=60, blank=True,
        help_text="Etiqueta de la lista, propia de este ranking. Vacía si el ranking no es por categoría. "
        "Independiente de la categoría oficial de GPADEL.",
    )
    puntos = models.DecimalField(max_digits=10, decimal_places=2)
    posicion = models.PositiveIntegerField(null=True, blank=True)
    origen = models.CharField(max_length=10, choices=ORIGEN_CHOICES, default=ORIGEN_MANUAL)
    dni_original = models.CharField(max_length=20, blank=True, help_text="DNI tal como vino (importaciones).")
    dni_normalizado = models.CharField(max_length=20, blank=True, default="", editable=False)
    nombre_original = models.CharField(max_length=150, blank=True)
    estado_vinculo = models.CharField(max_length=12, choices=VINCULO_CHOICES, default=VINCULO_VINCULADO)

    objects = PuntajeRankingQuerySet.as_manager()

    class Meta:
        ordering = ["version_id", "categoria", "posicion", "-puntos"]
        constraints = [
            models.UniqueConstraint(
                fields=["version", "persona", "categoria"], condition=Q(persona__isnull=False),
                name="puntaje_unico_por_persona_y_lista",
            ),
            # Control de duplicados aunque todavía no esté vinculado a una identidad.
            models.UniqueConstraint(
                fields=["version", "dni_normalizado", "categoria"], condition=~Q(dni_normalizado=""),
                name="puntaje_unico_por_dni_y_lista",
            ),
            models.CheckConstraint(condition=Q(puntos__gte=0), name="puntaje_puntos_no_negativos"),
            models.CheckConstraint(condition=Q(posicion__isnull=True) | Q(posicion__gte=1), name="puntaje_posicion_valida"),
            models.CheckConstraint(
                condition=~Q(estado_vinculo="vinculado") | Q(persona__isnull=False),
                name="puntaje_vinculado_requiere_persona",
            ),
        ]

    def __str__(self):
        return f"{self.persona or self.nombre_original}: {self.puntos} ({self.version})"

    def clean(self):
        ranking = self.version.temporada.ranking
        if ranking.por_categoria and not self.categoria:
            raise ValidationError({"categoria": "Este ranking es por categoría: indicá la lista."})
        if not ranking.por_categoria and self.categoria:
            raise ValidationError({"categoria": "Este ranking es una lista general, sin categorías."})

    def save(self, *args, **kwargs):
        if self.version.publicada:
            raise ValidationError("Los puntajes de una versión publicada son inmutables.")
        self.dni_normalizado = normalizar_dni(self.dni_original or (self.persona.dni if self.persona_id else ""))
        self.puntos = Decimal(self.puntos)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.version.publicada:
            raise ValidationError("Los puntajes de una versión publicada son inmutables.")
        return super().delete(*args, **kwargs)


# ---------------------------------------------------------------------------
# Ranking general GPADEL: qué torneos puntúan y bajo qué reglas (sin algoritmo todavía)
# ---------------------------------------------------------------------------
class ReglaPuntaje(models.Model):
    """Una regla de asignación de puntos. Solo se guarda su configuración: el algoritmo
    definitivo todavía no está definido ni implementado."""

    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(blank=True)
    parametros = models.JSONField(default=dict, blank=True)
    vigente = models.BooleanField(default=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    creada_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["nombre"]
        verbose_name_plural = "reglas de puntaje"

    def __str__(self):
        return self.nombre


class TorneoPuntuable(models.Model):
    """Decide si un torneo aporta puntos al ranking general. Por defecto NO puntúa."""

    torneo = models.OneToOneField("torneos.Torneo", on_delete=models.CASCADE, related_name="puntuabilidad")
    temporada = models.ForeignKey(
        TemporadaRanking, on_delete=models.PROTECT, related_name="torneos_puntuables",
        help_text="Temporada del ranking general a la que aportaría.",
    )
    regla = models.ForeignKey(ReglaPuntaje, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    puntuable = models.BooleanField(default=False)
    decidido_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decidido_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "torneo puntuable"
        verbose_name_plural = "torneos puntuables"
        constraints = [
            models.CheckConstraint(
                condition=~Q(puntuable=True) | Q(regla__isnull=False), name="torneo_puntuable_requiere_regla"
            ),
        ]

    def clean(self):
        if self.temporada_id and self.temporada.ranking.tipo != Ranking.TIPO_GENERAL:
            raise ValidationError({"temporada": "Solo se puede puntuar para el ranking general GPADEL."})

    def save(self, *args, **kwargs):
        self.clean()
        if self.decidido_por_id and not self.decidido_en:
            self.decidido_en = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.torneo} → {self.temporada} ({'puntúa' if self.puntuable else 'no puntúa'})"
