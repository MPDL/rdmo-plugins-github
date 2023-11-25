import base64
import hmac
import json
from urllib.parse import quote

from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from rdmo.core.imports import handle_fetched_file
from rdmo.projects.imports import RDMOXMLImport
from rdmo.projects.providers import OauthIssueProvider

from .mixins import GitHubProviderMixin


class GitHubIssueProvider(GitHubProviderMixin, OauthIssueProvider):
    add_label = _('Add GitHub integration')
    send_label = _('Send to GitHub')
    description = _('This integration allow the creation of issues in arbitrary GitHub repositories. '
                    'The upload of attachments is not supported by GitHub.')

    def get_post_url(self, request, issue, integration, subject, message, attachments):
        return integration.get_option_value('repo_url')

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
        return [
            {
                'key': 'repo_url',
                'placeholder': 'https://github.com/username/repo',
                'help': _('The URL of the GitHub repository to send issues to.')
            },
            {
                'key': 'secret',
                'placeholder': 'Secret (random) string',
                'help': _('The secret for a GitHub webhook to close a task (optional).'),
                'required': False,
                'secret': True
            }
        ]


class GitHubImport(GitHubProviderMixin, RDMOXMLImport):

    class Form(forms.Form):
        repo = forms.CharField(label=_('GitHub repository'),
                               help_text=_('Please use the form username/repository or organization/repository.'))
        path = forms.CharField(label=_('File path'))
        ref = forms.CharField(label=_('Branch, tag, or commit'), initial='main')

    def render(self):
        return render(self.request, 'projects/project_import_form.html', {
            'source_title': 'GitHub',
            'form': self.Form()
        }, status=200)

    def submit(self):
        form = self.Form(self.request.POST)

        if 'cancel' in self.request.POST:
            if self.project is None:
                return redirect('projects')
            else:
                return redirect('project', self.project.id)

        if form.is_valid():
            self.request.session['import_source_title'] = self.source_title = form.cleaned_data['path']

            url = '{api_url}/repos/{repo}/contents/{path}?ref={ref}'.format(
                api_url=self.api_url,
                repo=quote(form.cleaned_data['repo']),
                path=quote(form.cleaned_data['path']),
                ref=quote(form.cleaned_data['ref'])
            )

            return self.get(self.request, url)

        return render(self.request, 'projects/project_import_form.html', {
            'source_title': 'GitHub',
            'form': form
        }, status=200)

    def get_success(self, request, response):
        file_content = response.json().get('content')
        request.session['import_file_name'] = handle_fetched_file(base64.b64decode(file_content))

        if self.current_project:
            return redirect('project_update_import', self.current_project.id)
        else:
            return redirect('project_create_import')
