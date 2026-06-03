from django.apps import AppConfig


class RDMOGitHubConfig(AppConfig):
    name = 'rdmo_github'

    def ready(self):
        import rdmo_github.checks  # noqa: F401
