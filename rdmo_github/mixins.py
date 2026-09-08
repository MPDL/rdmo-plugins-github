import logging
from urllib.parse import quote, urlencode

from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

import requests
from requests.auth import HTTPBasicAuth
from requests_toolbelt.multipart.encoder import MultipartEncoder

from rdmo.services.providers import OauthProviderMixin

logger = logging.getLogger(__name__)


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
        return {'Authorization': f'Bearer {access_token}', 'Accept': 'application/vnd.github+json'}

    def get_authorize_params(self, request, state):
        return {
            'client_id': self.client_id,
            'redirect_uri': request.build_absolute_uri(self.redirect_path),
            'scope': 'repo',
            'state': state,
        }

    def get_callback_params(self, request):
        return {
            'token_url': self.token_url,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'code': request.GET.get('code'),
        }

    def get_validate_headers(self):
        return {'Accept': 'application/vnd.github+json'}

    def get_refresh_token_params(self, refresh_token):
        return {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        }

    def get_error_message(self, response):
        return response.json().get('message')

    def get_request_url(self, repo, path=None, suffix=None, ref=None):
        """Create the request url for export and import choices."""

        url = '{api_url}/repos/{repo}'.format(
            api_url=self.api_url, repo=repo.replace('https://github.com/', '').strip('/')
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

    def callback(self, request):
        if request.GET.get('state') != self.pop_from_session(request, 'state'):
            return render(
                request,
                'core/error.html',
                {'title': _('OAuth authorization not successful'), 'errors': [_('State parameter did not match.')]},
                status=200,
            )

        # authorization
        access_token = self.validate_access_token(request, self.get_from_session(request, 'access_token'))
        if access_token is None:
            url = self.token_url + '?' + urlencode(self.get_callback_params(request))

            response = requests.post(
                url,
                self.get_callback_data(request),
                auth=self.get_callback_auth(request),
                headers=self.get_callback_headers(request),
            )

            try:
                response.raise_for_status()
            except requests.HTTPError as e:
                logger.error('callback authorization error: %s (%s)', response.content, response.status_code)
                raise e

            response_data = response.json()

            # store access token and refresh token in session
            self.store_in_session(request, 'access_token', response_data.get('access_token'))
            self.store_in_session(request, 'refresh_token', response_data.get('refresh_token'))

        redirect_url = self.pop_from_session(request, 'redirect_url')
        if redirect_url is not None:
            return HttpResponseRedirect(redirect_url)

        try:
            method, *args = self.pop_from_session(request, 'request')
            if method == 'get':
                return self.get(request, *args)
            elif method == 'post':
                return self.post(request, *args)
            elif method == 'put':
                return self.put(request, *args)
        except ValueError:
            pass

        return render(
            request,
            'core/error.html',
            {'title': _('OAuth authorization successful'), 'errors': [_('But no redirect could be found.')]},
            status=200,
        )

    def put(self, request, url, json=None, files=None, multipart=None):
        # get access token from the session
        access_token = self.validate_access_token(request, self.get_from_session(request, 'access_token'))
        if access_token:
            # if the access_token is available put to the upstream service
            logger.debug('put: %s %s %s', url, json, files)

            if multipart is not None:
                multipart_encoder = MultipartEncoder(fields=multipart)
                headers = self.get_authorization_headers(access_token)
                headers['Content-Type'] = multipart_encoder.content_type
                response = requests.put(url, data=multipart_encoder, headers=headers)
            elif files is not None:
                response = requests.put(url, files=files, headers=self.get_authorization_headers(access_token))
            else:
                response = requests.put(url, json=json, headers=self.get_authorization_headers(access_token))

            if response.status_code == 401:
                logger.warning('put forbidden: %s (%s)', response.content, response.status_code)
            else:
                try:
                    response.raise_for_status()
                    return self.put_success(request, response)

                except requests.HTTPError:
                    logger.warning('put error: %s (%s)', response.content, response.status_code)

                    return render(
                        request,
                        'core/error.html',
                        {
                            'title': _('OAuth error'),
                            'errors': [_('Something went wrong: %s') % self.get_error_message(response)],
                        },
                        status=200,
                    )

        # if the above did not work authorize first
        self.store_in_session(request, 'request', ('put', url, json, files, multipart))
        return self.authorize(request)

    def put_success(self, request, response):
        raise NotImplementedError

    def validate_access_token(self, request, access_token):
        # https://docs.github.com/en/rest/apps/oauth-applications?apiVersion=2022-11-28#check-a-token
        # https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api?apiVersion=2022-11-28#using-basic-authentication

        if access_token is None:
            return None

        url = f'{self.api_url}/applications/{self.client_id}/token'
        response = requests.post(
            url,
            headers=self.get_validate_headers(),
            auth=HTTPBasicAuth(self.client_id, self.client_secret),
            json={'access_token': access_token},
        )

        try:
            response.raise_for_status()
        except requests.HTTPError:
            access_token = self.refresh_access_token(request)

        return access_token

    def refresh_access_token(self, request):
        "Update access token with refresh_token if it exists"

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

    def get_repo_choices(self, access_token, minimum_repo_permission, per_page=10):
        if access_token is None:
            return []

        # https://docs.github.com/en/rest/repos/repos?apiVersion=2026-03-10#list-repositories-for-the-authenticated-user
        url = '{api_url}/user/repos?per_page={per_page}&sort={sort}'.format(
            api_url=self.api_url,
            per_page=per_page,
            sort='updated',
        )

        response = requests.get(url, headers=self.get_authorization_headers(access_token=access_token))
        try:
            response.raise_for_status()
        except requests.HTTPError:
            logger.error('Error requesting GitHub repo list: %s (%s)', response.content, response.status_code)
            return []

        repos = [r.get('html_url') for r in response.json() if r.get('permissions', {}).get(minimum_repo_permission)]

        repo_choices = [(r, r) for r in repos]

        return repo_choices

    def get_repo_form_field_data(self, access_token, minimum_repo_permission):
        repo_choices = self.get_repo_choices(access_token, minimum_repo_permission)

        if len(repo_choices) == 0:
            repo_help_text = _('You do not have any GitHub repositories yet.')
        else:
            repo_help_text = _('These are your most recently updated, accessible GitHub repositories.')

        return repo_choices, repo_help_text
