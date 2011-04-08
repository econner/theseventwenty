'''
Created on Apr 7, 2011

@author: dinkydogg
'''

import functools
import tornadorpc.json
import model
import re
import tornado

import handlers


class MustOwnPlaylistException(Exception): pass


class InvalidParameterException(Exception):
    def __init__(self, errors):
        self.errors = errors


def owns_playlist(method):
    """ Decorator: throws an exception if user doesn't own current playlist
    
    NOTE: playlist_id must be the 1st positional arg. If you put some other
    value as the 1st positional arg it could be a security issue (as this 
    would check that the user owns the wrong playlist ID.) So make sure 
    playlist_id is the first positional arg.
    
    I need to read more about python to figure out if there's a better way to
    do this.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        playlist_id = (kwargs['playlist_id']
                       if 'playlist_id' in kwargs
                       else args[0])
        playlist = self.db_session.query(model.Playlist).get(playlist_id)
        if not self.owns_playlist(playlist):
            raise MustOwnPlaylistException()
        return method(self, *args, **kwargs)
    return wrapper


def sends_validation_results(method, async=False):
    """ Wraps a method so that it will return a dictionary with attributes indicating validation success or failure with errors or results.
    
    This function is a horrible hack, but the results are actually quite nice. It is intended for use as a decorator on RPC methods in a JSON RPC handler. It overrides the handler's result method in order to rap the results, and catches any exceptions thrown by a validator in order to return error messages to the client. Useful for forms. """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        original_result_func = self.result
        def result_with_success(result):
            if result.__class__ is not dict or "success" not in result:
                result = {"success": True, "result": result}
            original_result_func(result)
        self.result = result_with_success

        self.validator = Validator()
        try:
            result = method(self, *args, **kwargs)
            return result
        except InvalidParameterException as e:
            result = {
                 "success": False,
                 "errors": e.errors
            }
            if async:
                self.result(result)
            return result
    return wrapper


class Validator(object):
    def __init__(self, immediate_exceptions=False):
        self._immediate_exceptions = immediate_exceptions
        self.errors = {}


    def has_errors(self):
        return len(self.errors) > 0

    def validate(self):
        if self.has_errors():
            raise InvalidParameterException(self.errors)

    def error(self, message, name=''):
        self.errors[name] = message
        if self._immediate_exceptions:
            raise InvalidParameterException(self.errors)

    def add_rule(self, value, name='', type=None, min_length=None, max_length=None, email=None):
        if type is not None:
            self._check_type(value, name, type)
        if email is not None:
            self._check_email(value, name)

    def _check_type(self, value, name, type):
        if value.__class__ is not type:
            self.error("Type must be " + str(type), name)
            return False
        return True

    def _check_email(self, value, name):
        email_regex = re.compile('^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,4}$')
        if None == email_regex.match(value):
            self.error("Must be a valid email.", name)
            return False
        return True


class JsonRpcHandler(tornadorpc.json.JSONRPCHandler, handlers.PlaylistHandlerBase,
                     handlers.UserHandlerBase, handlers.ImageHandlerBase):

    @sends_validation_results
    def validation_test(self, str):
        validator = Validator()
        validator.add_rules(str, 'str', email=True)
        validator.validate()
        return str

    @owns_playlist
    def update_songlist(self, playlist_id, songs):
        self.db_session.query(model.Playlist).get(playlist_id).songs = songs
        self.db_session.commit()

    @owns_playlist
    def update_title(self, playlist_id, title):
        self.db_session.query(model.Playlist).get(playlist_id).title = title
        self.db_session.commit()

    @owns_playlist
    def update_description(self, playlist_id, description):
        self.db_session.query(model.Playlist).get(playlist_id).description = description
        self.db_session.commit()

    def is_registered_fbid(self, fb_id):
        """ Wraps the inherited function so it can be called via RPC """
        return self._is_registered_fbid(fb_id)

    @tornadorpc.async
    @owns_playlist
    def set_image_from_url(self, playlist_id, url):
        self.playlist_id = playlist_id
        http = tornado.httpclient.AsyncHTTPClient()
        http.fetch(url, callback=self._on_set_image_from_url_response)

    def _on_set_image_from_url_response(self, response):
        result = self._handle_image(response.buffer, self.playlist_id)
        self.result(result)

    @sends_validation_results
    def login(self, email, password, remember_me):
        email = email.strip()
        validator = Validator(immediate_exceptions=True)
        validator.add_rule(email, 'Email', email=True)

        user = self.db_session.query(model.User).filter_by(email=email).first()
        if not user:
            validator.error('No user with that email found.', 'email')

        if not self._verify_password(password, user.password):
            validator.error('Incorrect password.', 'password')

        # If we haven't failed out yet, the login is valid.
        self._log_user_in(user, expire_on_browser_close=(not remember_me))
