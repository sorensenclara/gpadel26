
from django.contrib import admin
from .models import Match

@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ("code", "team_a", "team_b", "current_set", "organizador", "jugador", "personalizado")
    search_fields = ("code", "team_a", "team_b")
    list_filter = ("organizador", "personalizado")
