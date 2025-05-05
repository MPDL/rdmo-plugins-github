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

from rdmo.services.providers import OauthProviderMixin
from rdmo.core.plugins import get_plugin

logger = logging.getLogger(__name__)

APP_TYPE = settings.GITHUB_PROVIDER['app_type']

class GitHubAppProviderMixin(OauthProviderMixin):
    GITHUB_APP_NAME = settings.GITHUB_PROVIDER['github_app_name']
    install_url = f'https://github.com/apps/{GITHUB_APP_NAME}/installations/new'

    def get_install_params(self, state):
        return {
            'client_id': self.client_id,
            'state': state
        }
    
    def get_app_config_url(self, request, installation_id):        
        if installation_id is None: return

        # get random state and store in session
        state = self.get_state(request)
        url = f'https://github.com/settings/installations/{installation_id}' + '?' + urlencode({'state': state})

        return url
    
    def get_app_install_url(self, request):
        # get random state and store in session
        state = self.get_state(request)
        url = self.install_url + '?' + urlencode(self.get_install_params(state))

        return url
    
    
class GitHubProviderMixin(GitHubAppProviderMixin if APP_TYPE == "github_app" else OauthProviderMixin):
    authorize_url = 'https://github.com/login/oauth/authorize'
    token_url = 'https://github.com/login/oauth/access_token'
    api_url = 'https://api.github.com'

    PROVIDER_TYPES = [
        'PROJECT_ISSUE_PROVIDERS',
        'PROJECT_EXPORTS',
        'PROJECT_IMPORTS'
    ]

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
    
    def process_app_context(self, request, *args, **kwargs):
        # pop state from all github providers
        for provider_type in self.PROVIDER_TYPES:
            provider = get_plugin(provider_type, 'github')
            if provider:
                provider.pop_from_session(request, 'state')

        # save values in session
        for k,v in kwargs.items():
            self.store_in_session(request, k, v)
    
    def get_state(self, request):
        state = get_random_string(length=32)
        self.store_in_session(request, 'state', state)
        return state
    
    def get_app_authorize_url(self, request):
        # get random state and store in session
        state = self.get_state(request)
        url = self.authorize_url + '?' + urlencode(self.get_authorize_params(request, state))

        return url
    
    def authorize(self, request):
        installation_id = self.get_from_session(request, 'installation_id')
        if APP_TYPE == 'github_app' and installation_id is None:
            url = self.get_app_install_url(request)
        else:
            url = self.get_app_authorize_url(request)

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
        if APP_TYPE == 'github_app' and installation_id is None:
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

        # After requesting new access_token or after github app installation or update
        redirect_url = self.pop_from_session(request, 'redirect_url')
        if redirect_url is not None:
            return HttpResponseRedirect(redirect_url)
        
        try:
            method, url, kwargs = self.pop_from_session(request, 'request')
            return self.make_request(request, method, url, **kwargs)
        except ValueError:
            pass

        return render(request, 'core/error.html', {
            'title': _('GitHub callback error'),
            'errors': [_('No redirect could be found.')]
        }, status=200)
    
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
            return

        response_data = response.json()

        # store new access token in session
        access_token = response_data.get('access_token')
        self.store_in_session(request, 'access_token', access_token)
        self.store_in_session(request, 'refresh_token', response_data.get('refresh_token', None))

        return access_token
    
    def get_repo_choices(self, access_token, installation_id, minimum_repo_permission):
        if access_token is None: return []

        if APP_TYPE == 'github_app':
            url = '{api_url}/user/installations/{installation_id}/repositories?per_page={per_page}'.format(
                    api_url=self.api_url,
                    installation_id=installation_id,
                    per_page=10
                )
        else:
            url = '{api_url}/user/repos?per_page={per_page}&sort={sort}'.format(
                    api_url=self.api_url,
                    per_page=10,
                    sort='updated'
                )
            # print(f'url: {url}')
        
        response = requests.get(url, headers=self.get_authorization_headers(access_token=access_token))
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            # logger.error('error requesting github app repo list: %s (%s)', response.content, response.status_code)
            logger.error('error requesting github repo list: %s (%s)', response.content, response.status_code)
            raise e

        if APP_TYPE == 'github_app':
            # print('repo permissions: ')
            # print([{'repo': r.get('html_url'), 'p': r.get('permissions')} for r in response.json().get('repositories', [])])
            repos = [r.get('html_url') for r in response.json().get('repositories', []) if r.get('permissions', {}).get(minimum_repo_permission) == True]
        else:
            # print('repo permissions: ')
            # print([{'repo': r.get('html_url'), 'p': r.get('permissions')} for r in response.json()])
            repos = [r.get('html_url') for r in response.json() if r.get('permissions', {}).get(minimum_repo_permission) == True]

        repo_choices = [(r, r) for r in repos]
        # print(f'    repo_choices: {repo_choices}')

        return repo_choices
    
    def get_repo_form_field_data(self, request, minimum_repo_permission):
        access_token = self.validate_access_token(request, self.get_from_session(request, 'access_token'))
        installation_id = self.get_from_session(request, 'installation_id')
        repo_choices = self.get_repo_choices(access_token, installation_id, minimum_repo_permission)
        
        app_actions = {
            'authorize': {
                'url_function': self.get_app_authorize_url,
                'url_kwargs': {'request': request},
                'link_label': _('Authorize App'),
                'link_help_text': _('To connect to GitHub repositories, you first need to authorize the MPDL app.')
            },
        }
        if APP_TYPE == 'github_app':
            app_actions.update({
                'install': {
                    'url_function': self.get_app_install_url,
                    'url_kwargs': {'request': request},
                    'link_label': _('Install App'),
                    'link_help_text': _('To connect to GitHub repositories, you first need to install the MPDL app.')
                },
                'update': {
                    'url_function': self.get_app_config_url, 
                    'url_kwargs': {'request': request, 'installation_id': installation_id},
                    'link_label': _('Update list'),
                    'link_help_text': _('List of your accessible GitHub repositories (up to 10 will be shown here).')
                }
            })
        # check if app was already (installed and) authorized, otherwise update repo access
        action = 'install' if (APP_TYPE == 'github_app' and installation_id is None) else (
            'authorize' if access_token is None else (None if APP_TYPE == 'oauth_app' else 'update')
        )
        if action is None:
            repo_help_text = _("""These are your most recently updated, accessible GitHub repositories (up to 10 will be shown here). 
                To add another repository to this list, please update the repository and reload this page""")
        else:
            url_function, url_kwargs, link_label, link_help_text = app_actions[action].values()
            url = url_function(**url_kwargs)
            repo_help_text = mark_safe(f'{link_help_text} <a href="{url}">{link_label}</a>') if url is not None else ''

        return repo_choices, repo_help_text

    
    def get_form(self, request, form, *args, **kwargs):
        repo_permission_map = {
            'GitHubExportForm': 'push',
            'GitHubImportForm': 'pull'
        }
        minimum_repo_permission = repo_permission_map[form.__name__]
        repo_choices, repo_help_text = self.get_repo_form_field_data(request, minimum_repo_permission)
        return form(
                *args,
                **kwargs,
                repo_choices=repo_choices, 
                repo_help_text=repo_help_text
            )