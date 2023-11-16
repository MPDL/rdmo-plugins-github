import hmac
import json

from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponse
from django.utils.translation import gettext_lazy as _

from rdmo.projects.providers import OauthIssueProvider

from .mixins import GitHubProviderMixin


class GitHubIssueProvider(GitHubProviderMixin, OauthIssueProvider):
    add_label = _('Add GitHub integration')
    send_label = _('Send to GitHub')
    description = _('This integration allow the creation of issues in arbitrary GitHub repositories. '
                    'The upload of attachments is not supported by GitHub.')

    def get_post_url(self, request, issue, integration, subject, message, attachments):
        repo_url = integration.get_option_value('repo_url')
        if repo_url:
            repo = repo_url.replace('https://github.com', '').strip('/')
            return f'https://api.github.com/repos/{repo}/issues'

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
