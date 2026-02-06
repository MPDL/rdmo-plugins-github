import base64
import hmac
import json
import logging
import requests

from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext, gettext_lazy as _

from rdmo.core.plugins import get_plugin
from rdmo.projects.providers import OauthIssueProvider
from rdmo.projects.exports import Export

from rdmo_maus.exports.smp_exports import SMPExportMixin

from ..mixins import GitHubProviderMixin
from ..forms.forms import GitHubExportForm
from ..forms.custom_validators import validate_file_path, FilePathExtensionValidator
from ..utils import set_record_id_on_project_value, get_record_id_from_project_value, clear_record_id_from_project_value

logger = logging.getLogger(__name__)

class GitHubExportProvider(GitHubProviderMixin, Export, SMPExportMixin):
    choice_labels = [
        ('xml', _('RDMO XML')),
        ('csvcomma', _('CSV (comma separated)')), 
        ('csvsemicolon', _('CSV (semicolon separated)')), 
        ('json', _('JSON'))
    ]
    
    @property
    def export_choices(self):
        export_choices = []
        for choice_key, choice_label in self.choice_labels:
            file_extension = 'csv' if choice_key.startswith('csv') else choice_key
            catalog = self.project.catalog.uri_path
            catalog = catalog.lower() if isinstance(catalog, str) else 'project_export'
            file_path = f"data/{catalog}{f'_{choice_key}' if file_extension == 'csv' else ''}.{file_extension}"
            file_path_label = _('File path')

            export_choices.append(
                (f'False,{file_path}', (choice_label, file_path_label), choice_key)
            )

        smp_exports = getattr(self, 'smp_exports', None)
        if smp_exports and len(smp_exports) > 0:
            smp_export_choices = [(f'False,{v["file_path"]}', (v["label"], file_path_label), k) for k,v in smp_exports.items()]
            return smp_export_choices + export_choices
        
        return export_choices
    
    @property
    def export_choice_validators(self):
        export_choice_validators = {}

        valid_extensions = {
            'xml': '.xml',
            'csvcomma': '.csv', 
            'csvsemicolon': 'csv', 
            'json': '.json',
        }
        choice_keys = ['xml', 'csvcomma', 'csvsemicolon', 'json']
        
        smp_exports = getattr(self, 'smp_exports', None)
        if smp_exports and len(smp_exports) > 0:
            valid_extensions.update(
                {k: f".{v['file_path'].split('.')[-1]}" for k,v in smp_exports.items() if not k.startswith('license')}
            )
            choice_keys.extend(smp_exports.keys())

        
        for choice_key in choice_keys:
            if choice_key.startswith('license'):
                export_choice_validators[choice_key] = {
                    'text': [validate_file_path]
                }
                continue
            
            export_choice_validators[choice_key] = {
                'text': [validate_file_path, FilePathExtensionValidator(valid_extensions.get(choice_key))]
            }
        
        return export_choice_validators
    
    @property
    def export_choice_attributes(self):
        export_choice_attributes = {}
        for c in self.export_choices:
            simple_checkbox = False
            values = c[0].split(',')
            if isinstance(values, list) and len(values) == 1:
                simple_checkbox = True

            choice_key = c[2]
            if not simple_checkbox:
                export_choice_attributes[choice_key] = {
                    'text': {
                        'placeholder': _('example_folder/example_file.extension'),
                    }
                }

        return export_choice_attributes
                         
    def render(self):
        self.pop_from_session(self.request, 'github_export_choice_warnings')
        
        redirect_url = self.request.build_absolute_uri()
        self.process_app_context(self.request, redirect_url=redirect_url)
        
        access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
        if access_token is None:
            return self.authorize(self.request)
        
        context = {
            'new_repo_name_display': 'none',
            'repo_display': 'block',
            'form': self.get_form(
                self.request, 
                GitHubExportForm, 
                export_choices=self.export_choices,
                export_choice_validators=self.export_choice_validators,
                export_choice_attributes=self.export_choice_attributes
            )
        }
        return render(self.request, 'plugins/github_export_form.html', context, status=200)

    def submit(self):
        form = self.get_form(
            self.request, 
            GitHubExportForm, 
            self.request.POST, 
            export_choices=self.export_choices,
            export_choice_validators=self.export_choice_validators,
            export_choice_attributes=self.export_choice_attributes
        )
        
        if 'cancel' in self.request.POST:
            if self.project is None:
                return redirect('projects')
            else:
                return redirect('project', self.project.id)

        if form.is_valid():
            
            # 1. Validate export choices: Check submitted file paths to warn user if repo files will be overwritten
            export_choice_warnings = self.get_from_session(self.request, 'github_export_choice_warnings')
            new_repo = form.cleaned_data['new_repo']
            if not new_repo and export_choice_warnings is None:
                context, export_choice_warnings = self.validate_export_choices(form.cleaned_data)

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
                return render(self.request, 'core/error.html', {
                    'title': _('Something went wrong'),
                    'errors': [_('Export choices could not be created or repository content would have been overwritten without a warning.')]
                }, status=200)
            
            if new_repo:
                return self.make_request(self.request, 'post', url, json=request_data[0])
            else:
                return self.make_request(self.request, 'put', url, json=request_data[0])
            
        new_repo = True if 'new_repo' in form.data else False
        context = {
            'new_repo_name_display': 'block' if new_repo else 'none',
            'repo_display': 'none' if new_repo else 'block',
            'form': form
        }
        return render(self.request, 'plugins/github_export_form.html', context, status=200)
    
    def validate_sha(self, project, export_choice, url, access_token):
        """Validate the Github sha stored in the project."""

        # Retrieve sha from the project's stored values
        stored_sha = get_record_id_from_project_value(project, export_choice)

        # Send a GET request to Github to validate the stored sha
        response = requests.get(url, headers=self.get_authorization_headers(access_token))
        if response.status_code == 200:
            github_sha = response.json().get('sha')

            if stored_sha != github_sha:
                set_record_id_on_project_value(project, github_sha, export_choice)
                logger.warning(f'GitHubExportProvider - Updating stored sha: stored value for export choice "{export_choice}" does not match with corresponding sha from github.')

            return github_sha
            
        elif response.status_code == 404:
            logger.error(f'GitHubExportProvider - No matching resource for export choice "{export_choice}" found in Github, deleting stored sha if it exists')
            # the export_choice does not exist in GitHub, delete the corresponding sha from the project.value.text
            clear_record_id_from_project_value(project, export_choice)
        else:
            # Log any other unexpected response code
            logger.error(f'GitHubExportProvider - Error validating sha for export choice "{export_choice}": {response.status_code}')

    def check_file_paths(self, exports, repo, branch):
        access_token = self.get_from_session(self.request, 'access_token')
        export_choice_warnings = {}
        choice_keys = []
        for e in exports:
            choice_key, file_path = e.split(',')
            choice_keys.append(choice_key)
            url = self.get_request_url(repo, path=file_path, ref=branch)

            sha = self.validate_sha(self.project, choice_key, url, access_token)
            if sha:
                export_choice_warnings[choice_key] = [gettext('A file with the same path exists in repo and will be overwritten')]

        return export_choice_warnings, choice_keys, exports, branch
    
    def validate_export_choices(self, form_data):
        export_choice_warnings, selected_choice_keys, checked_export_choices, checked_branch = self.check_file_paths(
            form_data['exports'], 
            form_data['repo'], 
            form_data['branch']
        )
        
        self.store_in_session(self.request, 'github_export_choice_warnings', export_choice_warnings)
        self.store_in_session(self.request, 'github_checked_export_choices', checked_export_choices)
        self.store_in_session(self.request, 'github_checked_branch', checked_branch)
        
        selected_choices = [c for c in self.export_choices if c[2] in selected_choice_keys]
        form = self.get_form(
            self.request, 
            GitHubExportForm, 
            self.request.POST, 
            export_choices=selected_choices, 
            export_choice_warnings=export_choice_warnings,
            export_choice_validators=self.export_choice_validators,
            export_choice_attributes=self.export_choice_attributes
        )
        context = {
            'new_repo_name_display': 'none',
            'repo_display': 'block',
            'form': form
        }
        return context, export_choice_warnings

    def render_export(self, choice_key):
        smp_exports = getattr(self, 'smp_exports', None)
        if smp_exports and choice_key in self.smp_exports.keys():
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
        except:
            logger.warning(f'GitHubExportProvider - No content created for {choice_key}')
            choice_content = None

        return choice_content
    
    def process_form_data(self, form_data, update_without_warning=False):
        self.pop_from_session(self.request, 'github_export_choice_warnings')
        request_data = []

        # REPO
        new_repo  = form_data['new_repo']
        repo_html_url = None
        if new_repo:
            request_data.append({
                'name': form_data['new_repo_name'],
                'message': form_data['commit_message'],
                'url': f'{self.api_url}/user/repos'
            })

            repo = 'repo_placeholder'
        else:
            repo = form_data['repo'].replace('https://github.com/', '').strip('/')    
            repo_html_url = 'https://github.com/{repo}'.format(repo=repo)

        # EXPORT OPTIONS
        checked_export_choices = self.pop_from_session(self.request, 'github_checked_export_choices')
        checked_branch = self.pop_from_session(self.request, 'github_checked_branch')
        exports = form_data['exports']
        processed_exports = []
        for e in exports:
            choice_key, file_path = e.split(',')
            initial_file_path = file_path if new_repo else next(
                (exp.split(',')[1] for exp in checked_export_choices if exp.split(',')[0] == choice_key), 
                file_path
            )
            initial_branch = 'main' if new_repo else checked_branch
            branch = 'main' if new_repo else form_data['branch']
            if file_path != initial_file_path or branch != initial_branch:
                new_export_choice_warnings, __, ___, ____ = self.check_file_paths([e], form_data['repo'], branch)
                if choice_key in new_export_choice_warnings.keys() and not update_without_warning:
                    processed_exports.append({
                        'key': choice_key,
                        'label': next((c[1][0] for c in self.export_choices if c[2] == choice_key), choice_key), 
                        'success': False,
                        'processing_status': _('not exported - it would have overwritten existing file in repository.')
                    })
                    continue
            
            choice_request_data = {}
            stored_sha = get_record_id_from_project_value(self.project, choice_key)
            if stored_sha is not None:
                choice_request_data['sha'] = stored_sha
            clear_record_id_from_project_value(self.project, choice_key)

            content = self.render_export_content(choice_key) 
            if content is None:
                success = False
                processing_status = _('not exported - it could not be created.')
            else:
                success = True
                processing_status = _('successfully exported.')

                choice_request_data.update({
                    'message': form_data['commit_message'],
                    'content': content,
                    'branch': branch,
                    'url': self.get_request_url(repo, path=file_path),
                    'choice_key': choice_key
                })
                request_data.append(choice_request_data)

            choice_label = next((c[1][0] for c in self.export_choices if c[2] == choice_key), choice_key)
            processed_exports.append({
                'key': choice_key,
                'label': choice_label,
                'success': success,
                'processing_status': processing_status
            })

        successfully_processed_exports = list(filter(lambda x: x['success'] == True, processed_exports))
        if len(successfully_processed_exports) == 0:
            logger.warning(f'GitHubExportProvider - No export content could be created for the selected choices: {exports}.')
            return None, None

        self.store_in_session(self.request, 'github_processed_exports', processed_exports)
        
        return request_data, repo_html_url
   
    def put_data(self, request, request_data, processed_exports):
        access_token = self.get_from_session(request, 'access_token')
        
        for json in request_data:
            url = json.pop('url')
            choice_key = json.pop('choice_key')
            response = requests.put(url, json=json, headers=self.get_authorization_headers(access_token))

            try:
                response.raise_for_status()
            except Exception as e:
                logger.error(f'GitHubExportProvider - Error putting {choice_key} to github: {e}')
                choice_label = next((c[1][0] for c in self.export_choices if c[2] == choice_key), choice_key)
                index, status = next(
                    ((i, s) for i, s in enumerate(processed_exports) if s['key'] == choice_key), 
                    (   
                        len(processed_exports), 
                        {
                            'key': choice_key,
                            'label': choice_label,
                            'success': False,
                            'processing_status': _('not exported - something went wrong.')
                        }
                    )
                )
                status.update({'success': False, 'processing_status': _('not exported - something went wrong.')})
                processed_exports[index] = status

        return processed_exports
    
    def post_success(self, request, response):
        repo = response.json().get('full_name')
        repo_html_url = response.json().get('html_url')
        
        request_data = self.pop_from_session(request, 'github_export_data')
        processed_exports = self.pop_from_session(request, 'github_processed_exports')

        if isinstance(request_data , list):
            request_data = [{**json, 'url': json['url'].replace('repo_placeholder', repo)} for json in request_data]
            processed_exports = self.put_data(request, request_data, processed_exports)
        
        context = {'repo_html_url': repo_html_url, 'processed_exports': processed_exports}
        return render(request, 'plugins/github_export_success.html', context, status=200)
                
    def put_success(self, request, response):
        request_data = self.pop_from_session(request, 'github_export_data')
        repo_html_url = self.pop_from_session(request, 'github_export_repo')
        processed_exports = self.pop_from_session(request, 'github_processed_exports')

        if isinstance(request_data , list):
            processed_exports = self.put_data(request, request_data, processed_exports)
        
        context = {'repo_html_url': repo_html_url, 'processed_exports': processed_exports}
        return render(request, 'plugins/github_export_success.html', context, status=200)
        

class GitHubIssueProvider(GitHubProviderMixin, OauthIssueProvider):
    add_label = _('Add GitHub integration')
    send_label = _('Send to GitHub')
    description = _('This integration allows the creation of issues in arbitrary GitHub repositories. '
                    'The upload of attachments is not supported by GitHub.')
    
    _fields = {
        'repo_url': {
            'key': 'repo_url',
            'placeholder': 'https://github.com/username/repo',
            'help': _('The URL of the GitHub repository to send issues to.')
        },
        'secret': {
            'key': 'secret',
            'placeholder': 'Secret (random) string',
            'help': _('The secret for a GitHub webhook to close a task (optional).'),
            'required': False,
            'secret': True
        }
    }
    
    def get_post_url(self, request, issue, integration, subject, message, attachments):
        repo_url = integration.get_option_value('repo_url')
        if repo_url:
            repo = repo_url.replace('https://github.com', '').strip('/')
            return '{api_url}/repos/{repo}/issues'.format(
                api_url=self.api_url, 
                repo=repo
            )

    def get_post_data(self, request, issue, integration, subject, message, attachments):
        return {
            'title': subject,
            'body': message
        }

    def get_issue_url(self, response):
        return response.json().get('html_url')

    def webhook(self, request, integration):
        secret = integration.get_option_value('secret')
        header_signature = request.headers.get('X-Hub-Signature')

        if (secret is not None) and (header_signature is not None):
            body_signature = 'sha1=' + hmac.new(secret.encode(), request.body, 'sha1').hexdigest()

            if hmac.compare_digest(header_signature, body_signature):
                try:
                    payload = json.loads(request.body.decode())
                    action = payload.get('action')
                    issue_url = payload.get('issue', {}).get('html_url')

                    if action and issue_url:
                        try:
                            issue_resource = integration.resources.get(url=issue_url)
                            if action == 'closed':
                                issue_resource.issue.status = issue_resource.issue.ISSUE_STATUS_CLOSED
                            else:
                                issue_resource.issue.status = issue_resource.issue.ISSUE_STATUS_IN_PROGRESS

                            issue_resource.issue.save()
                        except ObjectDoesNotExist:
                            pass

                    return HttpResponse(status=200)

                except json.decoder.JSONDecodeError as e:
                    return HttpResponse(e, status=400)

        raise Http404

    @property
    def fields(self):
        return self._fields.values()
    
    @fields.setter
    def fields(self, new_fields):        
        if isinstance(new_fields, dict):
            for k, v in new_fields.items():
                self._fields[k] = v

    def integration_setup(self, request):
        redirect_url = request.build_absolute_uri()
        self.process_app_context(request, redirect_url=redirect_url)
        mininum_repo_permission = 'triage'
        repo_choices, repo_help_text = self.get_repo_form_field_data(request, mininum_repo_permission)

        github_app_repo_url = {**self._fields['repo_url']}
        if repo_choices is not None:
            github_app_repo_url['widget'] = forms.RadioSelect(choices=repo_choices)
            
        if repo_help_text is not None:
            github_app_repo_url['help'] = repo_help_text

        self.fields = {'repo_url': github_app_repo_url}