import logging
import requests
from urllib.parse import urlencode
from requests.auth import HTTPBasicAuth

from django.conf import settings
from django.urls import reverse
from django.shortcuts import render, redirect
from django.utils.crypto import get_random_string
from django.utils.translation import gettext_lazy as _
from django.http import HttpResponseRedirect
from django.utils.safestring import mark_safe

# from rdmo.services.providers import OauthProviderMixin
from .helper_services_providers import OauthProviderMixin
from rdmo.core.plugins import get_plugin

logger = logging.getLogger(__name__)

APP_TYPE = settings.GITHUB_PROVIDER['app_type']

class GitHubAppProviderMixin(OauthProviderMixin):
    GITHUB_APP_NAME = settings.GITHUB_PROVIDER['github_app_name']
    install_url = f'https://github.com/apps/{GITHUB_APP_NAME}/installations/new'

    PROVIDER_TYPES = [
        'PROJECT_ISSUE_PROVIDERS',
        'PROJECT_EXPORTS',
        'PROJECT_IMPORTS'
    ]

    def authorize(self, request):
        # get random state and store in session
        state = self.get_state(request)
        
        installation_id = self.get_from_session(request, 'installation_id')
        if installation_id is None:
            url = self.install_url + '?' + urlencode(self.get_install_params(state))
        else:
            url = self.authorize_url + '?' + urlencode(self.get_authorize_params(request, state))

        return HttpResponseRedirect(url)
    
    def callback(self, request):
        setup_action = request.GET.get('setup_action', None)
        if setup_action != 'update' and request.GET.get('state') != self.pop_from_session(request, 'state'):
            return render(request, 'core/error.html', {
                'title': _('GitHub callback error'),
                'errors': [_('State parameter did not match.')]
            }, status=200)
        
        # store installation id of github app
        installation_id = self.get_from_session(request, 'installation_id')
        if installation_id is None:
            installation_id = request.GET.get('installation_id')
            for provider_type in self.PROVIDER_TYPES:
                provider = get_plugin(provider_type, 'github')
                if provider:
                    provider.store_in_session(request, 'installation_id', installation_id)
        
        # authorization
        access_token = self.validate_access_token(request, self.get_from_session(request, 'access_token'))
        if access_token is None:
            url = self.token_url + '?' + urlencode(self.get_callback_params(request))

            response = requests.post(url, self.get_callback_data(request),
                                    auth=self.get_callback_auth(request),
                                    headers=self.get_callback_headers(request))

            try:
                response.raise_for_status()
            except requests.HTTPError as e:
                logger.error('callback authorization error: %s (%s)', response.content, response.status_code)
                raise e

            response_data = response.json()

            access_token = response_data.get('access_token')
            self.store_in_session(request, 'access_token', access_token)
            self.store_in_session(request, 'refresh_token', response_data.get('refresh_token', None))

        # github app installation or update, i.e. not when only authorizing
        if setup_action == 'install' or setup_action == 'update':
            redirect_url = self.pop_from_session(request, 'redirect_url')
            if redirect_url is None:
                return redirect('home')
            return HttpResponseRedirect(redirect_url)
        
        # get request data from session
        stored_request = self.pop_from_session(request, 'request')
        if stored_request is None:
            redirect_url = self.pop_from_session(request, 'redirect_url')
            return HttpResponseRedirect(redirect_url)

        try:
            method, url, kwargs = stored_request
            return self.make_request(request, method, url, **kwargs)
        except ValueError:
            pass

        return render(request, 'core/error.html', {
            'title': _('GitHub callback error'),
            'errors': [_('No redirect could be found.')]
        }, status=200)

    def get_install_params(self, state):
        return {
            'client_id': self.client_id,
            'state': state
        }
    
    def get_validate_headers(self):
        return {
            'Accept': 'application/vnd.github+json'
        }
    
    def get_validate_params(self, access_token):
        return {'access_token': access_token}
    
    def get_validate_auth(self):
        return HTTPBasicAuth(self.client_id, self.client_secret)
    
    def get_refresh_token_params(self, refresh_token):
        return {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token
        }
    
    # https://docs.github.com/en/rest/apps/oauth-applications?apiVersion=2022-11-28#check-a-token
    # https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api?apiVersion=2022-11-28#using-basic-authentication
    def validate_access_token(self, request, access_token):
        if access_token is None: return

        url = '{api_url}/applications/{client_id}/token'.format(
            api_url=self.api_url,
            client_id=self.client_id
        )
        response = requests.post(
            url,
            headers=self.get_validate_headers(),
            auth=self.get_validate_auth(),
            json=self.get_validate_params(access_token))

        try:
            response.raise_for_status()
        except:
            access_token = self.refresh_access_token(request)

        return access_token
    
    def refresh_access_token(self, request):
        'Update access token with refresh_token if it exists'

        refresh_token = self.pop_from_session(request, 'refresh_token')
        if refresh_token is None: return

        url = self.token_url + '?' + urlencode(self.get_refresh_token_params(refresh_token))
        response = requests.post(url, headers=self.get_validate_headers())

        try:
            response.raise_for_status()
            response_error = response.json().get('error')
            if response_error == 'bad_refresh_token':
                self.pop_from_session(request, 'access_token')
                return
        except requests.HTTPError as e:
            logger.error('refresh token error: %s (%s)', response.content, response.status_code)
            raise e

        response_data = response.json()

        # store new access token in session
        access_token = response_data.get('access_token')
        self.store_in_session(request, 'access_token', access_token)
        self.store_in_session(request, 'refresh_token', response_data.get('refresh_token'))

        return access_token
    
    def get_state(self, request):
        state = get_random_string(length=32)
        self.store_in_session(request, 'state', state)

        return state
    
    def process_app_context(self, request, *args, **kwargs):
        # pop state from all github providers
        for provider_type in self.PROVIDER_TYPES:
            provider = get_plugin(provider_type, 'github')
            if provider:
                provider.pop_from_session(request, 'state')

        # save values in session
        for k,v in kwargs.items():
            self.store_in_session(request, k, v)
    
    def get_app_config_url(self, request, installation_id):
        # get random state and store in session
        state = self.get_state(request)
        url = f'https://github.com/settings/installations/{installation_id}' + '?' + urlencode({'state': state})

        return url
    
    def get_repo_choices(self, request, installation_id):
        url = '{api_url}/user/installations/{installation_id}/repositories?per_page={per_page}'.format(
                api_url=self.api_url,
                installation_id=installation_id,
                per_page=10
            )
        access_token = self.get_from_session(request, 'access_token')
        response = requests.get(url, headers=self.get_authorization_headers(access_token=access_token))

        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            logger.error('error requesting github app repo list: %s (%s)', response.content, response.status_code)
            raise e

        github_repos = [r.get('html_url') for r in response.json().get('repositories', [])]

        repo_choices = [(r, r) for r in github_repos]

        return repo_choices
    
    
class GitHubProviderMixin(GitHubAppProviderMixin if APP_TYPE == "github_app" else OauthProviderMixin):
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
            'Accept': 'application/vnd.github+json'
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
    
    def get_repo_form_field_data(self, request):
        if APP_TYPE == 'github_app':
            installation_id = self.get_from_session(request, 'installation_id')
            repo_choices = self.get_repo_choices(request, installation_id) if installation_id is not None else None
            app_config_url = self.get_app_config_url(request, installation_id) if installation_id is not None else None
            
            link_label = _('Update list')
            help_text = _('List of your accessible GitHub repositories (up to 10 repos will be shown here).')
            repo_help_text=mark_safe(
                f'{help_text} <a href="{app_config_url}">{link_label}</a>'
            ) if app_config_url is not None else ''

        else:
            repo_choices = None
            repo_help_text = None

        return repo_choices, repo_help_text

    
    def get_form(self, request, form, *args):
        repo_choices, repo_help_text = self.get_repo_form_field_data(request)

        return form(
                *args,
                repo_choices=repo_choices, 
                repo_help_text=repo_help_text
            )