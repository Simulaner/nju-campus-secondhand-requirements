from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO

from django import forms
from django.core.exceptions import ValidationError
from PIL import Image

from .models import PUBLIC_PRODUCT_STATUSES, Category, ProductStatus, ReviewDecision
from .reservations import RESERVATION_ACTIONS
from .services import SORTS

CONDITIONS = [("", "请选择")] + [(n, f"{n} 成新") for n in range(1, 10)]
IMAGE_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


@dataclass
class CheckedImage:
    data: bytes
    mime_type: str


class MultipleFileInput(forms.FileInput):
    allow_multiple_selected = True


class ImagesField(forms.Field):
    widget = MultipleFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"})

    def clean(self, files):
        if len(files or []) > 9:
            raise ValidationError("每件商品最多保留 9 张图片。")
        images = []
        for file in files or []:
            if file.size > 5 * 1024 * 1024:
                raise ValidationError("每张图片最大为 5 MB。")
            data = file.read()
            try:
                with Image.open(BytesIO(data), formats=list(IMAGE_TYPES)) as image:
                    if image.width * image.height > Image.MAX_IMAGE_PIXELS:
                        raise Image.DecompressionBombError("Image exceeds pixel limit")
                    mime_type = IMAGE_TYPES[image.format]
                    image.verify()
                with Image.open(BytesIO(data), formats=[image.format]) as decoded:
                    decoded.load()
            except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
                raise ValidationError("请上传能正常打开的 JPG、PNG 或 WebP 图片。") from exc
            images.append(CheckedImage(data, mime_type))
        return images


class ProductForm(forms.Form):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    title = forms.CharField(label="商品名称", max_length=120, required=False)
    category = forms.ModelChoiceField(
        label="分类",
        queryset=Category.objects.filter(enabled=True).order_by("name"),
        required=False,
    )
    price = forms.DecimalField(
        label="价格（元）",
        min_value=Decimal("0.01"),
        max_digits=10,
        decimal_places=2,
        required=False,
    )
    condition = forms.TypedChoiceField(
        label="新旧程度", choices=CONDITIONS, coerce=int, empty_value=None, required=False
    )
    description = forms.CharField(
        label="商品描述", widget=forms.Textarea(attrs={"rows": 4}), required=False
    )
    meeting_place = forms.CharField(label="校内面交地点（选填）", max_length=120, required=False)
    remove_images = forms.MultipleChoiceField(
        label="移除已有图片", widget=forms.CheckboxSelectMultiple, required=False
    )
    images = ImagesField(
        label="添加图片",
        required=False,
        help_text="可多选 JPG、PNG 或 WebP，单张不超过 5 MB，共 1 至 9 张。",
    )

    def __init__(self, *args, product, action=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.existing = list(product.images.all()) if not product._state.adding else []
        self.fields["remove_images"].choices = [
            (str(img.pk), f"图片 {img.position}") for img in self.existing
        ]
        if action == "submit" or product.status == ProductStatus.PUBLISHED:
            for key in ("title", "category", "price", "condition", "description"):
                self.fields[key].required = True
        self.initial.update(
            title=product.title,
            category=product.category_id,
            price=product.price_yuan,
            condition=product.condition,
            description=product.description,
            meeting_place=product.meeting_place,
            version=0 if product._state.adding else product.version,
        )


class SearchForm(forms.Form):
    q = forms.CharField(
        label="商品名称",
        max_length=120,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "搜索商品名称"}),
    )
    category = forms.ModelChoiceField(
        label="分类",
        queryset=Category.objects.order_by("name"),
        empty_label="全部分类",
        required=False,
    )
    min_price = forms.DecimalField(
        label="最低价（元）", min_value=0, max_digits=10, decimal_places=2, required=False
    )
    max_price = forms.DecimalField(
        label="最高价（元）", min_value=0, max_digits=10, decimal_places=2, required=False
    )
    condition = forms.TypedChoiceField(
        label="新旧程度", choices=[("", "全部成色"), *CONDITIONS[1:]], coerce=int, required=False
    )
    status = forms.ChoiceField(
        label="状态",
        choices=[("", "全部公开状态"), *((s, s.label) for s in PUBLIC_PRODUCT_STATUSES)],
        required=False,
    )
    sort = forms.ChoiceField(
        label="排序", choices=[(key, pair[0]) for key, pair in SORTS.items()], required=False
    )

    def clean(self):
        data = super().clean()
        low, high = data.get("min_price"), data.get("max_price")
        if low is not None and high is not None and low > high:
            self.add_error("max_price", "最高价不能低于最低价。")
        return data


class ReviewForm(forms.Form):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    decision = forms.ChoiceField(choices=ReviewDecision.choices)
    reason = forms.CharField(
        label="审核意见", required=False, widget=forms.Textarea(attrs={"rows": 3})
    )

    def clean(self):
        data = super().clean()
        if data.get("decision") == ReviewDecision.REJECTED and not data.get("reason"):
            self.add_error("reason", "请填写驳回原因，方便卖家修改。")
        return data


class ReserveForm(forms.Form):
    request_key = forms.UUIDField(widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class ReservationActionForm(forms.Form):
    action = forms.ChoiceField(
        choices=[(name, label) for name, (label, _) in RESERVATION_ACTIONS.items()]
    )
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
