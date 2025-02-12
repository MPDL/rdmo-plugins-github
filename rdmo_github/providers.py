import base64
import hmac
import json
import logging
import requests
from urllib.parse import quote, urlencode

from django import forms
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.utils.safestring import mark_safe

from rdmo.core.imports import handle_fetched_file
from rdmo.projects.imports import RDMOXMLImport
from rdmo.projects.models.project import Project
from rdmo.projects.providers import OauthIssueProvider
from rdmo.projects.exports import Export

from .mixins import GitHubProviderMixin
from .forms import GitHubExportForm, GitHubImportForm
from .utils import get_project_licenses, render_project_views, set_record_id_on_project_value, get_record_id_from_project_value, clear_record_id_from_project_value

logger = logging.getLogger(__name__)

APP_TYPE = settings.GITHUB_PROVIDER['app_type']

class GitHubExportProvider(GitHubProviderMixin, Export):
    
    def render(self):
        if APP_TYPE == 'github_app':
            redirect_url = self.request.build_absolute_uri()
            self.process_app_context(self.request, redirect_url=redirect_url)

            installation_id = self.get_from_session(self.request, 'installation_id')
            access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
            if installation_id is None or access_token is None:
                return self.authorize(self.request)
        
        new_repo_name_display = None
        repo_display = 'block'
        context = {
            'new_repo_name_display': new_repo_name_display,
            'repo_display': repo_display,
            'form': self.get_form(self.request, GitHubExportForm)
        }
        return render(self.request, 'plugins/github_export_form.html', context, status=200)

    def submit(self):        
        form = self.get_form(self.request, GitHubExportForm, self.request.POST)

        if 'cancel' in self.request.POST:
            if self.project is None:
                return redirect('projects')
            else:
                return redirect('project', self.project.id)

        if form.is_valid():
            new_repo, request_data, repo_html_url = self.process_form_data(form.cleaned_data)
            
            if repo_html_url is not None:
                self.store_in_session(self.request, 'github_export_repo', repo_html_url)

            if len(request_data) > 1:
                self.store_in_session(self.request, 'github_export_data', request_data[1:])
                self.store_in_session(self.request, 'project_id', self.project.id)

            url = request_data[0].get('url')
            
            if new_repo:
                return self.make_request(self.request, 'post', url, json=request_data[0])
            else:
                return self.make_request(self.request, 'put', url, json=request_data[0], data_processing_params={'project_id': self.project.id})

        new_repo_name_display = 'block' if form.cleaned_data['new_repo'] else None
        repo_display = None if form.cleaned_data['new_repo'] else 'block'
        context = {
            'new_repo_name_display': new_repo_name_display,
            'repo_display': repo_display,
            'form': form
        }
        return render(self.request, 'plugins/github_export_form.html', context, status=200)
    
    def get_option_content(self, option):
        view_map = {
            'readme': {
                'view_uri': 'https://dev-rdmo.mpdl.mpg.de/terms/views/smp_github_readme',
                'attachments_format': 'markdown',
                'export_format': 'readme.md',
                'path': 'contents/README.md'
            },
            'citation': {
                'view_uri': 'https://dev-rdmo.mpdl.mpg.de/terms/views/smp_github_citation',
                'attachments_format': 'plain',
                'export_format': 'citation.cff',
                'path': 'contents/CITATION.cff'
            }
        }
        function_map = {
            'license': get_project_licenses
        }

        if option in view_map.keys():
            view_uri, attachments_format, export_format, path = view_map[option].values()
            smp_readme_response = render_project_views(self.project, self.snapshot, attachments_format, view_uri) # , context)
            binary = smp_readme_response.content
            base64_bytes_of_content = base64.b64encode(binary)
            base64_string_of_content = base64_bytes_of_content.decode('utf-8')
            option_content = {'export_format': export_format, 'content': base64_string_of_content, 'path': path}

        else:
            option_content = function_map[option](self.project)
        
        return option_content
    
    def process_form_data(self, form_data):
        request_data = []

        # REPO
        new_repo  = form_data['new_repo']
        repo_html_url = None
        if new_repo:
            new_repo_name = quote(form_data['new_repo_name'])
            url = f'{self.api_url}/user/repos'
            request_data.append({
                'name': new_repo_name,
                'message': quote(form_data['commit_message']),
                'url': url
            })

            repo = 'repo_placeholder'
        else:
            repo = quote(form_data['repo'].replace('https://github.com/', ''))        
            repo_html_url = 'https://github.com/{repo}'.format(repo=repo)

        # EXPORT OPTIONS
        export_options = eval(form_data['export_options']) # string list to list
        option_contents = []
        for o in export_options:
            option = o.replace('github_export/', '')
            content = self.get_option_content(option)
            if isinstance(content, list):
                option_contents.extend(content)
            else:
                option_contents.append(content)

        for content in option_contents:
            url = '{api_url}/repos/{repo}/{path}'.format(api_url=self.api_url, repo=repo, path=content.pop('path'))

            request_data.append({
                **content,
                'message': form_data['commit_message'],
                'url': url
            })
        
        return new_repo, request_data, repo_html_url
    
    def validate_sha(self, project, export_format, url, access_token):
        """Validate the Github sha stored in the project."""

        # Retrieve sha from the project's stored values
        stored_sha = get_record_id_from_project_value(project, export_format)
        
        # Send a GET request to Github to validate the stored sha
        response = requests.get(url, headers=self.get_authorization_headers(access_token))
        
        if response.status_code == 200:
            github_sha = response.json().get('sha')
            if stored_sha == github_sha:
                logger.info(f'Stored sha {stored_sha} for export format "{export_format}" is valid.')
            else:
                set_record_id_on_project_value(project, github_sha, export_format)
                logger.warning(f'Updating stored sha: stored value for export format "{export_format}" does not match with corresponding sha from github.')

            return github_sha
            
        elif response.status_code == 404:
            logger.warning(f'No matching resource for export format "{export_format}" found in Github, deleting stored sha if it exists')
            # the export_format does not exist in GitHub, delete the corresponding sha from the project.value.text
            clear_record_id_from_project_value(project, export_format)
        else:
            # Log any other unexpected response code
            logger.error(f'Error validating sha for export format "{export_format}": {response.status_code}')
    
    def process_request_data(self, data, project_id, access_token):
        project = Project.objects.get(pk=project_id)
        export_format = data.pop('export_format')
        url = data.pop('url')
        sha = self.validate_sha(project, export_format, url, access_token)

        if sha is not None:
            data['sha'] = sha

        return data
   
    def put_data(self, request, request_data):
        access_token = self.get_from_session(request, 'access_token')
        project_id = self.pop_from_session(request, 'project_id')
        
        successful_uploads = []
        for json in request_data:
            export_format = json.get('export_format')
            url = json.get('url')
            processed_json = self.process_request_data(json, project_id, access_token)
            response = requests.put(url, json=processed_json, headers=self.get_authorization_headers(access_token))

            try:
                response.raise_for_status()
                github_sha = response.json().get('content', {}).get('sha')
                export_format = response.json().get('content', {}).get('name')
                if github_sha:
                    set_record_id_on_project_value(self.project, github_sha, export_format.lower())
                    successful_uploads.append({'export_format': export_format, 'success': True})

            except Exception as e:
                logger.warning(f'error putting {export_format} to github: {e}')
                successful_uploads.append({'export_format': export_format, 'success': False})
                continue

        return successful_uploads
    
    def post_success(self, request, response):
        repo = response.json().get('full_name')
        repo_html_url = response.json().get('html_url')
        request_data = self.pop_from_session(request, 'github_export_data')

        if isinstance(request_data , list):
            request_data = [{**json, 'url': json['url'].replace('repo_placeholder', repo)} for json in request_data]
            successful_uploads = self.put_data(request, request_data)

            context = {'repo_html_url': repo_html_url, 'successful_uploads': successful_uploads}
            return render(request, 'plugins/github_export_success.html', context, status=200)
        else:
            return HttpResponseRedirect(repo_html_url)
        
    def put_success(self, request, response):
        github_sha = response.json().get('content', {}).get('sha')
        export_format = response.json().get('content', {}).get('name')
        if github_sha:
            set_record_id_on_project_value(self.project, github_sha, export_format.lower())

        request_data = self.pop_from_session(request, 'github_export_data')
        repo_html_url = self.pop_from_session(request, 'github_export_repo')

        successful_uploads = [{'export_format': export_format, 'success': True}]
        if isinstance(request_data , list):
            successful_uploads += self.put_data(request, request_data)
        else:
            content_html_url = response.json().get('content', {}).get('html_url')
            return HttpResponseRedirect(content_html_url)

        context = {'repo_html_url': repo_html_url, 'successful_uploads': successful_uploads}
        return render(request, 'plugins/github_export_success.html', context, status=200)


# class GitHubIssueProvider(GitHubProviderMixin, OauthIssueProvider):
#     add_label = _('Add GitHub integration')
#     send_label = _('Send to GitHub')
#     description = _('This integration allows the creation of issues in arbitrary GitHub repositories. '
#                     'The upload of attachments is not supported by GitHub.')
#     repo_url = {
#         'key': 'repo_url',
#         'placeholder': 'https://github.com/username/repo',
#         'help': _('The URL of the GitHub repository to send issues to.')
#     }
#     secret = {
#         'key': 'secret',
#         'placeholder': 'Secret (random) string',
#         'help': _('The secret for a GitHub webhook to close a task (optional).'),
#         'required': False,
#         'secret': True
#     }

#     def get_post_url(self, request, issue, integration, subject, message, attachments):
#         repo_url = integration.get_option_value('repo_url')
#         if repo_url:
#             repo = repo_url.replace('https://github.com', '').strip('/')
#             return f'https://api.github.com/repos/{repo}/issues'

#     def get_post_data(self, request, issue, integration, subject, message, attachments):
#         return {
#             'title': subject,
#             'body': message
#         }

#     def get_issue_url(self, response):
#         return response.json().get('html_url')

#     def webhook(self, request, integration):
#         secret = integration.get_option_value('secret')
#         header_signature = request.headers.get('X-Hub-Signature')

#         if (secret is not None) and (header_signature is not None):
#             body_signature = 'sha1=' + hmac.new(secret.encode(), request.body, 'sha1').hexdigest()

#             if hmac.compare_digest(header_signature, body_signature):
#                 try:
#                     payload = json.loads(request.body.decode())
#                     action = payload.get('action')
#                     issue_url = payload.get('issue', {}).get('html_url')

#                     if action and issue_url:
#                         try:
#                             issue_resource = integration.resources.get(url=issue_url)
#                             if action == 'closed':
#                                 issue_resource.issue.status = issue_resource.issue.ISSUE_STATUS_CLOSED
#                             else:
#                                 issue_resource.issue.status = issue_resource.issue.ISSUE_STATUS_IN_PROGRESS

#                             issue_resource.issue.save()
#                         except ObjectDoesNotExist:
#                             pass

#                     return HttpResponse(status=200)

#                 except json.decoder.JSONDecodeError as e:
#                     return HttpResponse(e, status=400)

#         raise Http404

#     @property
#     def fields(self):
#         return [self.repo_url, self.secret]
    
#     @fields.setter
#     def fields(self, new_fields):
#         if new_fields is not None:
#             self.repo_url = new_fields[0]
#             self.secret = new_fields[1]

#     def integration_setup(self, request, *args, **kwargs):

#         if APP_TYPE == 'github_app':
#             self.process_app_context(request, **kwargs)

#             installation_id = self.get_from_session(request, 'installation_id')
#             access_token = self.validate_access_token(request, self.get_from_session(request, 'access_token'))
#             # check if app was already installed
#             if installation_id is None:
#                 state = self.get_state(request)
#                 installation_url = self.install_url + '?' + urlencode(self.get_install_params(state))
#                 repo_choices = []
#                 link_label = _('Install App')
#                 link_help_text = _('To connect to GitHub repos, you first need to install the MPDL app.')
#                 repo_help_text = mark_safe(f'{link_help_text} <a href="{installation_url}">{link_label}</a>')
#             # Check if app was already authorized
#             elif access_token is None:
#                 state = self.get_state(request)
#                 authorization_url = self.authorize_url + '?' + urlencode(self.get_authorize_params(request, state))
#                 repo_choices = []
#                 link_label = _('Authorize App')
#                 link_help_text = _('To connect to GitHub repos, you first need to authorize the MPDL app.')
#                 repo_help_text = mark_safe(f'{link_help_text} <a href="{authorization_url}">{link_label}</a>')
#             # get repo choices and app link to update them
#             else:
#                 repo_choices, repo_help_text = self.get_repo_form_field_data(request)

#             github_app_repo_url = {**self.repo_url}
#             github_app_repo_url['widget'] = forms.RadioSelect(choices=repo_choices)
#             github_app_repo_url['help'] = repo_help_text

#             self.repo_url = github_app_repo_url

#             return self.fields


class GitHubImport(GitHubProviderMixin, RDMOXMLImport):

    def render(self):
        if APP_TYPE == 'github_app':
            redirect_url = self.request.build_absolute_uri()
            self.process_app_context(self.request, redirect_url=redirect_url)

            installation_id = self.get_from_session(self.request, 'installation_id')
            access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
            if installation_id is None or access_token is None:
                return self.authorize(self.request)
        
        repo_display = 'block'
        other_repo_display = None
        context = {
            'source_title': 'GitHub',
            'app_type': APP_TYPE,
            'repo_display': repo_display,
            'other_repo_display': other_repo_display,
            'form': self.get_form(self.request, GitHubImportForm)
        }
        return render(self.request, 'plugins/github_import_form.html', context, status=200)

    def submit(self):
        form = self.get_form(self.request, GitHubImportForm, self.request.POST)

        if 'cancel' in self.request.POST:
            if self.project is None:
                return redirect('projects')
            else:
                return redirect('project', self.project.id)

        if form.is_valid():            
            self.request.session['import_source_title'] = self.source_title = form.cleaned_data['path']

            url = self.process_form_data(form.cleaned_data)

            return self.make_request(self.request, 'get', url)

        repo_display = None if form.cleaned_data['other_repo_check'] else 'block'
        other_repo_display = 'block' if form.cleaned_data['other_repo_check'] else None
        context = {
            'source_title': 'GitHub',
            'app_type': APP_TYPE,
            'repo_display': repo_display,
            'other_repo_display': other_repo_display,
            'form': form
        }

        return render(self.request, 'plugins/github_import_form.html', context, status=200)
    
    def process_form_data(self, form_data):
        other_repo_check  = form_data['other_repo_check']
        if other_repo_check and APP_TYPE == 'github_app':
            repo = quote(form_data['other_repo'].replace('https://github.com/', ''))
        else:
            repo = quote(form_data['repo'].replace('https://github.com/', ''))

        url = '{api_url}/repos/{repo}/contents/{path}?ref={ref}'.format(
            api_url=self.api_url,
            repo=repo,
            path=quote(form_data['path']),
            ref=quote(form_data['ref'])
        )

        return url

    def get_success(self, request, response):
        file_content = response.json().get('content')
        request.session['import_file_name'] = handle_fetched_file(base64.b64decode(file_content))

        if self.current_project:
            return redirect('project_update_import', self.current_project.id)
        else:
            return redirect('project_create_import')
