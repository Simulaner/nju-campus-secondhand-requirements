import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Category',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=40, unique=True)),
                ('enabled', models.BooleanField(default=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('object_type', models.CharField(max_length=40)),
                ('object_id', models.UUIDField()),
                ('action', models.CharField(max_length=48)),
                ('result', models.CharField(max_length=32)),
                ('reason_code', models.CharField(blank=True, max_length=48)),
                ('occurred_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('correlation_id', models.UUIDField(default=uuid.uuid4)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='Product',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('title', models.CharField(blank=True, max_length=120)),
                ('description', models.TextField(blank=True)),
                ('price_fen', models.PositiveIntegerField(blank=True, null=True)),
                ('condition', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('meeting_place', models.CharField(blank=True, max_length=120)),
                ('status', models.CharField(choices=[('draft', '草稿'), ('pending', '待审核'), ('rejected', '已驳回'), ('published', '已发布'), ('reserved', '已预约'), ('sold', '已售出'), ('offline', '已下架')], default='draft', max_length=12)),
                ('version', models.PositiveIntegerField(default=1)),
                ('relist_blocked', models.BooleanField(default=False)),
                ('off_reason', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='market.category')),
                ('seller', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='ProductImage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('storage_key', models.CharField(max_length=255)),
                ('position', models.PositiveSmallIntegerField()),
                ('mime_type', models.CharField(max_length=40)),
                ('size_bytes', models.PositiveIntegerField()),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='images', to='market.product')),
            ],
        ),
        migrations.CreateModel(
            name='RequestReceipt',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('request_key', models.CharField(max_length=80)),
                ('request_hash', models.CharField(max_length=64)),
                ('object_id', models.UUIDField()),
                ('result_code', models.CharField(max_length=48)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='Reservation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('pending', '待卖家确认'), ('accepted', '已确认待交接'), ('rejected', '已拒绝'), ('cancelled', '已取消'), ('expired', '已超时'), ('completed', '已完成')], default='pending', max_length=12)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('expires_at', models.DateTimeField()),
                ('buyer_confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('seller_confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('product_snapshot', models.JSONField(default=dict)),
                ('cancel_reason', models.TextField(blank=True)),
                ('version', models.PositiveIntegerField(default=1)),
                ('buyer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='purchases', to=settings.AUTH_USER_MODEL)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reservations', to='market.product')),
                ('seller', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sales', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='HandoverContact',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(max_length=24)),
                ('value', models.CharField(max_length=160)),
                ('consent_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ('reservation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contacts', to='market.reservation')),
            ],
        ),
        migrations.CreateModel(
            name='ReviewRecord',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('product_version', models.PositiveIntegerField()),
                ('decision', models.CharField(choices=[('approved', '通过'), ('rejected', '驳回')], max_length=8)),
                ('reason', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reviews', to='market.product')),
                ('reviewer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='TradeRecord',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('completed_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('agreed_price_fen', models.PositiveIntegerField()),
                ('reservation', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='trade', to='market.reservation')),
            ],
        ),
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('event_key', models.CharField(max_length=160)),
                ('object_type', models.CharField(max_length=40)),
                ('object_id', models.UUIDField()),
                ('message', models.TextField()),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('read_at', models.DateTimeField(blank=True, null=True)),
                ('recipient', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('recipient', 'event_key'), name='one_notice_per_event_recipient')],
            },
        ),
        migrations.AddIndex(
            model_name='product',
            index=models.Index(fields=['status', 'published_at'], name='market_prod_status_985fa0_idx'),
        ),
        migrations.AddConstraint(
            model_name='product',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['draft', 'pending', 'rejected', 'published', 'reserved', 'sold', 'offline'])), name='product_status_valid'),
        ),
        migrations.AddConstraint(
            model_name='product',
            constraint=models.CheckConstraint(condition=models.Q(('price_fen__gt', 0), ('price_fen', None), _connector='OR'), name='product_price_positive'),
        ),
        migrations.AddConstraint(
            model_name='product',
            constraint=models.CheckConstraint(condition=models.Q(('condition__range', (1, 9)), ('condition', None), _connector='OR'), name='product_condition_valid'),
        ),
        migrations.AddConstraint(
            model_name='productimage',
            constraint=models.UniqueConstraint(fields=('product', 'position'), name='product_image_position_unique'),
        ),
        migrations.AddConstraint(
            model_name='productimage',
            constraint=models.CheckConstraint(condition=models.Q(('position__range', (1, 9))), name='product_image_slot_valid'),
        ),
        migrations.AddConstraint(
            model_name='requestreceipt',
            constraint=models.UniqueConstraint(fields=('actor', 'request_key'), name='request_key_per_actor'),
        ),
        migrations.AddIndex(
            model_name='reservation',
            index=models.Index(fields=['status', 'expires_at'], name='market_rese_status_7a940c_idx'),
        ),
        migrations.AddConstraint(
            model_name='reservation',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['pending', 'accepted', 'rejected', 'cancelled', 'expired', 'completed'])), name='reservation_status_valid'),
        ),
        migrations.AddConstraint(
            model_name='reservation',
            constraint=models.CheckConstraint(condition=models.Q(('buyer', models.F('seller')), _negated=True), name='reservation_distinct_parties'),
        ),
        migrations.AddConstraint(
            model_name='reservation',
            constraint=models.UniqueConstraint(condition=models.Q(('status__in', ['pending', 'accepted'])), fields=('product',), name='one_active_reservation_per_product'),
        ),
        migrations.AddConstraint(
            model_name='handovercontact',
            constraint=models.UniqueConstraint(fields=('reservation', 'owner'), name='one_contact_per_party'),
        ),
        migrations.AddConstraint(
            model_name='reviewrecord',
            constraint=models.UniqueConstraint(fields=('product', 'product_version'), name='review_per_version'),
        ),
        migrations.AddConstraint(
            model_name='reviewrecord',
            constraint=models.CheckConstraint(condition=models.Q(('decision__in', ['approved', 'rejected'])), name='review_decision_valid'),
        ),
        migrations.AddConstraint(
            model_name='reviewrecord',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('decision', 'rejected'), _negated=True), models.Q(('reason', ''), _negated=True), _connector='OR'), name='review_rejection_reason'),
        ),
        migrations.AddConstraint(
            model_name='traderecord',
            constraint=models.CheckConstraint(condition=models.Q(('agreed_price_fen__gt', 0)), name='trade_price_positive'),
        ),
    ]
