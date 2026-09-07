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
        for key in ['client_id', 'client_secret']:
            if not provider.get(key):
                errors.append(Error(f'Key "{key}" is missing from settings.GITHUB_PROVIDER'))

    return errors


@register()
def check_settings_installed_apps_includes_rdmo_maus(app_configs, **kwargs):
    errors = []

    installed_apps = settings.INSTALLED_APPS

    if 'rdmo_maus' not in installed_apps:
        errors.append(
            Error('"rdmo_maus" must be included in settings.INSTALLED_APPS for rdmo_github to properly work.')
        )

    return errors
