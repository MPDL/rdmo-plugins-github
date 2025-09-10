import base64
import logging
from urllib.parse import quote

from django.conf import settings
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from rdmo.core.imports import handle_fetched_file
from rdmo.projects.imports import RDMOXMLImport

from ..mixins import GitHubProviderMixin
from ..forms.forms import GitHubImportForm

logger = logging.getLogger(__name__)

APP_TYPE = settings.GITHUB_PROVIDER['app_type']

class GitHubImport(GitHubProviderMixin, RDMOXMLImport):

    def render(self):
        redirect_url = self.request.build_absolute_uri()
        self.process_app_context(self.request, redirect_url=redirect_url)
        
        access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
        if access_token is None:
            return self.authorize(self.request)
        
        context = {
            'source_title': 'GitHub',
            'app_type': APP_TYPE,
            'repo_display': 'block',
            'other_repo_display': 'none',
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

        other_repo_check = True if 'other_repo_check' in form.data else False
        repo_display = 'none' if other_repo_check else 'block'
        other_repo_display = 'block' if other_repo_check else 'none'
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
        if other_repo_check:
            repo = form_data['other_repo']
        else:
            repo = form_data['repo']

        # other_repo is form-validated and repo exists, 
        # but path and ref are not form-validated so must be url encoded 
        url = self.get_request_url(repo, quote(form_data['path']), quote(form_data['ref'])) 
        
        return url

    def get_success(self, request, response):
        file_content = response.json().get('content')
        request.session['import_file_name'] = handle_fetched_file(base64.b64decode(file_content))

        if self.current_project:
            return redirect('project_update_import', self.current_project.id)
        else:
            return redirect('project_create_import')
