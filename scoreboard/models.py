from django.db import models
from django.utils.crypto import get_random_string

def default_code():
    return get_random_string(8).upper()

class Match(models.Model):
    code = models.SlugField(max_length=20, unique=True, default=default_code, help_text="Código para compartir el marcador (URL)")
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

    # NUEVO: logos (dataURL o URLs)
    sponsors = models.JSONField(default=list, blank=True)

    def as_payload(self):
        return {
            "code": self.code,
            "team_a": self.team_a,
            "team_b": self.team_b,
            "sets": [
                {"a": self.set1_a, "b": self.set1_b},
                {"a": self.set2_a, "b": self.set2_b},
                {"a": self.set3_a, "b": self.set3_b},
            ],
            "current_set": self.current_set,
            "sponsors": self.sponsors,  # importante
        }

    def __str__(self):
        return f"{self.team_a} vs {self.team_b} ({self.code})"
