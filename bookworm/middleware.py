import logging
from django.conf import settings
from django.http import HttpResponsePermanentRedirect

log = logging.getLogger('middleware')

stanza_browsers = ('iphone', )

class Mobile(object):
    @staticmethod
    def process_request(request):
        if 'HTTP_HOST' in request.META and 'http://' + request.META['HTTP_HOST'] + '/' != settings.MOBILE_HOST and request.mobile == True:
            log.info("Redirecting to %s because hostname was %s" %  (settings.MOBILE_HOST, request.META['HTTP_HOST']))
            return HttpResponsePermanentRedirect(settings.MOBILE_HOST)

        if not request.mobile:
            return None
        
        if not hasattr(request, 'stanza_compatible') and request.META.has_key('HTTP_USER_AGENT'):
            log.debug('Checking %s for Stanza-compatibility' % request.META['HTTP_USER_AGENT']) 
            for b in stanza_browsers:
                if b in request.META["HTTP_USER_AGENT"].lower():
                    log.debug('Setting true for stanza-compatible browser')
                    request.stanza_compatible = True
                        