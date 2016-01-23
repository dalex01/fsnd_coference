App Engine application for the Udacity training course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions

You can visit already deployed application on [this link][8]

How to setup:

1. Download and install the [Google App Engine SDK for Python][4]. The App Engine SDK includes command-line tools and libraries for developing apps on your computer, testing them with a local server, and deploying them to App Engine.
2. Create or select a project in Google App Engine [Developer Console][5].
3. Update the value of `application` in `app.yaml` to the app ID you have registered in the App Engine admin console and would like to use to host your instance of this sample.
4. Update the values at the top of `settings.py` to reflect the respective client IDs you have registered in the [Developer Console][5].
5. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
6. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][6].)
7. Generate your client library(ies) with [the endpoints tool][7].
8. Deploy your application.

## Features implemented according to project requirements

### Session and Speaker entities implementation

To support Sessions and Speakers the following was implemented:

1. Models
  - `Session` model with properties:
    - `sessionName` - name of Session (string property)
    - `highlights` - abstract of Session (string property)
    - `speaker` - Speakers key in datastore (key property)
    - `duration` - duration of Session (integer property)
    - `typeOfSession` - type of Session (string property). Available types:
      - Lecture
      - Workshop
      - Keynote
    - `date` - date of Session (date property)
    - `startTime` - start time of Session (time property)
  - `Speaker` model with properties:
    - `displayName` - name of Speaker (string property)
    - `mainEmail` - email of Speaker (string property)
2. Messages
  - `SessionForm` - update Session form message
  - `SessionForms` - multiple Session outbound form message
  - `SpeakerForm` - Speaker outbound form message
3. Endpoints
  - `getConferenceSessions` - given a conference (by websafeConferenceKey), return all sessions
  - `getConferenceSessionsByType` - given a conference (by websafeConferenceKey) and session type, return all sessions
  - `getConferenceSessionsBySpeaker` - given speaker, return all sessions
  - `createSession` - create a new Session
  - `getSpeaker` - return speaker profile by key
  - `createSpeaker` - create Speaker profile
4. Helpers
  - `_copySessionToForm` - copy relevant fields from Session to SessionForm
  - `_createSessionObject` - create Session object, returning SessionForm/request
  - `_copySpeakerProfileToForm` - copy relevant fields from Speaker to SpeakerForm

Each Conference can contain several Sessions. Ancestor relationship was used to model this relation. Sessions unique key is created using Conference key. So particular Conference is a parent for particular set of Sessions.

Each Session has only one Speaker and one Speaker can conduct several Sessions in several Conferences. I decide to create separate Speaker entity instead of just speaker property in Sessions entity because I want to be able to store more information about Speaker than just his name. I store for example list of Sessions that Speaker conducted.

### Wishlist implementation

To implement users wishlist `sessionsWishlist` property was added to Profile entity. This property stores Sessions keys that were added to wishlist by user. Also was added the following endpoints:
- `getSessionsInWishlist` - get list of sessions that user has added to wishlist
- `addSessionToWishlist` - add session to users wishlist
- `deleteSessionInWishlist` - delete session from users wishlist

To get sessions from wishlist was used `ndb.get_multi` method. And to add or delete sessions to/from wishlist was implemented `_addToWishlist` helper function where appropriate users list is updated depending on what we want - add or delete appropriate session.

### Additional queries

The following additional queries were implemented:
- `queryLongSessions` - query for sessions with duration more than 60. This query is useful if user want to filter long sessions.
- `getConferenceBySession` - given a Session key, return appropriate Conference. This query is useful if user want to found conference if he know only Session.

For each additional query, appropriate endpoint was implemented.

Also `queryBefore7pmNotWorkshops` endpoint was implemented to filter non-Workshop sessions which starts before 19PM. As we can't filter with inequalities for different properties (in our case non-Workshop is `!=` inequality and before 7PM is `<` inequality) I used `IN` operator to filter only `Lecture` and `Keynote` types of sessions. Here is more info about it:
https://cloud.google.com/appengine/docs/python/datastore/queries.
I chose usage of `IN` operator, as it is the simplest way to build such query if types of Session are hardcoded.

Also I have found other method with `MultiInequalityMixin` class that in some cases can be implemented to handle queries with multiple inequality: see [Multiple Inequalities in Google AppEngine][9] article for more details.

### Featured Speaker feature implementation

Featured Speaker is a speaker with more than one session in one conference. It was implemented via App Engine's Task Queue. When session is created, appropriate speaker key and conference key is stored in task. Task handler start appropriate static method where I verify if speaker of added session has more than one session in provided conference. If so speaker name and his sessions in conference are stored in memcache. We can get featured speaker from memcache via `getFeaturedSpeaker` endpoint.

[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://cloud.google.com/appengine/downloads?hl=en_US&_ga=1.103483011.2009305435.1449866660#Google_App_Engine_SDK_for_Python
[5]: https://console.developers.google.com/
[6]: https://localhost:8080/
[7]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool
[8]: https://p4-conference.appspot.com/#/
[9]: http://nick.zoic.org/python/multiple-inequalities-in-google-appengine/