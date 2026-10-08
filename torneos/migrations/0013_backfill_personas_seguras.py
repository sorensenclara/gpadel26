from django.db import migrations


def completar(apps, schema_editor):
    """
    SOLO relaciones 100% seguras, derivadas de claves foráneas que ya existen (nunca de
    DNI tipeado); no modifica ni borra ningún dato:
      - persona_1 = la identidad de la cuenta que hizo la inscripción (`usuario`);
      - persona_2 = la identidad de la cuenta vinculada como compañero (`usuario_2`).
    Las vinculaciones que dependen del DNI de texto las propone, en modo simulación, el
    comando `backfill_personas`, y las ambiguas quedan para revisión humana.

    Además abre un caso de revisión por cada grupo de identidades con el mismo DNI
    normalizado (que 0009 dejó marcadas): nada se fusiona ni se elige solo.
    """
    Identidad = apps.get_model("accounts", "Identidad")
    Inscripcion = apps.get_model("torneos", "Inscripcion")
    Revision = apps.get_model("torneos", "RevisionIdentidad")

    de_cuenta = {i.usuario_id: i.pk for i in Identidad.objects.filter(usuario__isnull=False)}
    for insc in Inscripcion.objects.all().only("pk", "usuario_id", "usuario_2_id", "persona_1_id", "persona_2_id"):
        p1 = insc.persona_1_id or de_cuenta.get(insc.usuario_id)
        p2 = insc.persona_2_id or de_cuenta.get(insc.usuario_2_id)
        if p1 and p1 == p2:
            continue  # dato incoherente (la misma persona en ambos lados): no se asocia ninguna
        cambios = {}
        if insc.persona_1_id is None and p1:
            cambios["persona_1_id"] = p1
        if insc.persona_2_id is None and p2:
            cambios["persona_2_id"] = p2
        if cambios:
            Inscripcion.objects.filter(pk=insc.pk).update(**cambios)

    grupos = {}
    for identidad in Identidad.objects.filter(dni_en_revision=True):
        grupos.setdefault(identidad.dni_normalizado, []).append(identidad)
    for dni, identidades in grupos.items():
        revision, _ = Revision.objects.get_or_create(dni=dni, motivo="dni_ambiguo", estado="abierta")
        revision.cuentas.add(*[i.usuario_id for i in identidades if i.usuario_id])


class Migration(migrations.Migration):

    dependencies = [
        ("torneos", "0012_personas_en_inscripciones"),
        ("accounts", "0010_identidad_dni_unico"),
    ]

    operations = [
        migrations.RunPython(completar, migrations.RunPython.noop),
    ]
