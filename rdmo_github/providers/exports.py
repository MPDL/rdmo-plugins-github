import base64
import logging

from django.shortcuts import redirect, render
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

import requests
from rdmo_maus.exports.smp_exports import SMPExportMixin
from rdmo_maus.forms.validators import FilePathExtensionValidator, validate_file_path

from rdmo.core.plugins import get_plugin
from rdmo.projects.exports import Export

from ..forms.forms import GitHubExportForm
from ..mixins import GitHubProviderMixin

logger = logging.getLogger(__name__)


class GitHubExportProvider(GitHubProviderMixin, Export, SMPExportMixin):
    @property
    def export_choices(self):

        catalog = self.project.catalog.uri_path
        catalog = catalog.lower() if isinstance(catalog, str) else 'project_export'

        export_choices = {  # check MultivalueCheckboxMultipleChoiceField in rdmo_maus.forms.fields.py for details
            'choices': [
                (f'True,data/{catalog}.xml', ('RDMO XML', _('File path')), 'xml'),
                (f'True,data/{catalog}_comma_separated.csv', (_('CSV (comma separated)'), _('File path')), 'csvcomma'),
                (
                    f'True,data/{catalog}_semicolon_separated.csv',
                    (_('CSV (semicolon separated)'), _('File path')),
                    'csvsemicolon',
                ),
                (f'True,data/{catalog}.json', ('JSON', _('File path')), 'json'),
            ],
            'choice_validators': {
                'xml': {'text': [validate_file_path, FilePathExtensionValidator('.xml')]},
                'csvcomma': {'text': [validate_file_path, FilePathExtensionValidator('.csv')]},
                'csvsemicolon': {'text': [validate_file_path, FilePathExtensionValidator('.csv')]},
                'json': {'text': [validate_file_path, FilePathExtensionValidator('.json')]},
            },
            'choice_attributes': {
                'xml': {
                    'text': {
                        'placeholder': _('example_folder/example_xml_file.xml'),
                    }
                },
                'csvcomma': {
                    'text': {
                        'placeholder': _('example_folder/example_csv_file.csv'),
                    }
                },
                'csvsemicolon': {
                    'text': {
                        'placeholder': _('example_folder/example_csv_file.csv'),
                    }
                },
                'json': {
                    'text': {
                        'placeholder': _('example_folder/example_json_file.json'),
                    }
                },
            },
        }

        smp_export_choices = getattr(self, 'smp_export_choices', None)
        if smp_export_choices:
            smp_export_choices.get('choices', []).extend(export_choices.get('choices', []))
            export_choices['choices'] = smp_export_choices.get('choices', [])
            export_choices['choice_validators'].update(smp_export_choices.get('choice_validators', {}))
            export_choices['choice_attributes'].update(smp_export_choices.get('choice_attributes', {}))

        return export_choices

    def render(self):
        self.pop_from_session(self.request, 'github_export_choice_warnings')

        access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
        if access_token is None:
            redirect_url = self.request.build_absolute_uri()
            self.store_in_session(self.request, 'redirect_url', redirect_url)
            return self.authorize(self.request)

        repo_choices, repo_help_text = self.get_repo_form_field_data(access_token, minimum_repo_permission='push')
        form_kwargs = {
            'repo_choices': repo_choices,
            'repo_help_text': repo_help_text,
            'export_choices': self.export_choices,
        }
        context = {'form': GitHubExportForm(**form_kwargs)}
        return render(self.request, 'plugins/github_export_form.html', context, status=200)

    def submit(self):
        if 'cancel' in self.request.POST:
            if self.project is None:
                return redirect('projects')
            else:
                return redirect('project', self.project.id)

        access_token = self.get_from_session(self.request, 'access_token')
        repo_choices, repo_help_text = self.get_repo_form_field_data(access_token, minimum_repo_permission='push')
        form_kwargs = {
            'repo_choices': repo_choices,
            'repo_help_text': repo_help_text,
            'export_choices': self.export_choices,
        }
        form = GitHubExportForm(self.request.POST, **form_kwargs)
        if form.is_valid():
            # 1. Validate export choices: Check submitted file paths to warn user if repo files will be overwritten
            export_choice_warnings = self.get_from_session(self.request, 'github_export_choice_warnings')
            new_repo = form.cleaned_data.get('new_repo')

            if not new_repo and export_choice_warnings is None:
                context, export_choice_warnings = self.validate_export_choices(form)

                if len(export_choice_warnings) > 0:
                    return render(self.request, 'plugins/github_export_form.html', context, status=200)

            # 2. Create file content for selected choices and export them
            request_data, repo_html_url = self.process_form_data(form.cleaned_data)

            if repo_html_url is not None:
                self.store_in_session(self.request, 'github_export_repo', repo_html_url)

            if request_data:
                url = request_data[0].pop('url')
                if len(request_data) > 1:
                    self.store_in_session(self.request, 'github_export_data', request_data[1:])
            else:
                return render(
                    self.request,
                    'core/error.html',
                    {
                        'title': _('Something went wrong'),
                        'errors': [
                            _(
                                'Either the export choices could not be created or '
                                'the repository content would have been overwritten without a warning.'
                            )
                        ],
                    },
                    status=200,
                )

            if new_repo:
                return self.post(self.request, url, json=request_data[0])
            else:
                return self.put(self.request, url, json=request_data[0])

        return render(self.request, 'plugins/github_export_form.html', {'form': form}, status=200)

    def validate_sha(self, request, export_choice, url):
        """Validate the Github sha stored in the session."""

        # Retrieve sha from the session
        stored_sha = self.get_from_session(request, f'github_sha_{export_choice}')

        # Send a GET request to Github to validate the stored sha
        access_token = self.get_from_session(self.request, 'access_token')
        response = requests.get(url, headers=self.get_authorization_headers(access_token))
        if response.status_code == 200:
            github_sha = response.json().get('sha')

            if stored_sha != github_sha:
                self.store_in_session(request, f'github_sha_{export_choice}', github_sha)
                logger.warning(
                    'GitHubExportProvider - Updating stored sha: stored value for export choice '
                    '"%s" does not match with corresponding sha from github.',
                    export_choice,
                )

            return github_sha

        elif response.status_code == 404:
            logger.error(
                'GitHubExportProvider - No matching resource for export choice "%s" found '
                'in Github, deleting stored sha if it exists',
                export_choice,
            )
            # the export_choice does not exist in GitHub, delete the corresponding sha from the session - if it exists
            self.pop_from_session(request, f'github_sha_{export_choice}')
        else:
            # Log any other unexpected response code
            logger.error(
                'GitHubExportProvider - Error validating sha for export choice "%s": %s',
                export_choice,
                response.status_code,
            )

    def check_file_paths(self, exports, repo, branch):
        export_choice_warnings = {}
        choice_keys = []
        for export in exports:
            choice_key, file_path = export.split(',')
            choice_keys.append(choice_key)
            url = self.get_request_url(repo, path=file_path, ref=branch)
            sha = self.validate_sha(self.request, choice_key, url)
            if sha:
                export_choice_warnings[choice_key] = [
                    gettext('A file with the same path exists in the selected repository and will be overwritten')
                ]

        return export_choice_warnings, choice_keys, exports, branch

    def validate_export_choices(self, form):
        export_choice_warnings, selected_choice_keys, checked_export_choices, checked_branch = self.check_file_paths(
            form.cleaned_data.get('exports'), form.cleaned_data.get('repo'), form.cleaned_data.get('branch')
        )

        self.store_in_session(self.request, 'github_export_choice_warnings', export_choice_warnings)
        self.store_in_session(self.request, 'github_checked_export_choices', checked_export_choices)
        self.store_in_session(self.request, 'github_checked_branch', checked_branch)

        selected_choices = [c for c in self.export_choices.get('choices', []) if c[2] in selected_choice_keys]

        form.fields['exports'].choices = selected_choices
        form.fields['exports'].widget.choice_warnings = export_choice_warnings

        context = {'form': form}

        return context, export_choice_warnings

    def render_export(self, choice_key):
        smp_export_choice_keys = getattr(self, 'smp_export_choice_keys', None)
        if smp_export_choice_keys and choice_key in smp_export_choice_keys:
            response = self.render_smp_export(choice_key)
        else:
            export_plugin = get_plugin('PROJECT_EXPORTS', choice_key)
            export_plugin.project = self.project
            response = export_plugin.render()

        return response

    def render_export_content(self, choice_key):
        response = self.render_export(choice_key)
        try:
            binary = response.content
            base64_bytes_of_content = base64.b64encode(binary)
            base64_string_of_content = base64_bytes_of_content.decode('utf-8')
            choice_content = base64_string_of_content
        except AttributeError:
            logger.warning('GitHubExportProvider - No content created for %s', choice_key)
            choice_content = None

        return choice_content

    def process_form_data(self, form_data, update_without_warning=False):
        self.pop_from_session(self.request, 'github_export_choice_warnings')
        request_data = []

        # REPO
        new_repo = form_data.get('new_repo')
        repo_html_url = None
        if new_repo:
            request_data.append(
                {
                    'name': form_data.get('new_repo_name'),
                    'message': form_data.get('commit_message'),
                    'url': f'{self.api_url}/user/repos',
                    'private': False,
                }
            )

            repo = 'repo_placeholder'
        else:
            repo = form_data['repo'].replace('https://github.com/', '').strip('/')
            repo_html_url = f'https://github.com/{repo}'

        # EXPORT OPTIONS
        checked_export_choices = self.pop_from_session(self.request, 'github_checked_export_choices')
        checked_branch = self.pop_from_session(self.request, 'github_checked_branch')
        branch = 'main' if new_repo else form_data['branch']

        exports = form_data.get('exports')
        processed_exports = []
        for export in exports:
            choice_key, file_path = export.split(',')
            initial_file_path = (
                file_path
                if new_repo
                else next(
                    (exp.split(',')[1] for exp in checked_export_choices if exp.split(',')[0] == choice_key), file_path
                )
            )
            initial_branch = 'main' if new_repo else checked_branch

            if file_path != initial_file_path or branch != initial_branch:
                new_export_choice_warnings, _choice_keys, _exports, _branch = self.check_file_paths(
                    [export], form_data['repo'], branch
                )
                if choice_key in new_export_choice_warnings and not update_without_warning:
                    processed_exports.append(
                        {
                            'key': choice_key,
                            'label': next(
                                (c[1][0] for c in self.export_choices.get('choices', []) if c[2] == choice_key),
                                choice_key,
                            ),
                            'success': False,
                            'processing_status': _(
                                'not exported - it would have overwritten existing file '
                                'in repository without a warning.'
                            ),
                        }
                    )
                    continue

            choice_request_data = {}
            stored_sha = self.pop_from_session(self.request, f'github_sha_{choice_key}')
            if stored_sha is not None:
                choice_request_data['sha'] = stored_sha

            content = self.render_export_content(choice_key)
            if content is None:
                success = False
                processing_status = _('not exported - it could not be created.')
            else:
                success = True
                processing_status = _('successfully exported.')

                choice_request_data.update(
                    {
                        'message': form_data.get('commit_message'),
                        'content': content,
                        'branch': branch,
                        'url': self.get_request_url(repo, path=file_path),
                        'choice_key': choice_key,
                    }
                )
                request_data.append(choice_request_data)

            choice_label = next(
                (c[1][0] for c in self.export_choices.get('choices', []) if c[2] == choice_key), choice_key
            )
            processed_exports.append(
                {'key': choice_key, 'label': choice_label, 'success': success, 'processing_status': processing_status}
            )

        successfully_processed_exports = list(filter(lambda x: x['success'], processed_exports))
        if len(successfully_processed_exports) == 0:
            logger.warning(
                'GitHubExportProvider - No export content could be created for the selected choices: %s.', exports
            )
            return None, None

        self.store_in_session(self.request, 'github_processed_exports', processed_exports)

        return request_data, repo_html_url

    def put_data(self, request, request_data, processed_exports):
        access_token = self.get_from_session(request, 'access_token')

        for file_json in request_data:
            url = file_json.pop('url')
            choice_key = file_json.pop('choice_key')
            response = requests.put(url, json=file_json, headers=self.get_authorization_headers(access_token))

            try:
                response.raise_for_status()
            except requests.HTTPError:
                logger.error(
                    'GitHubExportProvider - Error putting %s to github: %s (%s)',
                    choice_key,
                    response.content,
                    response.status_code,
                )
                choice_label = next(
                    (c[1][0] for c in self.export_choices.get('choices', []) if c[2] == choice_key), choice_key
                )
                index, status = next(
                    ((i, s) for i, s in enumerate(processed_exports) if s['key'] == choice_key),
                    (
                        len(processed_exports),
                        {
                            'key': choice_key,
                            'label': choice_label,
                            'success': False,
                            'processing_status': _('not exported - something went wrong.'),
                        },
                    ),
                )
                status.update({'success': False, 'processing_status': _('not exported - something went wrong.')})
                processed_exports[index] = status

        return processed_exports

    def post_success(self, request, response):
        repo = response.json().get('full_name')
        repo_html_url = response.json().get('html_url')

        request_data = self.pop_from_session(request, 'github_export_data')
        processed_exports = self.pop_from_session(request, 'github_processed_exports')

        if isinstance(request_data, list):
            request_data = [
                {**file_json, 'url': file_json['url'].replace('repo_placeholder', repo)} for file_json in request_data
            ]
            processed_exports = self.put_data(request, request_data, processed_exports)

        successful_exports = list(filter(lambda x: x['success'], processed_exports))
        if len(successful_exports) == len(processed_exports):
            return redirect(repo_html_url)

        context = {'repo_html_url': repo_html_url, 'processed_exports': processed_exports}
        return render(request, 'plugins/github_export_success.html', context, status=200)

    def put_success(self, request, response):
        request_data = self.pop_from_session(request, 'github_export_data')
        repo_html_url = self.pop_from_session(request, 'github_export_repo')
        processed_exports = self.pop_from_session(request, 'github_processed_exports')

        if isinstance(request_data, list):
            processed_exports = self.put_data(request, request_data, processed_exports)

        successful_exports = list(filter(lambda x: x['success'], processed_exports))
        if len(successful_exports) == len(processed_exports):
            return redirect(repo_html_url)

        context = {'repo_html_url': repo_html_url, 'processed_exports': processed_exports}
        return render(request, 'plugins/github_export_success.html', context, status=200)
