from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_backfill_identidad_y_membresias"),
    ]

    operations = [
        # Recién ahora, con el DNI normalizado ya calculado y los duplicados marcados.
        migrations.AddConstraint(
            model_name="identidad",
            constraint=models.UniqueConstraint(
                condition=models.Q(models.Q(("dni_normalizado", ""), _negated=True), ("dni_en_revision", False)),
                fields=("dni_normalizado",),
                name="identidad_dni_normalizado_unico",
            ),
        ),
    ]
