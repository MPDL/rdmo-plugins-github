import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

def validate_text_field(field_name, value, min_length, max_length, not_allowed_pattern, allowed_char_name_str):
    errors = []
    
    matches = re.findall(not_allowed_pattern, value)
    matches = list(set(matches))
    if len(matches) > 0:
        try:
            index = matches.index(' ')
            matches[index] = str(_('whitespace'))
        except:
            pass

        errors.append(ValidationError(
            _('{field_name} contains special character(s): "{spec_chars}". Allowed characters are: {allowed_char_name_str}.').format(
                field_name=field_name,
                spec_chars='", "'.join(matches),
                allowed_char_name_str=allowed_char_name_str
            ),
            code='invalid'
        ))
    
    if len(value) > max_length:
        errors.append(ValidationError(
            _('{field_name} must have at most {max_length} characters (it has {len_value}).').format(
                field_name=field_name,
                max_length=max_length,
                len_value=len(value)
            ),
            code='invalid'
        ))

    if len(value) < min_length:
        errors.append(ValidationError(
            _('{field_name} must have at least {min_length} characters (it has {len_value}).').format(
                field_name=field_name,
                min_length=min_length,
                len_value=len(value)
            ),
            code='invalid'
        ))

    if len(errors) > 0:
        raise ValidationError(errors)

def validate_new_repo_name(value):
    field_name = _('Repository name')
    min_length = 1
    max_length = 50
    not_allowed_pattern = f'[^A-Za-z0-9\-\_\.]'
    allowed_char_name_str = _('alphanumeric, hyphen, underscore, and period')

    return validate_text_field(field_name, value, min_length, max_length, not_allowed_pattern, allowed_char_name_str)

def validate_export_file_path(value):
    field_name = _('File path')
    min_length = 6
    max_length = 100
    not_allowed_pattern = f'[^A-Za-z0-9\/\-\_\.]'
    allowed_char_name_str = _('alphanumeric, slash, hyphen, underscore, and period')

    return validate_text_field(field_name, value, min_length, max_length, not_allowed_pattern, allowed_char_name_str)

def validate_import_file_path(value):
    if not value.endswith('.xml'):
        raise ValidationError(
            _('File must be in XML format.'),
            code='invalid'
        )