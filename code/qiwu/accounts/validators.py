from django.core.exceptions import ValidationError
from django.core.validators import validate_email

CAMPUS_DOMAINS = {"nju.edu.cn", "smail.nju.edu.cn"}
CAMPUS_EMAIL_HINT = "教工邮箱使用 @nju.edu.cn，学生邮箱使用 @smail.nju.edu.cn。"


def campus_email(value):
    email = (value or "").strip().lower()
    validate_email(email)
    if email.rsplit("@", 1)[1] not in CAMPUS_DOMAINS:
        raise ValidationError("请使用南京大学教工或学生邮箱。" + CAMPUS_EMAIL_HINT)
    return email


class LettersAndDigitsValidator:
    def validate(self, password, user=None):
        if not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
            raise ValidationError(self.get_help_text(), code="letters_and_digits")

    def get_help_text(self):
        return "密码需同时包含字母和数字。"
