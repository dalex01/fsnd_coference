#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'

import datetime
import json
import os
import time

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import urlfetch
from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb
from google.appengine.ext.db import polymodel

from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import TeeShirtSize
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import Session
from models import SessionForm
from models import SessionForms
from models import SessionTypes
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import BooleanMessage
from models import ConflictException
from models import StringMessage
from models import Speaker
from models import SpeakerForm

from settings import WEB_CLIENT_ID
from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
MEMCACHE_SPEAKER_KEY = "FEATURED_SPEAKER"
DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}
DEFAULTS_SESSION = {
    "duration": 60,
    "typeOfSession": 1
}
OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }
CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)
SPEAKER_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speakerKey=messages.StringField(1),
)
SESSION_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    sessionKey=messages.StringField(1),
)
SESSION_TYPE_GET_REQUEST = endpoints.ResourceContainer(
    sessionType=messages.StringField(1),
    websafeConferenceKey=messages.StringField(2),
)
SESSION_SPEAKER_GET_REQUEST = endpoints.ResourceContainer(
    speaker=messages.StringField(1),
    websafeConferenceKey=messages.StringField(2),
)
CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)
SESSION_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

@endpoints.api( name='conference',
                version='v1',
                allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
                scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""


    ######################################
    # Conference
    ######################################

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        # both for data model & outbound Message
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
            setattr(request, "seatsAvailable", data["maxAttendees"])

        # make Profile Key from user ID
        p_key = ndb.Key(Profile, user_id)
        # allocate new Conference ID with Profile key as parent
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        # make Conference key from ID
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )

        return request

    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm,
                path='conference',
                http_method='POST',
                name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                path='getConferencesCreated',
                http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # make profile key
        p_key = ndb.Key(Profile, getUserId(user))
        # create ancestor query for this user
        conferences = Conference.query(ancestor=p_key)
        # get the user profile and display name
        prof = p_key.get()
        displayName = getattr(prof, 'displayName')
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, displayName) for conf in conferences]
        )

    ######################################
    # Filter Conference
    ######################################

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                path='filterPlayground',
                http_method='GET',
                name='filterPlayground')
    def filterPlayground(self, request):
        q = Conference.query()
        # simple filter usage:
        # q = q.filter(Conference.city == "Paris")

        # advanced filter building and usage
        #field = "city"
        #operator = "="
        #value = "London"
        #f = ndb.query.FilterNode(field, operator, value)
        #q = q.filter(f)
        #field = "topics"
        #operator = "="
        #value = "Medical Innovations"
        #f = ndb.query.FilterNode(field, operator, value)
        #q = q.filter(f)
        q = q.filter(Conference.city == "London")
        q = q.filter(Conference.topics == "Medical Innovations")
        q = q.order(Conference.name)
        q = q.filter(Conference.month == 12)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )

    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                path='queryConferences',
                http_method='POST',
                name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

         # return individual ConferenceForm object per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") \
            for conf in conferences]
        )


    ######################################
    # Profile
    ######################################

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail = user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
            prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
                path='profile',
                http_method='GET',
                name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
                path='profile',
                http_method='POST',
                name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)

    ######################################
    # Registration
    ######################################

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)

    ######################################
    # Announcement
    ######################################

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        announcement = memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY)
        if not announcement:
            announcement = ""
        return StringMessage(data=announcement)

    ######################################
    # Sessions
    ######################################

    def _copySessionToForm(self, sess):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(sess, field.name):
                # convert Date/Time/Speaker to date/time/speaker string; just copy others
                if field.name == "date" or field.name == "startTime" or field.name == "speaker":
                    setattr(sf, field.name, str(getattr(sess, field.name)))
                elif field.name == 'typeOfSession':
                    setattr(sf, field.name, getattr(SessionTypes, str(getattr(sess, field.name))))
                else:
                    setattr(sf, field.name, getattr(sess, field.name))
            elif field.name == "websafeKey":
                setattr(sf, field.name, sess.key.urlsafe())

        sf.check_initialized()
        return sf


    @endpoints.method(CONF_GET_REQUEST, SessionForms,
            path='conference/{websafeConferenceKey}/session',
            http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Given a conference (by websafeConferenceKey), return all sessions."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        conf_key = conf.key
        sessions = Session.query(ancestor=conf_key)

        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])


    @endpoints.method(SESSION_TYPE_GET_REQUEST, SessionForms,
            path='conference/{websafeConferenceKey}/session/type/{sessionType}',
            http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Given a conference (by websafeConferenceKey) and session type, return all sessions."""
        # get Conference object from request; bail if not found
        confKey = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not confKey:
            raise endpoints.NotFoundException('No conference found with key: %s' % request.websafeConferenceKey)
        if request.sessionType not in SessionTypes:
            raise endpoints.NotFoundException('There is no such session type: %s' % request.sessionType)
        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        sessions = Session.query(ancestor=conf_key)
        sessions = sessions.filter(Session.typeOfSession == request.sessionType)

        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

    @endpoints.method(SESSION_SPEAKER_GET_REQUEST, SessionForms,
            path='conference/{websafeConferenceKey}/sessions/speaker/{speaker}',
            http_method='GET', name='getConferenceSessionsBySpeaker')
    def getConferenceSessionsBySpeaker(self, request):
        """Given speaker, return all sessions."""
        speaker = ndb.Key(urlsafe=request.speaker).get()
        if not speaker:
            raise endpoints.NotFoundException(
                'No speaker found with key: %s' % request.speaker)
        sessions = Session.query(ancestor=ndb.Key(urlsafe=request.websafeConferenceKey))
        sessions = sessions.filter(Session.speaker == ndb.Key(urlsafe=request.speaker))

        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

    def _createSessionObject(self, request):
        """Create Session object, returning SessionForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        speaker = ndb.Key(urlsafe=request.speaker).get()
        if not speaker:
            raise endpoints.NotFoundException(
                'No speaker found with key: %s' % request.speaker)

        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        if not request.sessionName:
            raise endpoints.BadRequestException(
                "Session 'sessionName' field required")

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS_SESSION:
            if data[df] in (None, []):
                if df == "typeOfSession":
                    data[df] = SessionTypes.LECTURE
                    setattr(request, df, SessionTypes.LECTURE)
                else:
                    data[df] = DEFAULTS_SESSION[df]
                    setattr(request, df, DEFAULTS_SESSION[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['date']:
            data['date'] = datetime.datetime.strptime(data['date'][:10], "%Y-%m-%d").date()
        if data['date']:
            data['startTime'] = datetime.datetime.strptime(data['startTime'], '%H:%M').time()
        if data['typeOfSession']:
            data['typeOfSession'] = str(data['typeOfSession'])
        del data['websafeKey']
        del data['websafeConferenceKey']

        # Create Session key
        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        new_session_id = Session.allocate_ids(size=1, parent=conf_key)[0]
        session_key = ndb.Key(Session, new_session_id, parent=conf_key)
        data['key'] = session_key
        # Get Speaker key
        speaker_key = speaker.key
        data['speaker'] = speaker_key
        # Put session into datastore
        Session(**data).put()
        # Add created session to speakers sessions
        #speaker.speakersSessions.append(session_key)
        speaker.put()

        taskqueue.add(params={'speaker_key': request.speaker,
                              'conf_key': request.websafeConferenceKey
                              },
                      url='/tasks/set_featured_speaker',
                      method='GET'
                     )
        return self._copySessionToForm(request)


    @endpoints.method(SESSION_POST_REQUEST, SessionForm,
        path='conference/{websafeConferenceKey}/session',
        http_method='POST',
        name='createSession')
    def createSession(self, request):
        """ Create a new Session"""
        return self._createSessionObject(request)

    ######################################
    # Wishlist
    ######################################

    def _addToWishlist(self, request, add=True):
        """Add or delete session to user wishlist."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if session exists given sessionKey
        # get sessions; check that it exists
        wssk = request.sessionKey
        sess = ndb.Key(urlsafe=wssk).get()
        if not sess:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % wssk)

        # add
        if add:
            # check if user already added otherwise add
            if sess.key in prof.sessionsWishlist:
                raise ConflictException(
                    "You have already added this session to wishlist")

            # add session
            prof.sessionsWishlist.append(sess.key)
            retval = True

        # delete
        else:
            # check if session in users wishlist
            if sess.key in prof.sessionsWishlist:

                # delete session from wishlist
                prof.sessionsWishlist.remove(sess.key)
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        #sess.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, SessionForms,
            path='profile/wishlist',
            http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Get list of sessions that user has added to wishlist."""
        prof = self._getProfileFromUser() # get user Profile
        session_keys = prof.sessionsWishlist
        sessions = ndb.get_multi(session_keys)

        # return set of SessionForm objects per Session
        return SessionForms(items=[self._copySessionToForm(sess) for sess in sessions])

    @endpoints.method(SESSION_GET_REQUEST, BooleanMessage,
            path='profile/wishlist/{sessionKey}',
            http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Add session to users wishlist."""
        return self._addToWishlist(request)


    @endpoints.method(SESSION_GET_REQUEST, BooleanMessage,
            path='profile/wishlist/{sessionKey}',
            http_method='DELETE', name='deleteSessionInWishlist')
    def deleteSessionInWishlist(self, request):
        """Delete session from users wishlist."""
        return self._addToWishlist(request, add=False)

    ######################################
    # Queries and indexes
    ######################################

    @endpoints.method(message_types.VoidMessage, SessionForms,
                path='sessions/long',
                http_method='GET',
                name='queryLongSessions')
    def queryLongSessions(self, request):
        """Query for sessions with duration more than 60."""
        sessions = Session.query()
        sessions = sessions.filter(Session.duration > 60)

        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

    @endpoints.method(SESSION_GET_REQUEST, ConferenceForm,
            path='conference',
            http_method='GET',
            name='getConferenceBySession')
    def getConferenceBySession(self, request):
        """Given a Session key, return appropriate Conference."""
        # get Session object from request; bail if not found
        sess = ndb.Key(urlsafe=request.sessionKey).get()
        if not sess:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % request.sessionKey)
        conf = sess.key.parent().get()
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(message_types.VoidMessage, SessionForms,
                path='sessions/before7pmNotWorkshops',
                http_method='GET',
                name='queryBefore7pmNotWorkshops')
    def queryBefore7pmNotWorkshops(self, request):
        """Query for sessions with type other than Workshop and start time before 19:00:00."""
        sessions = Session.query()
        sessions = sessions.order(Session.startTime)
        sessions = sessions.filter(Session.typeOfSession.IN(["LECTURE", "KEYNOTE"]))
        sessions = sessions.filter(Session.startTime < datetime.time(hour=19))

        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

    ######################################
    # Speaker
    ######################################

    def _copySpeakerProfileToForm(self, speaker):
        """Copy relevant fields from Speaker to SpeakerForm."""
        # copy relevant fields from Speaker to SpeakerForm
        sf = SpeakerForm()
        for field in sf.all_fields():
            if hasattr(speaker, field.name):
                setattr(sf, field.name, getattr(speaker, field.name))
            elif field.name == "speakerKey":
                setattr(sf, field.name, speaker.key.urlsafe())
        sf.check_initialized()
        return sf

    @endpoints.method(SPEAKER_GET_REQUEST, SpeakerForm,
                path='speaker',
                http_method='GET',
                name='getSpeaker')
    def getSpeaker(self, request):
        """Return speaker profile by key."""
        speaker = ndb.Key(urlsafe=request.speakerKey).get()
        if not speaker:
            raise endpoints.NotFoundException(
                'No speaker found with key: %s' % request.speakerKey)
        return self._copySpeakerProfileToForm(speaker)

    @endpoints.method(SpeakerForm, SpeakerForm,
                path='speaker',
                http_method='POST',
                name='createSpeaker')
    def createSpeaker(self, request):
        """Create Speaker profile."""
        speaker = Speaker(displayName=request.displayName, mainEmail=request.mainEmail)
        # Now the speaker variable actually holds a speaker object instead of a dictionary.
        speaker.put()
        return self._copySpeakerProfileToForm(speaker)



    ######################################
    # Featured Speaker
    ######################################

    @staticmethod
    def _cacheFeaturedSpeaker(c_key, s_key):
        """
        Set Featured Speaker (speaker with more than one session) to memcache; used by featured speaker task.
        If there is no Featured Speaker clear memcache.
        """
        speaker_key = ndb.Key(urlsafe=s_key).get().key
        conf_key = ndb.Key(urlsafe=c_key).get().key

        sessionsWithCurrentSpeaker = Session.query(ancestor=conf_key). \
            filter(Session.speaker == speaker_key). \
            fetch(projection=[Session.sessionName])


        if (len(sessionsWithCurrentSpeaker) > 1):
            speakerName = ndb.Key(urlsafe=s_key).get().displayName
            featuredspeaker = '%s %s %s' % (
                speakerName,
                'is featured speaker with session:',
                ', '.join(session.sessionName for session in sessionsWithCurrentSpeaker))
            memcache.set(MEMCACHE_SPEAKER_KEY, featuredspeaker)
        else:
            # If there are no featured speakers,
            # delete the featured speaker memcache entry
            featuredspeaker = ""
            memcache.delete(MEMCACHE_SPEAKER_KEY)


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='sessions/featured_speakers',
            http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return featured speaker from memcache."""
        fspeaker = memcache.get(MEMCACHE_SPEAKER_KEY)
        if not fspeaker:
            fspeaker = ""
        return StringMessage(data=fspeaker)

# registers API
api = endpoints.api_server([ConferenceApi])