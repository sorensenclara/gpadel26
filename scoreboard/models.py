from django.db import models
from .utils import default_code  # si estás usando default_code


class Match(models.Model):
    code = models.SlugField(
        max_length=20,
        unique=True,
        default=default_code,
        help_text="Código para compartir el marcador (URL)",
    )
    team_a = models.CharField(max_length=50, default="Equipo A")
    team_b = models.CharField(max_length=50, default="Equipo B")

    # Juegos por set
    set1_a = models.IntegerField(default=0)
    set1_b = models.IntegerField(default=0)
    set2_a = models.IntegerField(default=0)
    set2_b = models.IntegerField(default=0)
    set3_a = models.IntegerField(default=0)
    set3_b = models.IntegerField(default=0)

    current_set = models.IntegerField(default=1)

    # Modalidad del partido
    mode = models.CharField(
        max_length=20,
        default="3_sets",
        help_text="3 sets o 2 sets + super tie-break",
    )

    # Logos o auspiciantes (lista JSON)
    sponsors = models.JSONField(default=list, blank=True)

    # ------------------------------------------------------------
    # FUNCIONES AUXILIARES
    # ------------------------------------------------------------
    def check_super_tiebreak(self):
        """Devuelve True si debe jugarse super tie-break."""
        if self.mode != "2_sets_super_tb":
            return False

        sets_a = 0
        sets_b = 0

        # Set 1
        if self.set1_a > self.set1_b:
            sets_a += 1
        elif self.set1_b > self.set1_a:
            sets_b += 1

        # Set 2
        if self.set2_a > self.set2_b:
            sets_a += 1
        elif self.set2_b > self.set2_a:
            sets_b += 1

        # Si cada uno ganó un set → super tie-break
        return sets_a == 1 and sets_b == 1

    # ------------------------------------------------------------
    # NUEVO: as_payload() para API y WebSocket
    # ------------------------------------------------------------
    def as_payload(self):
        """Devuelve el estado actual del partido en formato JSON serializable."""
        return {
            "code": self.code,
            "team_a": self.team_a or "",
            "team_b": self.team_b or "",
            "sets": [
                {"a": self.set1_a or 0, "b": self.set1_b or 0},
                {"a": self.set2_a or 0, "b": self.set2_b or 0},
                {"a": self.set3_a or 0, "b": self.set3_b or 0},
            ],
            "current_set": self.current_set or 1,
            "mode": self.mode or "3_sets",
            "sponsors": self.sponsors or [],
        }

    def __str__(self):
        return f"{self.code} - {self.team_a} vs {self.team_b}"
