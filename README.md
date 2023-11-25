rdmo-github
===========

This repo implements two plugins for [RDMO](https://github.com/rdmorganiser/rdmo):

* an [issue provider](https://rdmo.readthedocs.io/en/latest/plugins/index.html#issue-providers), which lets users push their tasks from RDMO to GitHub issues.
* a [project import plugins](https://rdmo.readthedocs.io/en/latest/plugins/index.html#project-import-plugins), which can be used to import projects from (public or private)repos.

The plugin uses [OAUTH 2.0](https://oauth.net/2/), so that users use their respective accounts in both systems.


Setup
-----

Install the plugin in your RDMO virtual environment using pip (directly from GitHub):

```bash
pip install git+https://github.com/rdmorganiser/rdmo-github
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

For the import, add the plugin to `PROJECT_IMPORTS` in `config/settings/local.py`:

```python
PROJECT_IMPORTS = [
    ('github', _('Import from GitHub'), 'rdmo_github.providers.GitHubImport'),
]
```


Usage
-----

### Issue provider

Users can add a GitHub intergration to their projects. They need to provide the URL to their repository.  Afterwards, issues can be pushed to the GitHub repo.

Additionally, a secret can be added to enable GitHub to communicate to RDMO when an issue has been closed. For this, a webhook has to be added at `<https://github.com/<user>/<repo>/settings/hooks`. The webhook has to point to `https://<rdmo_url>/projects/<project_id>/integrations/<integration_id>/webhook/`, the content type is `application/json` and the secret has to be exactly the secret entered in the integration.

### Project import

Users can import project import files directly from a public or private GitHub repository.
