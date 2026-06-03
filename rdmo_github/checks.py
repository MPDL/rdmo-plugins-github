from django.conf import settings
from django.core.checks import Error, register


@register()
def check_github_provider_settings(app_configs, **kwargs):
    errors = []

    try:
        provider = settings.GITHUB_PROVIDER
    except AttributeError:
        errors.append(
            Error('settings.GITHUB_PROVIDER does not exist.', hint='Add GITHUB_PROVIDER to config/settings/local.py')
        )
    else:
        for key in ['client_id', 'client_secret', 'app_type', 'github_app_name']:
            if not provider.get(key):
                errors.append(Error(f'Key "{key}" is missing from settings.GITHUB_PROVIDER')
        )

    return errors
