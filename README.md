rdmo-plugins-github
===========

This repo implements three plugins for [RDMO](https://github.com/rdmorganiser/rdmo):

* an [issue provider](https://rdmo.readthedocs.io/en/latest/plugins/index.html#issue-providers), which lets users push their tasks from RDMO to GitHub issues.
* a [project import plugins](https://rdmo.readthedocs.io/en/latest/plugins/index.html#project-import-plugins), which can be used to import projects from (public or private)repos.
* an export plugin, which can be used to export projects to (public or private) repos. For SMP projects, this plugin also provides other export choices that reuse project data (e.g. README, CITATION or LICENSE files).

The plugin uses [OAUTH 2.0](https://oauth.net/2/), so that users use their respective accounts in both systems.


Setup
-----

Install the plugin in your RDMO virtual environment using pip (directly from GitHub):

```bash
pip install git+https://github.com/rdmorganiser/rdmo-plugins-github
```

An *App* has to be registered with GitHub. Go to https://github.com/settings/developers and create an application with your RDMO URL as callback URL.

The `client_id` and the `client_secret` need to be configured in `config/settings/local.py`:

```python
GITHUB_PROVIDER = {
    'client_id': '',
    'client_secret': ''
}
```

For the issue provider, add the plugin to `PROJECT_ISSUE_PROVIDERS` in `config/settings/local.py`:

```python
PROJECT_ISSUE_PROVIDERS += [
    ('github', _('GitHub Provider'), 'rdmo_github.providers.GitHubProvider'),
]
```

For the import, add the plugin to `PROJECT_IMPORTS` and `PROJECT_IMPORTS_LIST` in `config/settings/local.py`:

```python
PROJECT_IMPORTS = [
    ('github', _('Import from GitHub'), 'rdmo_github.providers.GitHubImport'),
]

PROJECT_IMPORTS_LIST += ['github']
```

For the export:

1. Add the plugin to `PROJECT_EXPORTS` in `config/settings/local.py`:

```python
PROJECT_EXPORTS += [
    ('github', _('Github'), 'rdmo_github.providers.GitHubExportProvider'),
]
```

2. Install the helper plugin "MAUS" in your RDMO virtual environment using pip (directly from GitHub). MAUS provides the SMP specific export choices:

```bash
not working yet!!!!!!!!
pip install git+https://github.com/MPDL/rdmo-plugins-maus
```


Usage
-----

### Issue provider

Users can add a GitHub intergration to their projects. They need to provide the URL to their repository.  Afterwards, issues can be pushed to the GitHub repo.

Additionally, a secret can be added to enable GitHub to communicate to RDMO when an issue has been closed. For this, a webhook has to be added at `<https://github.com/<user>/<repo>/settings/hooks`. The webhook has to point to `https://<rdmo_url>/projects/<project_id>/integrations/<integration_id>/webhook/`, the content type is `application/json` and the secret has to be exactly the secret entered in the integration.

### Project import

Users can import project files directly from a public or private GitHub repository.

### Project export

Users can export project import files directly to a public or private GitLab repository. For SMP projects, they can also export custom files (README, CITATION, LICENSE) created with the SMP project's data. They can choose to export to an existing repository or to create a new one.