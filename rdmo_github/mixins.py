from django.conf import settings
from django.urls import reverse

from rdmo.services.providers import OauthProviderMixin


class GitHubProviderMixin(OauthProviderMixin):
    authorize_url = 'https://github.com/login/oauth/authorize'
    token_url = 'https://github.com/login/oauth/access_token'
    api_url = 'https://api.github.com'

    @property
    def client_id(self):
        return settings.GITHUB_PROVIDER['client_id']

    @property
    def client_secret(self):
        return settings.GITHUB_PROVIDER['client_secret']

    @property
    def redirect_path(self):
        return reverse('oauth_callback', args=['github'])

    def get_authorization_headers(self, access_token):
        return {
            'Authorization': f'token {access_token}',
            'Accept': 'application/vnd.github.v3+json'
        }

    def get_authorize_params(self, request, state):
        return {
            'client_id': self.client_id,
            'redirect_uri': request.build_absolute_uri(self.redirect_path),
            'scope': 'repo',
            'state': state
        }

    def get_callback_params(self, request):
        return {
            'token_url': self.token_url,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'code': request.GET.get('code')
        }

    def get_error_message(self, response):
        return response.json().get('message')
