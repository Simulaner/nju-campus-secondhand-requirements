from django.db import migrations


def seed_categories(apps, schema_editor):
    category = apps.get_model("market", "Category")
    for name in ("数码电子", "图书教材", "生活用品", "运动户外"):
        category.objects.using(schema_editor.connection.alias).get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [("market", "0002_product_image_content")]
    operations = [migrations.RunPython(seed_categories, migrations.RunPython.noop)]
