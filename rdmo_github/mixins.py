import logging
from urllib.parse import parse_qs, quote, urlencode, urlparse

from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

import requests
from requests.auth import HTTPBasicAuth
from requests_toolbelt.multipart.encoder import MultipartEncoder

from rdmo.core.plugins import get_plugin
from rdmo.services.providers import OauthProviderMixin

logger = logging.getLogger(__name__)

APP_TYPE = settings.GITHUB_PROVIDER['app_type']

class GitHubProviderMixin(OauthProviderMixin):
    install_url = f"https://github.com/apps/{settings.GITHUB_PROVIDER['github_app_name']}/installations/new"
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

    def get_install_params(self, state):
        return {
            'client_id': self.client_id,
            'state': state
        }

    def get_authorization_headers(self, access_token):
        return {
            'Authorization': f'Bearer {access_token}',
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

    def get_validate_headers(self):
        return {
            'Accept': 'application/vnd.github+json'
        }

    def get_validate_params(self, access_token):
        return {'access_token': access_token}

    def get_refresh_token_params(self, refresh_token):
        return {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token
        }

    def get_error_message(self, response):
        return response.json().get('message')

    def get_state(self, request):
        state = get_random_string(length=32)
        self.store_in_session(request, 'state', state)
        return state

    def get_app_config_url(self, request, installation_id):
        if installation_id is None:
            return None

        # get random state and store in session
        state = self.get_state(request)
        url = f'https://github.com/settings/installations/{installation_id}' + '?' + urlencode({'state': state})

        return url

    def get_app_install_url(self, request):
        # get random state and store in session
        state = self.get_state(request)
        url = self.install_url + '?' + urlencode(self.get_install_params(state))

        return url

    def get_app_authorize_url(self, request):
        # get random state and store in session
        state = self.get_state(request)
        url = self.authorize_url + '?' + urlencode(self.get_authorize_params(request, state))

        return url

    def get_request_url(self, repo, path=None, suffix=None, ref=None):
        url = '{api_url}/repos/{repo}'.format(
            api_url=self.api_url,
            repo=repo.replace('https://github.com/', '').strip('/')
        )

        if path:
            url += '/contents/{path}'.format(
                path=quote(path.removeprefix('../').removeprefix('./').strip('/'), safe='')
            )

        if suffix:
            url += suffix

        if ref:
            url += '?ref={ref}'.format(ref=quote(ref, safe=''))

        return url

    def process_app_context(self, request, *args, **kwargs):
        # pop state from all github providers
        for provider_type in self.PROVIDER_TYPES:
            provider = get_plugin(provider_type, 'github')
            if provider:
                provider.pop_from_session(request, 'state')

        # save values in session
        for k,v in kwargs.items():
            self.store_in_session(request, k, v)

    def make_request(self, request, method, url, apply_data_processing=False, *args, **kwargs):
        methods = {
            'get': {'request_method': requests.get, 'success_method': self.get_success},
            'post': {'request_method': requests.post, 'success_method': self.post_success},
            'put': {'request_method': requests.put, 'success_method': self.put_success}
        }
        if method not in methods.keys():
            raise ValueError(f"Unsupported request method: {method}")

        access_token = self.get_from_session(request, 'access_token')
        if access_token:
            # if the access_token is available make request to the upstream service
            logger.debug('%s: %s', method, url)

            request_method, success_method = methods[method].values()

            data_processing_params = {}
            if 'data_processing_params' in kwargs.keys():
                data_processing_params = kwargs['data_processing_params']

            headers = self.get_authorization_headers(access_token)
            if 'multipart' in kwargs.keys():
                multipart = kwargs['multipart']
                if apply_data_processing:
                    processed_multipart = self.process_request_data(
                        {**multipart},
                        access_token=access_token,
                        **data_processing_params
                    )
                    multipart_encoder = MultipartEncoder(fields=processed_multipart)
                else:
                    multipart_encoder = MultipartEncoder(fields=multipart)

                headers['Content-Type'] = multipart_encoder.content_type
                response = request_method(url, data=multipart_encoder, headers=headers)
            elif 'files' in kwargs.keys():
                files = kwargs['files']
                if apply_data_processing:
                    processed_files = self.process_request_data(
                        {**files},
                        access_token=access_token,
                        **data_processing_params
                    )
                    response = request_method(url, files=processed_files, headers=headers)
                else:
                    response = request_method(url, files=files, headers=headers)
            elif 'json' in kwargs.keys():
                json = kwargs['json']
                if apply_data_processing:
                    processed_json = self.process_request_data(
                        {**json},
                        access_token=access_token,
                        **data_processing_params
                    )
                    response = request_method(url, json=processed_json, headers=headers)
                else:
                    response = request_method(url, json=json, headers=headers)
            else:
                response = request_method(url, headers=headers)

            if response.status_code == 401:
                logger.warning('%s forbidden: %s (%s)', method, response.content, response.status_code)
            else:
                try:
                    response.raise_for_status()
                    return success_method(request, response)

                except requests.HTTPError:
                    logger.warning('%s error: %s (%s)', method, response.content, response.status_code)

                    return render(request, 'core/error.html', {
                        'title': _('Request (export or import) error'),
                        'errors': [_('Something went wrong: %s') % self.get_error_message(response)]
                    }, status=200)

        # if the above did not work authorize first
        kwargs['apply_data_processing'] = apply_data_processing
        self.store_in_session(request, 'request', (method, url, kwargs))
        return self.authorize(request)

    def put_success(self, request, response):
        raise NotImplementedError

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

    def get_validate_auth(self):
        return HTTPBasicAuth(self.client_id, self.client_secret)

    def validate_access_token(self, request, access_token):
        # https://docs.github.com/en/rest/apps/oauth-applications?apiVersion=2022-11-28#check-a-token
        # https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api?apiVersion=2022-11-28#using-basic-authentication

        if access_token is None:
            return None

        url = f'{self.api_url}/applications/{self.client_id}/token'
        response = requests.post(
            url,
            headers=self.get_validate_headers(),
            auth=self.get_validate_auth(),
            json=self.get_validate_params(access_token))

        try:
            response.raise_for_status()
        except requests.HTTPError:
            access_token = self.refresh_access_token(request)

        return access_token

    def refresh_access_token(self, request):
        'Update access token with refresh_token if it exists'

        refresh_token = self.pop_from_session(request, 'refresh_token')
        if refresh_token is None:
            return None

        url = self.token_url + '?' + urlencode(self.get_refresh_token_params(refresh_token))
        response = requests.post(url, headers=self.get_validate_headers())

        try:
            response.raise_for_status()
            response_error = response.json().get('error')
            if response_error == 'bad_refresh_token':
                self.pop_from_session(request, 'access_token')
                return
        except requests.HTTPError:
            logger.error('GitHub refresh token error: %s (%s)', response.content, response.status_code)
            return

        response_data = response.json()

        # store new access token in session
        access_token = response_data.get('access_token')
        self.store_in_session(request, 'access_token', access_token)
        self.store_in_session(request, 'refresh_token', response_data.get('refresh_token'))

        return access_token

    def get_repo_choices(self, request, access_token, installation_id, minimum_repo_permission, page, per_page=10):
        if access_token is None:
            return [], False

        stored_repo_choices = self.get_from_session(request, 'github_repo_choices')
        more_repos_available = self.get_from_session(request, 'github_more_repos_available')
        more_repos_available = more_repos_available if more_repos_available is not None else True

        if stored_repo_choices and not more_repos_available:
            return stored_repo_choices, more_repos_available

        if APP_TYPE == 'github_app':
            # https://docs.github.com/de/rest/apps/installations?apiVersion=2022-11-28#list-repositories-accessible-to-the-user-access-token
            url = f'{self.api_url}/user/installations/{installation_id}/repositories?per_page={per_page}'
        else:
            # https://docs.github.com/en/rest/repos/repos?apiVersion=2026-03-10#list-repositories-for-the-authenticated-user
            url = '{api_url}/user/repos?per_page={per_page}&page={page}&sort={sort}'.format(
                    api_url=self.api_url,
                    per_page=per_page,
                    page=page,
                    sort='updated'
                )

        response = requests.get(url, headers=self.get_authorization_headers(access_token=access_token))
        try:
            response.raise_for_status()
        except requests.HTTPError:
            logger.error('Error requesting GitHub repo list: %s (%s)', response.content, response.status_code)
            return [], False

        if APP_TYPE == 'github_app':
            repos = [
                r.get('html_url') for r in response.json().get('repositories', [])
                if r.get('permissions', {}).get(minimum_repo_permission)
            ]
            total_repo_count = response.json().get('total_count')
        else:
            repos = [
                r.get('html_url') for r in response.json()
                if r.get('permissions', {}).get(minimum_repo_permission)
            ]
            header_last_link = next(
                (
                    link.removesuffix('>; rel="last"').removeprefix('<')
                    for link in response.headers.get('Link', '').split(', ')
                    if link.endswith('; rel="last"')
                ),
                None
            )
            parsed = urlparse(header_last_link)
            query_params = parse_qs(parsed.query)
            last_page = int(query_params.get('page')[0]) if query_params.get('page') else 0
            total_repo_count = last_page*per_page # max total_repo_count

        repo_choices = [(r, r) for r in repos]

        if stored_repo_choices:
            repo_choices = stored_repo_choices + repo_choices

        more_repos_available = total_repo_count > page*per_page

        if APP_TYPE == 'oauth_app':
            self.store_in_session(request, 'github_more_repos_available', more_repos_available)
            self.store_in_session(request, 'github_repo_choices', repo_choices)
            self.store_in_session(request, 'github_repos_page', page)

        return repo_choices, more_repos_available

    def get_repo_form_field_data(self, request, minimum_repo_permission):
        access_token = self.validate_access_token(request, self.get_from_session(request, 'access_token'))
        installation_id = self.get_from_session(request, 'installation_id')

        repos_page = self.pop_from_session(request, 'github_repos_page')
        next_repos_page = repos_page + 1 if repos_page else 1
        repo_choices, more_repos_available = self.get_repo_choices(
            request,
            access_token,
            installation_id,
            minimum_repo_permission,
            next_repos_page
        )

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

        if len(repo_choices) == 0:
            repo_help_text = _('You do not have any GitHub repositories yet')

        elif action is None:
            more_repos_link_text = _('To add more repositories to this list, click')
            link_label = _('here')
            more_repos_link = (
                f' {more_repos_link_text} <a href="{self.request.build_absolute_uri()}" >{link_label}</a>.'
                if more_repos_available
                else ''
            )
            help_text = _('These are your most recently updated, accessible GitHub repositories.')
            repo_help_text = mark_safe(f'{help_text} {more_repos_link}')

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
