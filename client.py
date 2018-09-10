##########################################################################
# Copyright 2016 Curity AB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##########################################################################
import hashlib
import json
import os
import urllib
import urllib2

import tools

REGISTERED_CLIENT_FILENAME = 'registered_client.json'


class Client:
    def __init__(self, config):
        self.config = config

        print 'Getting ssl context for oauth server'
        self.ctx = tools.get_ssl_context(self.config)
        self.__init_config()
        self.client_data = None

    def __init_config(self):

        if 'issuer' in self.config:
            meta_data_url = self.config['issuer'] + '/.well-known/openid-configuration'
            print 'Fetching config from: %s' % meta_data_url
            meta_data = urllib2.urlopen(meta_data_url, context=self.ctx)
            if meta_data:
                self.config.update(json.load(meta_data))
            else:
                print 'Unexpected response on discovery document: %s' % meta_data
        else:
            print 'Found no issuer in config, can not perform discovery. All endpoint config needs to be set manually'

        # Mandatory settings
        if 'authorization_endpoint' not in self.config:
            raise Exception('authorization_endpoint not set.')
        if 'token_endpoint' not in self.config:
            raise Exception('token_endpoint not set.')

        self.read_credentials_from_file()
        if 'client_id' not in self.config:
            print 'Client is not registered.'

        if 'scope' not in self.config:
            self.config['scope'] = 'openid'

    def read_credentials_from_file(self):
        if not os.path.isfile(REGISTERED_CLIENT_FILENAME):
            print 'Client is not registered'
            return

        try:
            registered_client = json.loads(open(REGISTERED_CLIENT_FILENAME).read())
        except Exception as e:
            print 'Could not read credentials from file', e
            return
        self.config['client_id'] = registered_client['client_id']
        self.config['client_secret'] = registered_client['client_secret']
        self.config['redirect_uri'] = registered_client['redirect_uris'][0]
        self.client_data = registered_client

    def register(self):
        """
        Register a client at the AS
        :raises: raises error when http call fails
        """
        if 'registration_endpoint' not in self.config:
            print 'Authorization server does not support Dynamic Client Registration. Please configure client ' \
                  'credentials manually '
            return

        if 'client_id' in self.config:
            raise Exception('Client is already registered')

        dcr_access_token = None

        if 'dcr_client_id' in self.config and "dcr_client_secret" in self.config:
            # DCR endpoint requires an access token, so perform CC flow and get one
            dcr_access_token = self.get_registration_token()

        if 'template_client' in self.config:
            print 'Registering client using template_client: %s' % self.config['template_client']
            data = {
                'software_id': self.config['template_client']
            }
        else:
            data = {
                'client_name': 'OpenID Connect Demo',
                "grant_types": ["implicit", "authorization_code", "refresh_token"],
                'redirect_uris': [self.config['base_url'] + "/callback"]
            }
            if self.config['debug']:
                print 'Registering client with data:\n %s' % json.dumps(data)

        register_response = self.__urlopen(self.config['registration_endpoint'], data=json.dumps(data),
                                           context=self.ctx, token=dcr_access_token)
        self.client_data = json.loads(register_response.read())

        with open(REGISTERED_CLIENT_FILENAME, 'w') as outfile:
            outfile.write(json.dumps(self.client_data))

        if self.config['debug']:
            tools.print_json(self.client_data)

        self.read_credentials_from_file()

    def clean_registration(self, config):
        """
        Removes the registration file and reloads config
        :return:
        """
        os.remove(REGISTERED_CLIENT_FILENAME)
        config.pop('client_id', None)
        config.pop('client_secret', None)
        self.client_data = None
        self.config = config

    def revoke(self, token, token_type_hint="access_token"):
        """
        Revoke the token
        :param token: the token to revoke
        :param token_type_hint: a hint to the OAuth server about the kind of token being revoked
        :raises: raises error when http call fails
        """
        if 'revocation_endpoint' not in self.config:
            print 'No revocation endpoint set'
            return

        data = {
            'token': token,
            "token_type_hint": token_type_hint,
            'client_id': self.config['client_id'],
            'client_secret': self.config['client_secret']
        }

        self.__urlopen(self.config['revocation_endpoint'], urllib.urlencode(data), context=self.ctx)

    def refresh(self, refresh_token):
        """
        Refresh the access token with the refresh_token
        :param refresh_token:
        :return: the new access token
        """
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'client_id': self.config['client_id'],
            'client_secret': self.config['client_secret']
        }
        token_response = self.__urlopen(self.config['token_endpoint'], urllib.urlencode(data), context=self.ctx)
        return json.loads(token_response.read())

    def get_authn_req_url(self, session, acr, forceAuthN, scope, forceConsent, allowConsentOptionDeselection,
                          response_type):
        """
        :param session: the session, will be used to keep the OAuth state
        :param acr: The acr to request
        :param force_authn: Force the resource owner to authenticate even though a session exist
        :return redirect url for the OAuth code flow
        """
        state = tools.generate_random_string()
        session['state'] = state
        session['code_verifier'] = code_verifier = tools.generate_random_string(100)
        session["flow"] = response_type

        code_challenge = tools.base64_urlencode(hashlib.sha256(code_verifier).digest())

        request_args = {'scope': scope,
                        'response_type': response_type,
                        'client_id': self.config['client_id'],
                        'state': state,
                        'code_challenge': code_challenge,
                        'code_challenge_method': "S256",
                        'redirect_uri': self.config['redirect_uri']}

        if 'authn_parameters' in self.config:
            request_args.update(self.config['authn_parameters'])

        if acr: request_args["acr_values"] = acr

        if forceAuthN: request_args["prompt"] = "login"

        if forceConsent:
            if allowConsentOptionDeselection:
                request_args["prompt"] = request_args.get("prompt", "") + " consent consent_allow_deselection"
            else:
                request_args["prompt"] = request_args.get("prompt", "") + " consent"

        if response_type.find("id_token"):
            request_args["nonce"] = session["nonce"] = tools.generate_random_string()

        delimiter = "?" if self.config['authorization_endpoint'].find("?") < 0 else "&"
        login_url = "%s%s%s" % (self.config['authorization_endpoint'], delimiter, urllib.urlencode(request_args))

        print "Redirect to %s" % login_url

        return login_url

    def get_token(self, code, code_verifier):
        """
        :param code: The authorization code to use when getting tokens
        :return the json response containing the tokens
        """
        data = {'client_id': self.config['client_id'], "client_secret": self.config['client_secret'],
                'code': code,
                "code_verifier": code_verifier,
                'redirect_uri': self.config['redirect_uri'],
                'grant_type': 'authorization_code'}

        # Exchange code for tokens
        try:
            token_response = self.__urlopen(self.config['token_endpoint'], urllib.urlencode(data), context=self.ctx)
        except urllib2.URLError as te:
            print "Could not exchange code for tokens"
            raise te
        return json.loads(token_response.read())

    def get_client_data(self):
        if not self.client_data:
            self.read_credentials_from_file()

        if self.client_data:
            masked = self.client_data
            masked['client_secret'] = '***********************************'
            return masked

    def get_registration_token(self):

        if 'dcr_client_id' not in self.config:
            raise Exception('Can not run client registration. Missing client id.')

        if 'dcr_client_secret' not in self.config:
            raise Exception('Can not run client registration. Missing client secret.')

        data = {
            'client_id': self.config['dcr_client_id'],
            'client_secret': self.config['dcr_client_secret'],
            'grant_type': 'client_credentials',
            'scope': 'dcr'
        }

        try:
            token_response = self.__urlopen(self.config['token_endpoint'], urllib.urlencode(data), context=self.ctx)
        except urllib2.URLError as te:
            print "Could not get DCR access token"
            raise te

        json_response = json.loads(token_response.read())

        return json_response['access_token']

    def __urlopen(self, url, data=None, context=None, token=None):
        """
        Open a connection to the specified url. Sets valid requests headers.
        :param url: url to open - cannot be a request object 
        :param data: data to send, optional
        :param context: ssl context
        :param token: token to add to the authorization header
        :return the request response
        """
        headers = {
            'User-Agent': 'CurityExample/1.0',
            'Accept': 'application/json,text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,'
                      '*/*;q=0.8 '
        }
        if token:
            headers['Authorization'] = 'Bearer %s' % token

        request = urllib2.Request(url, data, headers)
        return urllib2.urlopen(request, context=context)

    def __authn_req_args(self, state, scope, code_challenge, code_challenge_method="plain"):
        """
        :param state: state to send to authorization server
        :return a map of arguments to be sent to the authz endpoint
        """
        if 'client_id' not in self.config:
            raise Exception('Client is not registered')

        args = {'scope': scope,
                'response_type': 'code',
                'client_id': self.config['client_id'],
                'state': state,
                'code_challenge': code_challenge,
                'code_challenge_method': code_challenge_method,
                'redirect_uri': self.config['redirect_uri']}

        if 'authn_parameters' in self.config:
            args.update(self.config['authn_parameters'])
        return args