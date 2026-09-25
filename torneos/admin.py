from django.contrib import admin

from .models import Categoria, Inscripcion, Torneo


class CategoriaInline(admin.TabularInline):
    model = Categoria
    extra = 0


@admin.register(Torneo)
class TorneoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "organizador", "ciudad", "fecha_inicio", "estado")
    list_filter = ("estado", "formato")
    search_fields = ("nombre", "ciudad", "codigo")
    inlines = [CategoriaInline]


@admin.register(Inscripcion)
class InscripcionAdmin(admin.ModelAdmin):
    list_display = ("nombre_1", "nombre_2", "torneo", "categoria", "estado", "metodo_pago", "creado")
    list_filter = ("estado", "metodo_pago", "torneo")
    search_fields = ("nombre_1", "nombre_2", "dni_1", "dni_2", "telefono")
