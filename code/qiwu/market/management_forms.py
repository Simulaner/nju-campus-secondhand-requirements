from django import forms

from .models import Category, ModerationAction, ProductStatus


class ModerationForm(forms.Form):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    action = forms.ChoiceField(widget=forms.HiddenInput)
    reason = forms.CharField(
        label="处理原因", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3})
    )

    def __init__(self, *args, actions, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["action"].choices = [(action.value, action.label) for action in actions]

    def clean(self):
        values = super().clean()
        if values.get("action") in (ModerationAction.VIOLATION, ModerationAction.DISABLE):
            if not values.get("reason") and "reason" not in self.errors:
                self.add_error("reason", "请填写处理原因。")
        return values


class ProductFilterForm(forms.Form):
    q = forms.CharField(label="商品名称", max_length=120, required=False)
    category = forms.ModelChoiceField(
        label="分类",
        queryset=Category.objects.order_by("name"),
        empty_label="全部分类",
        required=False,
    )
    status = forms.ChoiceField(
        label="状态",
        required=False,
        choices=[
            ("", "全部状态"),
            *((s.value, s.label) for s in ProductStatus if s != ProductStatus.DRAFT),
        ],
    )


class AccountFilterForm(forms.Form):
    q = forms.CharField(label="昵称或校园邮箱", required=False, max_length=254)
    status = forms.ChoiceField(
        label="账号状态",
        required=False,
        choices=[
            ("", "全部状态"),
            ("active", "正常"),
            ("disabled", "已停用"),
        ],
    )


OBJECT_LABELS = {"product": "商品", "reservation": "预约", "account": "账号"}
RESULT_LABELS = {"success": "成功", "failure": "失败"}


class AuditFilterForm(forms.Form):
    actor = forms.CharField(label="操作者昵称", max_length=20, required=False)
    object_type = forms.ChoiceField(
        label="业务对象", required=False, choices=[("", "全部对象"), *OBJECT_LABELS.items()]
    )
    result = forms.ChoiceField(
        label="结果", required=False, choices=[("", "全部结果"), *RESULT_LABELS.items()]
    )
