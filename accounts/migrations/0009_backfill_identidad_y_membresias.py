import re

from django.db import migrations

AREAS = ("rankings", "torneos", "canchas", "miembros")


def _normalizar(dni):
    return re.sub(r"\D", "", dni or "")


def completar_datos(apps, schema_editor):
    """
    SOLO agrega información derivada; no modifica ni borra ningún dato existente:
    1) Calcula el DNI normalizado de cada identidad.
    2) Marca como "en revisión" TODAS las identidades de un grupo con el mismo DNI
       normalizado (no las fusiona, no elige una, no borra ninguna).
    3) Da a cada organizador existente una membresía para su cuenta fundadora, con
       los mismos poderes que ya tenía (permiso explícito de administrar en cada área).
    """
    Identidad = apps.get_model("accounts", "Identidad")
    Organizador = apps.get_model("accounts", "Organizador")
    Membresia = apps.get_model("accounts", "MembresiaOrganizador")
    Permiso = apps.get_model("accounts", "PermisoMembresia")

    grupos = {}
    for identidad in Identidad.objects.all().order_by("pk"):
        normalizado = _normalizar(identidad.dni)
        if normalizado:
            grupos.setdefault(normalizado, []).append(identidad.pk)
    # Primero se marcan los grupos duplicados (nada se fusiona ni se elige) y recién después se
    # escribe el DNI normalizado: así el orden es válido aunque la restricción de unicidad ya exista.
    for normalizado, pks in grupos.items():
        if len(pks) > 1:
            Identidad.objects.filter(pk__in=pks).update(dni_en_revision=True)
    for identidad in Identidad.objects.all().order_by("pk"):
        Identidad.objects.filter(pk=identidad.pk).update(dni_normalizado=_normalizar(identidad.dni))

    for organizador in Organizador.objects.all():
        membresia, _ = Membresia.objects.get_or_create(
            organizador=organizador, usuario_id=organizador.usuario_id, defaults={"activa": True}
        )
        for area in AREAS:
            Permiso.objects.get_or_create(membresia=membresia, area=area, defaults={"nivel": "administrar"})


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_identidad_persona_reclamos_membresias"),
    ]

    operations = [
        # Reversible sin pérdida: lo que agrega es derivado y se recalcula; al revertir
        # el esquema (0008) las columnas y tablas nuevas desaparecen.
        migrations.RunPython(completar_datos, migrations.RunPython.noop),
    ]
