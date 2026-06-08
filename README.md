rdmo-plugins-github
===========

This repo implements three plugins for [RDMO](https://github.com/rdmorganiser/rdmo):

* an [issue provider](https://rdmo.readthedocs.io/en/latest/plugins/index.html#issue-providers), which lets users push their tasks from RDMO to GitHub issues.
* an [import provider](https://rdmo.readthedocs.io/en/latest/plugins/index.html#project-import-plugins), which can be used to import projects from (public or private) repositories. For SMP projects, repository metadata (dependecy graph, languages, license, CITATION or CodeMeta) can also be imported.
* an [export provider](https://rdmo.readthedocs.io/en/latest/plugins/index.html#project-export-plugins), which can be used to export projects to (public or private) repositories. For SMP projects, this plugin also provides other export choices that reuse project data (e.g. README, CITATION, CodeMeta or LICENSE files).

The plugins use [OAUTH 2.0](https://oauth.net/2/), so that users use their respective accounts in both systems.


Setup
-----

Install the plugin in your RDMO virtual environment using pip (directly from GitHub):

```bash
pip install git+https://github.com/MPDL/rdmo-plugins-github@dev
```

An *App* has to be registered with GitHub. Go to https://github.com/settings/developers and create an application with your RDMO URL as callback URL. GitHub offers two types of apps: [GitHub Apps](https://docs.github.com/en/apps/using-github-apps/about-using-github-apps) and [OAuth Apps](https://docs.github.com/en/apps/oauth-apps), both app types use [OAUTH 2.0](https://oauth.net/2/).

The `client_id`, the `client_secret`, the `app_type` (`oauth_app` or `github_app`), and the `github_app_name` ([Register a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app)) need to be configured in `config/settings/local.py`:

```python
GITHUB_PROVIDER = {
    'client_id': '',
    'client_secret': '',
    'app_type': 'oauth_app' | 'github_app',
    'github_app_name': ''
}
```

For the issue provider, add the plugin to `PROJECT_ISSUE_PROVIDERS` in `config/settings/local.py`:

```python
PROJECT_ISSUE_PROVIDERS += [
    ('github', _('GitHub Provider'), 'rdmo_github.providers.exports.GitHubIssueProvider'),
]
```

For the import, add the plugin to `PROJECT_IMPORTS` and its key to `PROJECT_IMPORTS_LIST` in `config/settings/local.py`:

```python
PROJECT_IMPORTS = [
    ('github', _('GitHub'), 'rdmo_github.providers.imports.GitHubImportProvider'),
]

PROJECT_IMPORTS_LIST += ['github']
```

For the export, add the plugin to `PROJECT_EXPORTS` in `config/settings/local.py`:

```python
PROJECT_EXPORTS += [
    ('github', _('Github'), 'rdmo_github.providers.exports.GitHubExportProvider'),
]
```

The export and import plugins use the plugin [rdmo_maus](https://github.com/MPDL/rdmo-plugins-maus). This plugin provides the SMP specific import and export choices as well as a custom field used in their form templates. Install rdmo_maus in your RDMO virtual environment using pip (directly from GitHub):

```bash
pip install git+https://github.com/MPDL/rdmo-plugins-maus
```


Usage
-----

### Issue provider

Users can add a GitHub intergration to their projects. They need to provide the URL to their repository. Afterward, project tasks can be pushed to the GitHub repository as issues.

Additionally, a secret can be added to enable GitHub to communicate to RDMO when an issue has been closed. For this, a webhook has to be added at `<https://github.com/<user>/<repo>/settings/hooks`. The webhook has to point to `https://<rdmo_url>/projects/<project_id>/integrations/<integration_id>/webhook/`, the content type is `application/json` and the secret has to be exactly the secret entered in the integration.

### Project import

Users can import xml project files, and for SMP projects also repository metadata (dependency graph, languages, license, CITATION or CodeMeta files) directly from a public or private GitHub repository.

### Project export

Users can export project files directly to a public or private GitHub repository. For SMP projects, they can also export custom files (README, CITATION, CodeMeta, LICENSE) created with the SMP project's data. They can choose to export to an existing repository or to create a new one.