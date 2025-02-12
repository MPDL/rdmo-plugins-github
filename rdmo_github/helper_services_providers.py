import logging
from urllib.parse import urlencode

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.crypto import get_random_string
from django.utils.translation import gettext_lazy as _

import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder

logger = logging.getLogger(__name__)


class OauthProviderMixin:

    def make_request(self, request, method, url, *args, **kwargs):
        methods = {
            'get': {'request_method': requests.get, 'success_method': self.get_success},
            'post': {'request_method': requests.post, 'success_method': self.post_success},
            'put': {'request_method': requests.put, 'success_method': self.put_success}
        }

        access_token = self.get_from_session(request, 'access_token')
        if access_token:
            # if the access_token is available make request to the upstream service
            logger.debug('%s: %s', method, url)

            request_method = methods[method]['request_method']

            data_processing_method = None
            if 'data_processing_params' in kwargs.keys():
                data_processing_method = self.process_request_data
                data_processing_params = kwargs['data_processing_params']
            
            if 'multipart' in kwargs.keys():
                multipart = kwargs['multipart']
                multipart_encoder = MultipartEncoder(fields=multipart)
                headers = self.get_authorization_headers(access_token)
                headers['Content-Type'] = multipart_encoder.content_type
                response = request_method(url, data=multipart_encoder, headers=headers)
            elif 'files' in kwargs.keys():
                files = kwargs['files']
                response = request_method(url, files=files, headers=self.get_authorization_headers(access_token))
            elif 'json' in kwargs.keys():
                json = kwargs['json']

                if data_processing_method:
                    processed_json = data_processing_method({**json}, **data_processing_params, access_token=access_token)
                    response = request_method(url, json=processed_json, headers=self.get_authorization_headers(access_token))
                else:
                    response = request_method(url, json=json, headers=self.get_authorization_headers(access_token))
            else:
                print(f"url: {url}")
                response = request_method(url, headers=self.get_authorization_headers(access_token))

            if response.status_code == 401:
                logger.warning('%s forbidden: %s (%s)', method, response.content, response.status_code)
            else:
                try:
                    response.raise_for_status()
                    success_method = methods[method]['success_method']
                    return success_method(request, response)

                except requests.HTTPError:
                    logger.warning('%s error: %s (%s)', method, response.content, response.status_code)

                    return render(request, 'core/error.html', {
                        'title': _('OAuth error'),
                        'errors': [_('Something went wrong: %s') % self.get_error_message(response)]
                    }, status=200)

        # if the above did not work authorize first
        self.store_in_session(request, 'request', (method, url, kwargs))
        return self.authorize(request)

    def authorize(self, request):
        # get random state and store in session
        state = get_random_string(length=32)
        self.store_in_session(request, 'state', state)

        url = self.authorize_url + '?' + urlencode(self.get_authorize_params(request, state))
        return HttpResponseRedirect(url)

    def callback(self, request):
        if request.GET.get('state') != self.pop_from_session(request, 'state'):
            return render(request, 'core/error.html', {
                'title': _('OAuth authorization not successful'),
                'errors': [_('State parameter did not match.')]
            }, status=200)

        url = self.token_url + '?' + urlencode(self.get_callback_params(request))

        response = requests.post(url, self.get_callback_data(request),
                                 auth=self.get_callback_auth(request),
                                 headers=self.get_callback_headers(request))

        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            logger.error('callback error: %s (%s)', response.content, response.status_code)
            raise e

        response_data = response.json()

        # store access token in session
        self.store_in_session(request, 'access_token', response_data.get('access_token'))

        # get request data from session
        try:
            method, url, kwargs = self.pop_from_session(request, 'request')
            return self.make_request(request, method, url, **kwargs)
        except ValueError:
            pass

        return render(request, 'core/error.html', {
            'title': _('OAuth authorization successful'),
            'errors': [_('But no redirect could be found.')]
        }, status=200)

    def get_success(self, request, response):
        raise NotImplementedError

    def post_success(self, request, response):
        raise NotImplementedError
    
    def put_success(self, request, response):
        raise NotImplementedError
    
    def process_request_data(self, data, access_token, *args, **kwargs):
        raise NotImplementedError

    def get_session_key(self, key):
        return f'{self.class_name}.{key}'

    def store_in_session(self, request, key, data):
        session_key = self.get_session_key(key)
        request.session[session_key] = data

    def get_from_session(self, request, key):
        session_key = self.get_session_key(key)
        return request.session.get(session_key, None)

    def pop_from_session(self, request, key):
        session_key = self.get_session_key(key)
        return request.session.pop(session_key, None)

    def get_authorization_headers(self, access_token):
        return {'Authorization': f'Bearer {access_token}'}

    def get_authorize_params(self, request, state):
        raise NotImplementedError

    def get_callback_auth(self, request):
        return None
    
    def get_request_auth(self, request):
        return None

    def get_callback_headers(self, request):
        return {'Accept': 'application/json'}

    def get_callback_params(self, request):
        return {}

    def get_callback_data(self, request):
        return {}

    def get_error_message(self, response):
        return response.json().get('error')
