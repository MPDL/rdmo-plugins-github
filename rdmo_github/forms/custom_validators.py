import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

def validate_text_field(field_name, value, min_length, max_length, not_allowed_pattern, allowed_char_name_str):
    errors = []
    
    matches = re.findall(not_allowed_pattern, value)
    matches = list(set(matches))
    if len(matches) > 0:
        errors.append(ValidationError(
            _(f'''{field_name} contains special character(s): "{'", "'.join(matches)}". Allowed characters are: {allowed_char_name_str}.''')
        ))
    
    if len(value) > max_length:
        errors.append(ValidationError(
            _(f'{field_name} must have at most {max_length} characters (it has {len(value)}).')
        ))

    if len(value) < min_length:
        errors.append(ValidationError(
            _(f'{field_name} must have at least {min_length} characters (it has {len(value)}).')
        ))

    if len(errors) > 0:
        raise ValidationError(errors)

def validate_new_repo_name(value):
    field_name = 'Repository name'
    min_length = 1
    max_length = 50
    not_allowed_pattern = f'[^A-Za-z0-9\-\_\.]'
    allowed_char_name_str = 'alphanumeric, hyphen, underscore, and period'

    return validate_text_field(field_name, value, min_length, max_length, not_allowed_pattern, allowed_char_name_str)

def validate_file_path(value):
    field_name = 'File path'
    min_length = 6
    max_length = 100
    not_allowed_pattern = f'[^A-Za-z0-9\/\-\_\.]'
    allowed_char_name_str = 'alphanumeric, slash, hyphen, underscore, and period'

    return validate_text_field(field_name, value, min_length, max_length, not_allowed_pattern, allowed_char_name_str)